"""Tests that the LangGraph ToolNode executes concurrent tool_calls in parallel."""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from react_agent.graph.graph_builder import build_graph
from react_agent.graph.nodes import ParallelToolNode
from react_agent.graph.state import AgentState
from react_agent.graph.tools_exceptions import ToolRetryableError, ToolValidationError


class _FakeLLM:
    """Returns one AIMessage with three tool_calls, then a final answer."""

    def __init__(self):
        self.call_count = 0
        self._tools_runtime = None

    def bind_tools(self, tools):
        self._tools_runtime = tools
        return self

    def invoke(self, msgs):
        self.call_count += 1
        from langchain_core.messages import AIMessage
        if self.call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "slow_tool_a", "args": {}},
                    {"id": "c2", "name": "slow_tool_b", "args": {}},
                    {"id": "c3", "name": "slow_tool_c", "args": {}},
                ],
            )
        return AIMessage(content="done")


def _make_slow_tool(name: str, registry: dict):
    def _fn(state: dict = None) -> str:
        registry[name] = time.monotonic()
        time.sleep(0.2)
        registry[name + "_end"] = time.monotonic()
        return f"{name} result"

    return StructuredTool.from_function(
        func=_fn, name=name, description=f"{name} desc",
        args_schema=None,
    )


def test_three_tools_execute_in_parallel():
    starts = {}
    tools = [
        _make_slow_tool("slow_tool_a", starts),
        _make_slow_tool("slow_tool_b", starts),
        _make_slow_tool("slow_tool_c", starts),
    ]

    llm = _FakeLLM()
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tptest-")
    try:
        graph = build_graph(
            llm=llm, tools=tools, memory=None,
            tools_describe="", tool_names=[t.name for t in tools],
            max_iterations=2, executor=executor,
        )
        state_in: AgentState = {
            "messages": [HumanMessage(content="go")],
            "session_id": "t1", "user_id": "", "lang": "en",
            "max_iterations": 2, "iteration": 0,
            "text_mode": False, "scratchpad": "",
            "system_prompt": "", "system_prompt_extra": "",
            "progress_queue": None, "finalize_structured": False,
            "diagnostic_attachments": {},
        }
        t0 = time.monotonic()
        result = graph.invoke(state_in, {"recursion_limit": 30})
        elapsed = time.monotonic() - t0
    finally:
        executor.shutdown(wait=False)

    assert elapsed < 0.4, f"parallel run took {elapsed:.3f}s — should be ~0.2s"
    starts_a = starts.get("slow_tool_a")
    starts_b = starts.get("slow_tool_b")
    starts_c = starts.get("slow_tool_c")
    assert starts_a and starts_b and starts_c
    latest_start = max(starts_a, starts_b, starts_c)
    earliest_start = min(starts_a, starts_b, starts_c)
    assert (latest_start - earliest_start) < 0.1, (
        f"tool starts not concurrent: spread={latest_start-earliest_start:.3f}s"
    )


def _args_schema_with_state():
    return type(
        "Args",
        (BaseModel,),
        {"__annotations__": {"state": (Optional[dict], None)}},
    )


def _flaky_tool_factory(name: str, exc: Exception):
    def _fn(state: dict = None) -> str:
        raise exc

    return StructuredTool.from_function(
        func=_fn,
        name=name,
        description=f"{name} desc",
        args_schema=_args_schema_with_state(),
    )


def test_parallel_tool_retryable_error_propagates_flag():
    captured = {}

    class _TN:
        def invoke(self, _state):
            raise ToolRetryableError("network blip")

    node = ParallelToolNode(_TN(), executor=ThreadPoolExecutor(max_workers=2))
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tcid_x", "name": "flaky_tool", "args": {}},
            {"id": "tcid_w", "name": "flaky_tool", "args": {}},
        ],
    )
    state = {"messages": [HumanMessage(content="go"), last_ai]}

    out = node(state)
    by_id = {m.tool_call_id: m for m in out["messages"]}
    assert by_id["tcid_x"].content, f"missing ToolMessage: {out}"
    # Baseline (pre-0f8f9fa) does not propagate retryable flag.
    # ToolRetryableError is wrapped as a generic ToolError.
    assert "ToolRetryableError" in by_id["tcid_x"].content, (
        f"ToolRetryableError not surfaced: {by_id['tcid_x'].content}"
    )


def test_parallel_tool_validation_error_renders_retryable_false():
    class _TN:
        def invoke(self, _state):
            raise ToolValidationError("bad input")

    node = ParallelToolNode(_TN(), executor=ThreadPoolExecutor(max_workers=2))
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tcid_y", "name": "flaky_tool", "args": {}},
            {"id": "tcid_v", "name": "flaky_tool", "args": {}},
        ],
    )
    state = {"messages": [HumanMessage(content="go"), last_ai]}
    out = node(state)
    by_id = {m.tool_call_id: m for m in out["messages"]}
    assert "retryable=false" in by_id["tcid_y"].content, (
        f"retryable=false missing: {by_id['tcid_y'].content}"
    )


def test_parallel_tool_plain_exception_renders_retryable_false():
    class _TN:
        def invoke(self, _state):
            raise RuntimeError("boom")

    node = ParallelToolNode(_TN(), executor=ThreadPoolExecutor(max_workers=2))
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tcid_z", "name": "flaky_tool", "args": {}},
            {"id": "tcid_u", "name": "flaky_tool", "args": {}},
        ],
    )
    state = {"messages": [HumanMessage(content="go"), last_ai]}
    out = node(state)
    by_id = {m.tool_call_id: m for m in out["messages"]}
    assert "retryable=false" in by_id["tcid_z"].content, by_id["tcid_z"].content
