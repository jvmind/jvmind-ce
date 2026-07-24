"""LangGraph node functions and routing helpers (Stage 1)."""
from __future__ import annotations

from concurrent.futures import Executor
from typing import Any, Dict, List, Literal

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.prebuilt import ToolNode

from .state import MAX_HISTORY_MESSAGES

# P0-3 fix (2026-07-09): 移除 _set_session_id 调用 — thread-local 在 LangGraph
# ToolNode 的 worker 线程上不可见；改为从 graph state["session_id"] 取值。
# 各 node 函数现在只依赖 state 参数。


_REQUIRED_REACT_PLACEHOLDERS = ("{tool_names}", "{tool_descriptions}", "{memory_block}")


def _is_template(raw: str) -> bool:
    return all(p in (raw or "") for p in _REQUIRED_REACT_PLACEHOLDERS)


def _trim_history(msgs: List[BaseMessage]) -> List[BaseMessage]:
    """Keep the SystemMessage (if any at position 0) plus the latest
    ``MAX_HISTORY_MESSAGES`` non-system messages. This mirrors legacy
    ``history[-40:]`` behaviour, preventing per-call context from growing
    unboundedly across tool iterations."""
    if not msgs:
        return msgs
    head: List[BaseMessage] = []
    rest = list(msgs)
    if isinstance(rest[0], SystemMessage):
        head = [rest[0]]
        rest = rest[1:]
    if len(rest) <= MAX_HISTORY_MESSAGES:
        return msgs
    return head + rest[-MAX_HISTORY_MESSAGES:]


def _render_system_prompt(
    state: Dict[str, Any],
    tools_describe: str,
    tool_names: List[str],
    memory,
) -> str:
    """Return the system prompt to use.

    If ``state["system_prompt"]`` contains the ReAct placeholders, re-render
    it via ``build_system_prompt`` using the latest memory facts.
    Otherwise treat it as an already-rendered custom prompt and pass it
    through. In both branches the persisted conversation summary is
    appended (via
    :func:`react_agent.summarizer.inject_summary_into_prompt`) so the prompt
    that reaches ``_prepare_messages`` always carries it, even after
    re-rendering or per-iteration refresh.
    """
    from ..summarizer import inject_summary_into_prompt
    raw = state.get("system_prompt", "") or ""
    session_id = state.get("session_id", "")
    if raw and not _is_template(raw):
        # Custom prompt — still append the summary so cross-session memory
        # is preserved across iterations.
        return inject_summary_into_prompt(raw, session_id, memory)
    from ..prompts import build_system_prompt
    lang = state.get("lang", "")
    facts = memory.get_prompt_facts(session_id) if memory is not None else []
    rendered = build_system_prompt(
        tool_names=tool_names,
        tool_descriptions=tools_describe,
        facts=facts,
        template=raw or None,
        extra=state.get("system_prompt_extra", ""),
        lang=lang,
        function_calling=True,
    )
    # Re-inject the summary on every iteration: the fresh `build_system_prompt`
    # output strips anything `_build_initial_messages` may have appended, so
    # the prompt that flows into `_prepare_messages` (and ultimately the LLM)
    # always carries the persisted summary block.
    return inject_summary_into_prompt(rendered, session_id, memory)


def _prepare_messages(state: Dict[str, Any], system_prompt: str) -> List[BaseMessage]:
    history = state.get("messages", [])
    if history and isinstance(history[0], SystemMessage):
        msgs = list(history)
        msgs[0] = SystemMessage(content=system_prompt)
    else:
        msgs = [SystemMessage(content=system_prompt), *history]
    return _trim_history(msgs)


def agent_node(
    state: Dict[str, Any],
    *,
    llm,
    tools,
    memory,
    tools_describe: str,
    tool_names: List[str],
) -> Dict[str, Any]:
    system_prompt = _render_system_prompt(state, tools_describe, tool_names, memory)
    msgs = _prepare_messages(state, system_prompt)
    bound = llm.bind_tools(tools)
    response = bound.invoke(msgs)
    return {"messages": [response]}


def build_tool_node(tools) -> ToolNode:
    return ToolNode(tools, handle_tool_errors=True)


def tool_postprocess_node(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"iteration": state.get("iteration", 0) + 1}


def force_final_node(
    state: Dict[str, Any],
    *,
    llm,
    memory,
) -> Dict[str, Any]:
    history = state.get("messages", [])
    msgs = list(history)
    if msgs and isinstance(msgs[-1], AIMessage) and getattr(msgs[-1], "tool_calls", None):
        msgs[-1] = AIMessage(
            content=getattr(msgs[-1], "content", "") or "",
            id=getattr(msgs[-1], "id", None),
        )
    msgs.append(HumanMessage(content=(
        "You have reached the maximum number of tool iterations. "
        "Please provide a final answer to the user now using the information you have gathered so far. "
        "Do NOT call any more tools. Respond directly in the user's language."
    )))
    msgs = _trim_history(msgs)
    resp = llm.invoke(msgs)
    content = resp.content if hasattr(resp, "content") else str(resp)
    return {"messages": [AIMessage(content=str(content))]}


def should_continue(state: Dict[str, Any]) -> Literal["tool_node", "force_final", "end"]:
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    if tool_calls:
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 10)
        if iteration >= max_iter:
            return "force_final"
        return "tool_node"
    return "end"


class ParallelToolNode:
    """Run a ToolNode over multiple tool_calls concurrently using an Executor.

    LangGraph's built-in ToolNode runs tool_calls sequentially. When the
    graph state contains several tool_calls on one AIMessage, this wrapper
    fans them out to the executor and waits for all to complete, then
    returns the merged ToolMessage list in tool_call_id order.

    Falls back to sequential execution when ``executor`` is None.
    """

    def __init__(self, tool_node, executor: Executor):
        self._tool_node = tool_node
        self._executor = executor

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        tool_calls = list(getattr(last, "tool_calls", None) or [])
        if not tool_calls:
            return self._tool_node.invoke(state)
        if len(tool_calls) == 1:
            return self._tool_node.invoke(state)

        from langchain_core.messages import AIMessage

        results_by_id: Dict[str, Any] = {}
        futures = {}
        for tc in tool_calls:
            tcid = tc.get("id") or ""
            sub_state = {
                **state,
                "messages": [
                    *list(messages[:-1]),
                    AIMessage(
                        content=getattr(last, "content", "") or "",
                        tool_calls=[tc],
                        id=getattr(last, "id", None),
                    ),
                ],
                "__current_tool_call_id__": tcid,
            }
            fut = self._executor.submit(self._tool_node.invoke, sub_state)
            futures[tcid] = fut

        for tc in tool_calls:
            tcid = tc.get("id") or ""
            try:
                sub_result = futures[tcid].result(timeout=300)
            except Exception as e:
                from react_agent.graph.tools_exceptions import ToolValidationError
                from langchain_core.messages import ToolMessage
                sub_result = {
                    "messages": [
                        ToolMessage(
                            content=f"ToolError(retryable=false): {type(e).__name__}: {e}",
                            tool_call_id=tcid,
                            name=tc.get("name", ""),
                        )
                    ]
                }
            for m in sub_result.get("messages", []):
                if getattr(m, "tool_call_id", "") == tcid:
                    results_by_id[tcid] = m
                    break

        ordered = [results_by_id[tc.get("id") or ""] for tc in tool_calls if tc.get("id") in results_by_id]
        return {"messages": ordered}
