"""Conversation summarization.

Once a session accumulates more than ``threshold`` messages, the older
``messages[:-keep_last]`` window is compressed into a single short summary
and stored as a context fact under the key ``[context:summary]``. The next
request reads that fact back and prepends it to the system prompt.

Failures (DB or LLM) are swallowed: summarization is best-effort and must
never break the user-facing chat path.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_SUMMARY_KEY = "summary"
_DEFAULT_THRESHOLD = 40
_DEFAULT_KEEP_LAST = 10
_DEFAULT_MAX_CHARS = 1500

_SUMMARIZE_SYSTEM = (
    "你是一个对话摘要助手。请将下方对话历史压缩成简短中文摘要。"
    "保留：用户的关键问题、agent 给出的最终结论、报告 ID（如 gc_/jstack_/hd_ 开头）、"
    "remember 工具写入的事实。忽略闲聊和逐步推理细节。"
    "直接输出摘要，不要任何前缀、解释或 Markdown 标记。"
)

_SUMMARIZE_USER_TEMPLATE = (
    "请将以下对话历史压缩成不超过 1500 字的摘要：\n\n"
    "{transcript}\n\n"
    "摘要："
)


def _format_transcript(messages: List[Dict[str, str]]) -> str:
    return "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in messages
        if m.get("content")
    )


def maybe_summarize(
    session_id: str,
    memory: Any,
    llm_caller: Callable[[List[Dict[str, str]]], str],
    *,
    threshold: int = _DEFAULT_THRESHOLD,
    keep_last: int = _DEFAULT_KEEP_LAST,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> bool:
    try:
        messages = memory.get_messages(session_id) or []
    except Exception:
        logger.exception("maybe_summarize: get_messages failed")
        return False
    if len(messages) <= threshold:
        return False
    to_summarize = messages[:-keep_last] if keep_last > 0 else messages
    transcript = _format_transcript(to_summarize)
    if not transcript.strip():
        return False
    try:
        text = llm_caller([
            {"role": "system", "content": _SUMMARIZE_SYSTEM},
            {"role": "user", "content": _SUMMARIZE_USER_TEMPLATE.format(transcript=transcript)},
        ])
    except Exception:
        logger.exception("maybe_summarize: llm call failed")
        return False
    if not text:
        return False
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars - 3].rstrip() + "..."
    try:
        memory.set_context_fact(session_id, _SUMMARY_KEY, text)
    except Exception:
        logger.exception("maybe_summarize: set_context_fact failed")
        return False
    return True


def inject_summary_into_prompt(
    system_prompt: Any,
    session_id: str,
    memory: Any,
) -> Any:
    """Append the persisted conversation summary to a system prompt string.

    Centralises the ``"\n\n[context:summary]\n{summary}"``
    suffix used by both the legacy ``ReActAgent.run_stream`` injection site
    (and its function-calling fallback rebuild) and the LangGraph
    ``SSEAdapter._build_initial_messages`` / ``nodes._render_system_prompt``
    sites. Keeping a single source of truth for the format means a future
    change to the prefix or surrounding whitespace only has to be made here.

    Contract:
      - Returns ``system_prompt`` unchanged when there is no summary, the
        memory backend predates ``get_context_fact``, the session id is
        empty, or the input is not a string.
      - Never raises; exceptions are swallowed so a degraded memory backend
        cannot break chat. (Mirrors ``maybe_summarize``'s best-effort
        posture.)
      - The summary is treated as opaque text; callers concatenate it onto
        their own prompt strings.
    """
    if not isinstance(system_prompt, str) or not session_id:
        return system_prompt
    try:
        get_ctx = getattr(memory, "get_context_fact", None)
        if not get_ctx:
            return system_prompt
        summary = get_ctx(session_id, _SUMMARY_KEY) or ""
        if summary:
            return system_prompt + f"\n\n[context:summary]\n{summary}"
    except Exception:
        logger.exception("inject_summary_into_prompt: lookup failed")
    return system_prompt
