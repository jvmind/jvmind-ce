"""Regression tests for the system prompt rendering path.

After the legacy text-ReAct cleanup (0.1.7), ``build_system_prompt()`` no
longer accepts a ``function_calling`` kwarg. These tests pin that contract
so the LangGraph render path (the only production path) doesn't regress.
"""
from __future__ import annotations

import inspect


def test_build_system_prompt_signature_has_no_function_calling():
    """``build_system_prompt`` must not expose a ``function_calling`` param —
    the only execution mode is now function-calling."""
    from react_agent.prompts import build_system_prompt

    sig = inspect.signature(build_system_prompt)
    assert "function_calling" not in sig.parameters, (
        f"function_calling param leaked back into build_system_prompt: {sig}"
    )


def test_build_system_prompt_renders_tool_calling_mode_suffix():
    """The rendered system prompt must include the TOOL-CALLING MODE suffix
    so the model knows to use native tool calls, not the text protocol."""
    from react_agent.prompts import build_system_prompt

    rendered = build_system_prompt(
        tool_names=["foo"],
        tool_descriptions="- foo(x): does foo",
        facts=[],
        template="",
        extra="",
        lang="en",
    )
    assert "TOOL-CALLING MODE" in rendered
    assert "function/tool-call" in rendered
    # The suffix instructs the model to use the native mechanism — the
    # marker text below is part of the directive itself (telling the model
    # what NOT to write as plain text).
    assert "do NOT write 'Thought:'" in rendered


def test_render_system_prompt_does_not_pass_function_calling():
    """The live LangGraph render path (``nodes._render_system_prompt``) must
    not pass a removed ``function_calling`` kwarg — that was the regression
    in 0.1.7's release.
    """
    from react_agent.graph.nodes import _render_system_prompt

    class _FakeMem:
        def get_prompt_facts(self, _sid):
            return []

        def get_context_fact(self, _sid, _key):
            return ""

    state = {
        "lang": "en",
        "system_prompt": "",
        "system_prompt_extra": "",
        "session_id": "s1",
    }
    # Must not raise TypeError: got an unexpected keyword argument
    # 'function_calling'.
    rendered = _render_system_prompt(
        state,
        tools_describe="- foo(x): does foo",
        tool_names=["foo"],
        memory=_FakeMem(),
    )
    assert "TOOL-CALLING MODE" in rendered