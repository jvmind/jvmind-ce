"""Token-aware system-prompt budget assembly."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import tiktoken


ENCODINGS: Dict[str, str] = {
    "deepseek-chat": "cl100k_base",
    "deepseek-reasoner": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
}
DEFAULT_ENCODING = "cl100k_base"

MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
}
DEFAULT_CONTEXT_WINDOW = 32000

_ENCODING_CACHE: Dict[str, tiktoken.Encoding] = {}


def _get_encoding(model: str) -> tiktoken.Encoding:
    name = ENCODINGS.get(model, DEFAULT_ENCODING)
    enc = _ENCODING_CACHE.get(name)
    if enc is None:
        enc = tiktoken.get_encoding(name)
        _ENCODING_CACHE[name] = enc
    return enc


def compute_tokens(text: str, model: str) -> int:
    if not text:
        return 0
    return len(_get_encoding(model).encode(text))


def get_context_window(model: str) -> int:
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


def assemble_with_budget(
    *,
    system_prompt: str,
    facts_block: str,
    summary: str,
    history: List[Dict[str, Any]],
    model: str,
    context_window: int,
    reserve_tokens: int = 2000,
    keep_last_turns: int = 10,
) -> Dict[str, Any]:
    """Build the final messages list with token-budget enforcement.

    Order of system-message appends:
      1. system_prompt (always)
      2. facts_block (if budget allows)
      3. summary (if budget allows, prefixed with ``[context:summary]``)

    History is trimmed from the oldest non-last-``keep_last_turns`` turn
    while the running token total exceeds ``context_window - reserve_tokens``.
    """
    budget = max(0, context_window - reserve_tokens)
    sys_content = system_prompt or ""
    used = compute_tokens(sys_content, model)

    if facts_block:
        cost = compute_tokens(facts_block, model)
        if used + cost <= budget:
            sys_content = sys_content + "\n\n" + facts_block
            used += cost

    if summary:
        summary_block = "[context:summary]\n" + summary
        cost = compute_tokens(summary_block, model)
        if used + cost <= budget:
            sys_content = sys_content + "\n\n" + summary_block
            used += cost

    tail = list(history[-keep_last_turns:]) if keep_last_turns > 0 else []
    head = list(history[:-keep_last_turns]) if keep_last_turns > 0 else list(history)
    selected_head: List[Dict[str, Any]] = []
    for msg in reversed(head):
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        cost = compute_tokens(content, model)
        if used + cost > budget:
            break
        selected_head.insert(0, msg)
        used += cost

    messages = [{"role": "system", "content": sys_content}] + selected_head + tail
    return {"messages": messages, "tokens_used": used, "budget": budget}
