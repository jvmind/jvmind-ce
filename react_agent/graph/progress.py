"""Per-session progress queue + emitter for long-running tools."""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class ProgressEvent:
    tool: str
    tool_call_id: str
    phase: Literal["start", "poll", "end"]
    pct: int
    msg: str = ""


def get_queue(state: dict) -> Optional["queue.Queue[ProgressEvent]"]:
    q = state.get("progress_queue") if isinstance(state, dict) else None
    return q if isinstance(q, queue.Queue) else None


def _safe_put(q: Optional["queue.Queue"], event: ProgressEvent) -> None:
    if q is None:
        return
    try:
        q.put_nowait(event)
    except queue.Full:
        pass


class _ProgressEmitter:
    """Context manager wrapping a tool body; pushes start/end events.

    Use ``em.update(pct, msg)`` inside the body to push incremental poll
    events (e.g. during long-polling loops).
    """

    def __init__(self, state: dict, tool_call_id: str, tool: str, start_msg: str):
        self._state = state
        self._tcid = tool_call_id
        self._tool = tool
        self._start_msg = start_msg

    def __enter__(self):
        _safe_put(get_queue(self._state), ProgressEvent(
            self._tool, self._tcid, "start", 0, self._start_msg or f"Running {self._tool}...",
        ))
        return self

    def __exit__(self, exc_type, exc, tb):
        _safe_put(get_queue(self._state), ProgressEvent(
            self._tool, self._tcid, "end", 100, "",
        ))
        return False

    def update(self, pct: int, msg: str = "") -> None:
        _safe_put(get_queue(self._state), ProgressEvent(
            self._tool, self._tcid, "poll", max(0, min(100, int(pct))), msg,
        ))