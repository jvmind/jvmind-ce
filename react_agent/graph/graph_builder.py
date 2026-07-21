"""Construct the compiled LangGraph StateGraph for the LangGraph agent."""
from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph, START, END
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

from .state import AgentState
from . import nodes


def build_graph(
    llm,
    tools: List[BaseTool],
    memory,
    tools_describe: str,
    tool_names: List[str],
    max_iterations: int = 10,
    executor: Optional[Executor] = None,
):
    """Build and compile a 2-node ReAct cycle with force_final guard.

    When ``executor`` is provided, the inner ``ToolNode`` runs concurrent
    tool_calls in parallel threads (used for parallel MAT calls and the
    like). When ``None``, tool calls run sequentially (LangGraph default).
    """
    workflow = StateGraph(AgentState)

    agent = partial(
        nodes.agent_node,
        llm=llm, tools=tools, memory=memory,
        tools_describe=tools_describe, tool_names=tool_names,
    )
    text_agent = partial(
        nodes.text_mode_agent_node,
        llm=llm, memory=memory,
        tools_describe=tools_describe, tool_names=tool_names,
    )
    force_final = partial(nodes.force_final_node, llm=llm, memory=memory)
    tool_node = ToolNode(tools, handle_tool_errors=True)

    class _SingleToolCallAdapter:
        """Inject ``__current_tool_call_id__`` for the single-tool path.

        LangGraph's built-in ``ToolNode`` does not propagate tool_call_id
        into the per-tool ``state`` argument; tools that want progress
        events tagged with their own tcid rely on this key. Adapter only
        acts when there is exactly one tool call and an id is present.
        """

        def __init__(self, inner):
            self._inner = inner

        def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
            return self(state)

        def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
            msgs = state.get("messages") or []
            if msgs:
                last = msgs[-1]
                tcs = getattr(last, "tool_calls", None) or []
                if len(tcs) == 1 and (tcs[0].get("id") or ""):
                    state = {**state, "__current_tool_call_id__": tcs[0]["id"]}
            return self._inner.invoke(state)

    tool_node_single = _SingleToolCallAdapter(tool_node)
    tools_node = (
        nodes.ParallelToolNode(tool_node_single, executor=executor)
        if executor is not None
        else tool_node_single
    )

    def entry_route(state: Dict[str, Any]) -> str:
        return "text_agent" if state.get("text_mode") else "agent"

    def after_tools(state: Dict[str, Any]) -> str:
        return "text_agent" if state.get("text_mode") else "agent"

    workflow.add_node("agent", agent)
    workflow.add_node("text_agent", text_agent)
    workflow.add_node("tools", tools_node)
    workflow.add_node("post_tools", nodes.tool_postprocess_node)
    workflow.add_node("force_final", force_final)

    workflow.add_conditional_edges(START, entry_route, {"agent": "agent", "text_agent": "text_agent"})
    workflow.add_conditional_edges(
        "agent", nodes.should_continue,
        {"tool_node": "tools", "force_final": "force_final", "end": END},
    )
    workflow.add_conditional_edges(
        "text_agent", nodes.should_continue,
        {"tool_node": "tools", "force_final": "force_final", "end": END},
    )
    workflow.add_edge("tools", "post_tools")
    workflow.add_conditional_edges("post_tools", after_tools, {"agent": "agent", "text_agent": "text_agent"})
    workflow.add_edge("force_final", END)

    return workflow.compile()
