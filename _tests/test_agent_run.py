"""Unit coverage for the non-streaming ReActAgent.run() path.

run() was largely uncovered (the streaming run_stream path dominates existing
tests). These exercise both execution routes without any network:
  - native function-calling via _run_tools (mocked _chat_stream_tools)
  - the text-ReAct fallback via _run_stream... no: run() uses _chat + parsing
  - tool dispatch + remember side effect
  - max-iteration forced finish

We inject fakes for _chat / _chat_stream_tools at the instance level and use a
tiny in-memory store implementing the methods run() touches.
"""
from __future__ import annotations

import pytest

from react_agent.agent import ReActAgent, AgentStep
from react_agent.tools import default_tools


class _Mem:
    """Minimal memory backend implementing what run() touches."""

    def __init__(self):
        self.messages = []   # list of (role, content)
        self.facts = []
        self.context = {}

    def append_message(self, sid, role, content):
        self.messages.append((role, content))

    def get_messages(self, sid):
        return [{"role": r, "content": c} for r, c in self.messages]

    def get_facts(self, sid):
        return list(self.facts)

    def get_prompt_facts(self, sid):
        return list(self.facts)

    def add_fact(self, sid, fact):
        self.facts.append(fact)

    def set_context_fact(self, sid, key, value):
        self.context[key] = value


def _make_agent(mem=None, fc=True):
    agent = ReActAgent(api_key="x", base_url="https://fake.local/v1", model="m")
    agent.memory = mem or _Mem()
    # Force the function-calling decision deterministically.
    agent._fc_unsupported = not fc
    return agent


# ---------- function-calling path (run -> _run_tools) ----------

def test_run_fc_direct_answer(monkeypatch):
    """Model returns content and no tool calls -> that content is the answer."""
    agent = _make_agent(fc=True)

    def fake_stream_tools(self, convo, tools):
        yield {"kind": "final", "text": "Throughput is 98%."}
        yield {"kind": "finish", "content": "Throughput is 98%."}

    monkeypatch.setattr(ReActAgent, "_chat_stream_tools", fake_stream_tools)
    answer, steps = agent.run("s1", "How is throughput?")
    assert answer == "Throughput is 98%."
    # user + assistant persisted
    assert agent.memory.messages[0] == ("user", "How is throughput?")
    assert agent.memory.messages[-1] == ("assistant", "Throughput is 98%.")


def test_run_fc_executes_tool_then_answers(monkeypatch):
    """First turn requests a tool call, second turn answers."""
    agent = _make_agent(fc=True)
    turns = [
        [  # turn 1: a tool call
            {"kind": "tool_calls", "calls": [{"id": "c1", "name": "current_time", "arguments": "{}"}]},
            {"kind": "finish", "content": ""},
        ],
        [  # turn 2: final answer
            {"kind": "final", "text": "Done."},
            {"kind": "finish", "content": "Done."},
        ],
    ]
    seq = iter(turns)

    def fake_stream_tools(self, convo, tools):
        for ev in next(seq):
            yield ev

    monkeypatch.setattr(ReActAgent, "_chat_stream_tools", fake_stream_tools)
    answer, steps = agent.run("s1", "what time is it then summarize")
    assert answer == "Done."
    # One tool step recorded with an observation from current_time
    tool_steps = [s for s in steps if s.action == "current_time"]
    assert len(tool_steps) == 1
    assert tool_steps[0].observation  # current_time returns a non-empty string


def test_run_fc_remember_persists_fact(monkeypatch):
    agent = _make_agent(fc=True)
    turns = [
        [
            {"kind": "tool_calls", "calls": [{"id": "c1", "name": "remember", "arguments": '{"fact": "user likes G1GC"}'}]},
            {"kind": "finish", "content": ""},
        ],
        [
            {"kind": "final", "text": "Noted."},
            {"kind": "finish", "content": "Noted."},
        ],
    ]
    seq = iter(turns)
    monkeypatch.setattr(ReActAgent, "_chat_stream_tools", lambda self, c, t: iter(next(seq)))
    answer, steps = agent.run("s1", "remember I like G1GC")
    assert answer == "Noted."
    assert "user likes G1GC" in agent.memory.facts


def test_run_fc_max_iterations_forces_finish(monkeypatch):
    """If the model keeps calling tools, run() force-finishes via _chat."""
    agent = _make_agent(fc=True)
    agent.max_iterations = 2

    def always_tool(self, convo, tools):
        yield {"kind": "tool_calls", "calls": [{"id": "c", "name": "current_time", "arguments": "{}"}]}
        yield {"kind": "finish", "content": ""}

    monkeypatch.setattr(ReActAgent, "_chat_stream_tools", always_tool)
    monkeypatch.setattr(ReActAgent, "_chat", lambda self, msgs, stop=None: "Final summary after limit.")
    answer, steps = agent.run("s1", "loop forever")
    assert answer == "Final summary after limit."
    assert agent.memory.messages[-1] == ("assistant", "Final summary after limit.")


# ---------- text ReAct path (run with _fc_unsupported) ----------

def test_run_text_direct_final_answer(monkeypatch):
    agent = _make_agent(fc=False)

    def fake_chat(self, messages, stop=None):
        return "Thought: I can answer directly\nFinal Answer: 42"

    monkeypatch.setattr(ReActAgent, "_chat", fake_chat)
    answer, steps = agent.run("s1", "what is the answer")
    assert answer == "42"
    assert agent.memory.messages[-1] == ("assistant", "42")


def test_run_text_tool_then_final(monkeypatch):
    agent = _make_agent(fc=False)
    replies = iter([
        "Thought: need the time\nAction: current_time\nAction Input: ",
        "Thought: got it\nFinal Answer: The time is shown above.",
    ])
    monkeypatch.setattr(ReActAgent, "_chat", lambda self, m, stop=None: next(replies))
    answer, steps = agent.run("s1", "tell me the time")
    assert answer == "The time is shown above."
    assert any(s.action == "current_time" for s in steps)


