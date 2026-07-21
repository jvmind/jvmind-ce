"""Tests for tiktoken-based token budget assembler."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from react_agent.memory.token_budget import (
    compute_tokens,
    assemble_with_budget,
    MODEL_CONTEXT_WINDOWS,
)


def test_compute_tokens_uses_cl100k_for_deepseek():
    n = compute_tokens("hello world", "deepseek-chat")
    assert n > 0


def test_compute_tokens_unknown_model_falls_back():
    n = compute_tokens("hello", "model-that-does-not-exist")
    assert n > 0


def test_assemble_with_budget_keeps_last_turns():
    sys_prompt = "You are a JVM assistant."
    facts = "fact1\nfact2\n"
    summary = "Earlier conversation summarised."
    history = [
        {"role": "user", "content": f"old message {i}"}
        for i in range(50)
    ]
    history.append({"role": "user", "content": "the latest user message"})
    history.append({"role": "assistant", "content": "the latest assistant reply"})

    out = assemble_with_budget(
        system_prompt=sys_prompt,
        facts_block=facts,
        summary=summary,
        history=history,
        model="deepseek-chat",
        context_window=130,
        reserve_tokens=101,
        keep_last_turns=10,
    )

    assert out["budget"] > 0
    assert out["tokens_used"] <= out["budget"]
    msgs = out["messages"]
    assert msgs[0]["role"] == "system"
    assert "JVM assistant" in msgs[0]["content"]
    last = msgs[-1]
    assert last["content"] == "the latest assistant reply"
    assert len(msgs) <= 1 + 10 + 1


def test_assemble_with_budget_drops_summary_when_over():
    sys_prompt = "short"
    facts = ""
    summary = "X" * 100_000
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    out = assemble_with_budget(
        system_prompt=sys_prompt,
        facts_block=facts,
        summary=summary,
        history=history,
        model="deepseek-chat",
        context_window=2048,
        reserve_tokens=100,
        keep_last_turns=2,
    )
    msgs = out["messages"]
    assert "[context:summary]" not in msgs[0]["content"]
