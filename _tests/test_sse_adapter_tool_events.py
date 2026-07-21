"""Tests for tool_start / tool_end SSE events emitted by SSEAdapter."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from react_agent.graph.sse_adapter import SSEAdapter


class _FakeMemory:
    def __init__(self):
        self._messages = {}
        self._context = {}

    def append_message(self, sid, role, content):
        self._messages.setdefault(sid, []).append({"role": role, "content": content})
        return 1

    def get_messages(self, sid):
        return list(self._messages.get(sid, []))

    def get_facts(self, sid):
        return []

    def get_prompt_facts(self, sid):
        return []

    def set_context_fact(self, sid, key, value):
        self._context.setdefault(sid, {})[key] = value

    def get_context_fact(self, sid, key):
        return self._context.get(sid, {}).get(key, "")


class _OneShotGraph:
    """Yields a single tool call chunk + ToolMessage pair."""
    def __init__(self):
        self.yielded = False

    def stream(self, state_in, config=None, stream_mode="messages"):
        # Tool call streamed in two chunks (name first, args second).
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "tc1", "name": "echo", "args": "", "index": 0}],
        )
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "tc1", "name": "", "args": '{"input":"hi"}', "index": 0}],
        )
        yield (chunk1, {})
        yield (chunk2, {})
        yield (ToolMessage(content="echo-result", tool_call_id="tc1"), {})
        # terminator
        yield (AIMessage(content="done"), {})


def test_tool_start_emitted_once_per_call_id():
    mem = _FakeMemory()
    adapter = SSEAdapter(_OneShotGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    tool_starts = [e for e in events if e["type"] == "tool_start"]
    assert len(tool_starts) == 1, f"want 1 tool_start, got {len(tool_starts)}: {tool_starts}"
    payload = tool_starts[0]
    assert payload["tool_call_id"] == "tc1"
    assert payload["name"] == "echo"
    # `args` MAY be partial at tool_start time (design intent: see spec §2.2).
    # Some providers stream `name` in one chunk and `args` in another, so when
    # chunk1 fires `tool_start`, args_buf may still be "". Either is acceptable;
    # full args are guaranteed to be present in the corresponding `tool_end`.
    assert payload["args"] in ('{"input":"hi"}', ""), f"unexpected args: {payload['args']!r}"


def test_tool_end_emitted_before_step_with_status():
    mem = _FakeMemory()
    adapter = SSEAdapter(_OneShotGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    tool_ends = [e for e in events if e["type"] == "tool_end"]
    steps = [e for e in events if e["type"] == "step"]
    assert len(tool_ends) == 1, f"want 1 tool_end, got {len(tool_ends)}: {tool_ends}"
    assert len(steps) == 1

    # tool_end MUST come before step
    te_idx = events.index(tool_ends[0])
    st_idx = events.index(steps[0])
    assert te_idx < st_idx, f"tool_end@{te_idx} must precede step@{st_idx}"

    payload = tool_ends[0]
    assert payload["tool_call_id"] == "tc1"
    assert payload["name"] == "echo"
    assert payload["args"] == '{"input":"hi"}'
    assert payload["observation"] == "echo-result"
    assert payload["status"] == "ok"

    # Legacy step event still has the same shape; action_input is the
    # extracted `tool_input` (legacy byte-compat), not the raw JSON args_buf.
    assert steps[0]["step"]["action"] == "echo"
    assert steps[0]["step"]["action_input"] == "hi"
    assert steps[0]["step"]["observation"] == "echo-result"


class _ErrorToolGraph:
    def stream(self, state_in, config=None, stream_mode="messages"):
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"id": "tc1", "name": "bad", "args": '{"input":"x"}', "index": 0}],
            ),
            {},
        )
        yield (
            ToolMessage(content="[Tool Error] ValueError: kaboom", tool_call_id="tc1"),
            {}
        )
        yield (AIMessage(content="done"), {})


def test_tool_end_status_error_when_observation_is_tool_error():
    mem = _FakeMemory()
    adapter = SSEAdapter(_ErrorToolGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    tool_ends = [e for e in events if e["type"] == "tool_end"]
    assert tool_ends, "expected tool_end"
    assert tool_ends[0]["status"] == "error"
    assert tool_ends[0]["observation"].startswith("[Tool Error]")


class _ParallelToolGraph:
    def stream(self, state_in, config=None, stream_mode="messages"):
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"id": "tc1", "name": "echo", "args": '{"input":"a"}', "index": 0},
                    {"id": "tc2", "name": "echo2", "args": '{"input":"b"}', "index": 1},
                ],
            ),
            {}
        )
        # Order may differ from declaration order — ToolNode parallelises.
        yield (ToolMessage(content="r1", tool_call_id="tc2"), {})
        yield (ToolMessage(content="r2", tool_call_id="tc1"), {})
        yield (AIMessage(content="done"), {})


def test_parallel_tool_calls_emit_one_start_end_each():
    mem = _FakeMemory()
    adapter = SSEAdapter(_ParallelToolGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    steps = [e for e in events if e["type"] == "step"]

    assert len(starts) == 2
    assert len(ends) == 2
    assert len(steps) == 2

    start_ids = {e["tool_call_id"] for e in starts}
    end_ids = {e["tool_call_id"] for e in ends}
    assert start_ids == end_ids == {"tc1", "tc2"}

    # each tool_end MUST precede its matching tool's step event
    for end in ends:
        end_idx = events.index(end)
        matching_steps = [
            e for e in steps
            if e["step"]["tool_call_id"] == end["tool_call_id"]
        ]
        assert matching_steps, f"no step for {end['tool_call_id']}"
        step_idx = events.index(matching_steps[0])
        assert end_idx < step_idx


class _ContinuationChunkGraph:
    """Provider sends name in the first chunk (with id), then args in a
    continuation chunk that has index but no id. Index-based lookup must
    route the continuation to the same tool_call_id."""

    def stream(self, state_in, config=None, stream_mode="messages"):
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"id": "tc1", "name": "echo", "args": "", "index": 0}],
            ),
            {},
        )
        # Continuation: same index, no id, only args.
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"args": '{"input":"hel', "index": 0}],
            ),
            {},
        )
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"args": 'lo"}', "index": 0}],
            ),
            {},
        )
        yield (ToolMessage(content="echo-result", tool_call_id="tc1"), {})
        yield (AIMessage(content="done"), {})


def test_id_less_continuation_chunks_route_by_index():
    mem = _FakeMemory()
    adapter = SSEAdapter(_ContinuationChunkGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    assert len(starts) == 1
    assert len(ends) == 1
    # Full args_buf is reconstructed across continuation chunks.
    assert starts[0]["tool_call_id"] == "tc1"
    assert ends[0]["args"] == '{"input":"hello"}'
    # tool_end MUST come before the corresponding step event
    assert events.index(ends[0]) < events.index(
        next(e for e in events if e["type"] == "step")
    )


class _ToolErrorMessageGraph:
    """ToolMessage.content starts with 'ToolError(...)' — emitted by some
    parallel tool paths. Must trigger status='error'."""

    def stream(self, state_in, config=None, stream_mode="messages"):
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"id": "tc1", "name": "bad", "args": '{"input":"x"}', "index": 0}],
            ),
            {},
        )
        yield (
            ToolMessage(content="ToolError(ValueError): kaboom", tool_call_id="tc1"),
            {}
        )
        yield (AIMessage(content="done"), {})


def test_tool_end_status_error_when_observation_starts_with_tool_error():
    mem = _FakeMemory()
    adapter = SSEAdapter(_ToolErrorMessageGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends
    assert ends[0]["status"] == "error"


class _LangchainStatusErrorGraph:
    """ToolMessage.status='error' (langchain built-in flag) must also
    trigger status='error', regardless of observation text content."""

    def stream(self, state_in, config=None, stream_mode="messages"):
        yield (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"id": "tc1", "name": "bad", "args": '{"input":"x"}', "index": 0}],
            ),
            {},
        )
        yield (
            ToolMessage(content="some failure", tool_call_id="tc1", status="error"),
            {}
        )
        yield (AIMessage(content="done"), {})


def test_tool_end_status_error_when_tool_message_status_is_error():
    mem = _FakeMemory()
    adapter = SSEAdapter(_LangchainStatusErrorGraph(), mem)
    events = list(adapter.run_stream("sid", "hi", system_prompt="sys"))

    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends
    assert ends[0]["status"] == "error"