def test_run_text_no_action_no_final_repairs(monkeypatch):
    """A malformed reply with neither Action nor Final Answer triggers repair."""
    agent = _make_agent(fc=False)
    replies = iter([
        "I am just rambling without structure.",   # first parse: no action/final
        "Thought: ok\nFinal Answer: Repaired answer.",  # _repair_final_answer
    ])
    monkeypatch.setattr(ReActAgent, "_chat", lambda self, m, stop=None: next(replies))
    answer, steps = agent.run("s1", "hello")
    assert answer == "Repaired answer."


# =========================================================================
# Task 4 (legacy): summary injection into run_stream system prompt
# =========================================================================

class _MemWithSummary(_Mem):
    """Memory stub that returns a controllable context:summary fact."""

    def __init__(self, summary: str = ""):
        super().__init__()
        self._summary = summary

    def get_context_fact(self, sid, key):
        return self._summary if key == "summary" else ""


def _stub_run_stream_tools(captured: dict):
    """Return a fake _run_stream_tools that records the system_prompt it received."""

    def fake(self, session_id, system_prompt, history, should_stop=None):
        captured["system_prompt"] = system_prompt
        yield {"type": "final", "content": "ok"}
        yield {"type": "done"}

    return fake


def test_run_stream_injects_summary_block_into_system_prompt(monkeypatch):
    """When memory.get_context_fact('summary') returns a value, run_stream must
    append a '[context:summary]' block to the system prompt that flows into
    _run_stream_tools."""
    mem = _MemWithSummary(summary="earlier user asked about gc_abc yesterday")
    agent = _make_agent(mem=mem, fc=True)
    captured: dict = {}
    monkeypatch.setattr(ReActAgent, "_run_stream_tools", _stub_run_stream_tools(captured))

    list(agent.run_stream("s1", "what about that gc log?"))

    sp = captured["system_prompt"]
    assert "[context:summary]" in sp, sp[-300:]
    assert "earlier user asked about gc_abc yesterday" in sp, sp[-300:]
    # trailing-block shape: appended after a blank line
    assert "\n\n[context:summary]\nearlier user asked about gc_abc yesterday" in sp, sp[-300:]


def test_run_stream_omits_summary_block_when_no_summary(monkeypatch):
    """When memory has no summary, run_stream must NOT append the summary block."""
    mem = _MemWithSummary(summary="")
    agent = _make_agent(mem=mem, fc=True)
    captured: dict = {}
    monkeypatch.setattr(ReActAgent, "_run_stream_tools", _stub_run_stream_tools(captured))

    list(agent.run_stream("s1", "hi"))

    assert "[context:summary]" not in captured["system_prompt"]


def test_run_stream_tolerates_memory_without_get_context_fact(monkeypatch):
    """If the memory backend predates get_context_fact, run_stream must not crash."""
    mem = _Mem()  # no get_context_fact
    assert not hasattr(mem, "get_context_fact")
    agent = _make_agent(mem=mem, fc=True)
    captured: dict = {}
    monkeypatch.setattr(ReActAgent, "_run_stream_tools", _stub_run_stream_tools(captured))

    list(agent.run_stream("s1", "hi"))

    assert "[context:summary]" not in captured["system_prompt"]


# =========================================================================
# Task 6 (legacy): trigger maybe_summarize after the `done` event
# =========================================================================

def test_run_stream_summary_trigger_after_done(monkeypatch):
    """After run_stream yields `done`, the legacy loop must invoke
    `maybe_summarize(session_id, self.memory, lambda msgs: self._chat(msgs))`
    exactly once, with a non-empty messages list.

    Mirrors the Task 5 facade trigger contract on the legacy path. The summary
    callable is `lambda msgs: self._chat(msgs)` because the legacy ReActAgent
    already mixes in `_LLMMixin`, so `_chat` is directly callable.
    """
    mem = _MemWithSummary(summary="")
    agent = _make_agent(mem=mem, fc=True)

    # Stub _run_stream_tools to emit a single `done` so run_stream exits cleanly.
    def fake_run_stream_tools(self, session_id, system_prompt, history, should_stop=None):
        yield {"type": "done"}

    monkeypatch.setattr(ReActAgent, "_run_stream_tools", fake_run_stream_tools)

    # Record every _chat call (the new trigger uses `lambda msgs: self._chat(msgs)`
    # as the summary LLM; during the streaming agent loop `_chat` is not called,
    # so the recorder only observes the summary invocation).
    summary_calls: list = []

    def recording_chat(self, messages, stop=None):
        summary_calls.append(messages)
        return "summary text"

    monkeypatch.setattr(ReActAgent, "_chat", recording_chat)

    # Seed >=41 messages so maybe_summarize crosses its default threshold (40).
    for i in range(41):
        role = "user" if i % 2 == 0 else "assistant"
        agent.memory.append_message("s1", role, f"m{i}")

    # Drive run_stream to completion (the inner stub yields one event and exits,
    # so the outer try/finally reaches its terminator naturally).
    list(agent.run_stream("s1", "hi"))

    # The summary LLM callable must have been invoked exactly once with a
    # non-empty messages list.
    assert len(summary_calls) == 1, (
        f"expected exactly 1 summary call after done, got {len(summary_calls)}"
    )
    assert len(summary_calls[0]) > 0, "summary call must receive a non-empty messages list"
    # Sanity: the messages list matches the summarizer's [system, user] shape.
    roles = [m["role"] for m in summary_calls[0]]
    assert roles[0] == "system" and roles[1] == "user", roles
