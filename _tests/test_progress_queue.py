"""Tests for the per-session progress queue and emitter."""
from __future__ import annotations

import os
import queue
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from langchain_core.messages import AIMessage, HumanMessage

from react_agent.graph.progress import (
    ProgressEvent,
    _ProgressEmitter,
    get_queue,
)


def _state_with_queue():
    q: queue.Queue = queue.Queue(maxsize=10)
    return {"progress_queue": q}, q


def test_emitter_pushes_start_and_end():
    state, q = _state_with_queue()
    with _ProgressEmitter(state, "tcid1", "fake_tool", "starting") as em:
        em.update(50, "halfway")
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    phases = [(e.phase, e.pct) for e in events]
    assert phases == [("start", 0), ("poll", 50), ("end", 100)]
    assert events[0].tool == "fake_tool"
    assert events[0].tool_call_id == "tcid1"
    assert events[2].msg == ""


def test_emitter_swallows_queue_full():
    state, q = _state_with_queue()
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    q.put_nowait(ProgressEvent("x", "y", "start", 0, ""))
    with _ProgressEmitter(state, "tcid", "t", "") as em:
        em.update(50, "")
    assert q.full()


def test_get_queue_returns_none_when_absent():
    assert get_queue({}) is None
    state, q = _state_with_queue()
    assert get_queue(state) is q


def test_sse_adapter_yields_step_progress_events():
    from react_agent.graph.sse_adapter import SSEAdapter

    class _StubMemory:
        def __init__(self):
            self._saved = None

        def append_message(self, sid, role, content):
            self._saved = (sid, role, content)
            return 1

    class _StubGraph:
        def stream(self, state_in, config, stream_mode):
            q = state_in["progress_queue"]
            q.put_nowait(ProgressEvent("fake", "tcid", "start", 0, ""))
            for pct in (25, 75, 100):
                time.sleep(0.01)
                q.put_nowait(ProgressEvent("fake", "tcid", "poll", pct, ""))
            yield (AIMessage(content="done"), {"langgraph_node": "agent"})

    adapter = SSEAdapter(_StubGraph(), _StubMemory(), stream_mode="messages")
    events = list(adapter.run_stream(
        session_id="sid",
        user_input="hi",
        initial_messages=[HumanMessage(content="hi")],
        max_iterations=2,
        should_stop=lambda: False,
    ))

    progress_events = [e for e in events if e.get("type") == "step.progress"]
    assert progress_events, "expected step.progress events in SSE stream"
    pcts = [e["pct"] for e in progress_events]
    assert max(pcts) >= 75, f"expected high pct in stream, got {pcts}"