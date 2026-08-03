"""State-machine preprocessor for JDK8 legacy GC logs.

Background: The original `_preprocess_lines` uses a single merged-list with
implicit conditions ("merged[-1] is an open event when its body contains
`[GC (` but not `secs]`"). This module replaces it with an explicit state
machine that splits the log into raw events (each with raw_lines metadata).

States:
    SEEKING          - not inside any block, looking for next event
    IN_HEAP_BEFORE   - inside {Heap before GC ...} block (heap content skipped)
    IN_HEAP_AFTER    - inside {Heap after GC ...} block (heap content skipped)
    IN_EVENT         - inside an open multi-line event (no closing `secs]` yet)

Heap-block interaction with events:
    The renaissance page-rank logs NEST events inside heap blocks:
        {Heap before GC ...
          <indented heap content>
        2026-08-02T20:01:30.646+0800: 47.570: [GC (Allocation Failure) AdaptiveSizeStart: ...
        AdaptiveSizeStart: 48.406 collection: 1
        AdaptiveSizeStop: collection: 1
        [PSYoungGen: ...] ..., 0.8364548 secs]
        Heap after GC ...
          <indented heap content>
        }
    The state machine accepts this: an event starting inside a heap block
    transitions to IN_EVENT, and the heap-block closing `}` afterwards is
    consumed (a leftover, no semantic meaning).

Output:
    RawGCEvent namedtuples with:
        raw_lines: List[str]      - lines belonging to this event
        start_line: int            - 0-based index of the first line in raw_lines
        has_heap_before: bool      - was preceded by {Heap before GC ...}
        has_heap_after: bool       - was followed by {Heap after GC ...}
"""
from __future__ import annotations

import re
from enum import Enum
from typing import List, NamedTuple

from .base_parser import _RE_TS, _RE_TS_DATE_ONLY


class _State(str, Enum):
    SEEKING = "SEEKING"
    IN_HEAP_BEFORE = "IN_HEAP_BEFORE"
    IN_HEAP_AFTER = "IN_HEAP_AFTER"
    IN_EVENT = "IN_EVENT"


# Recognizers
_RE_OPENS_EVENT = re.compile(r"\[(?:Full )?GC\s|\[CMS-concurrent-")
_RE_OPENS_HEAP_BEFORE = re.compile(r"^\s*(?:\{|Heap before GC)")
_RE_OPENS_HEAP_AFTER = re.compile(r"^\s*Heap after GC")


class RawGCEvent(NamedTuple):
    """Raw event produced by the state machine (before metric extraction)."""
    raw_lines: List[str]
    start_line: int
    has_heap_before: bool
    has_heap_after: bool


def _is_timestamped(line: str) -> bool:
    """True if line starts with a JDK8 timestamp (ISO date or uptime)."""
    return bool(_RE_TS.match(line) or _RE_TS_DATE_ONLY.match(line))


def _is_indented_raw(raw_line: str) -> bool:
    """True if the original (pre-strip) line starts with whitespace."""
    return raw_line.startswith((" ", "\t"))


def _is_event_close(line: str) -> bool:
    """True if a line closes a multi-line event.

    Detection: body contains `secs]` (jvm-style pause duration).
    """
    return "secs]" in line


def _is_safepoint(line: str) -> bool:
    """Safepoint / app-time lines that should be ignored (not an event)."""
    return (
        "Application time:" in line
        or "Total time for which application threads were stopped" in line
    )


def _brackets_balanced(s: str) -> bool:
    """Check if `[` and `]` are balanced in the string.

    Used to detect G1 Full GC with embedded concurrent events: the Full GC
    body has unbalanced brackets until the final closing `]` arrives. For
    example:
        [Full GC (cause) ... [GC concurrent-...start]   <- unbalanced
            ... [GC concurrent-...end, X secs]          <- still unbalanced
            ... [GC concurrent-...start]                <- still unbalanced
         NNNM->NNNM(NNNM), X secs]                      <- balanced NOW
    """
    return s.count("[") == s.count("]")


def _open_new_event(line: str) -> bool:
    """True if line starts a new GC event (after stripping timestamp prefix)."""
    return bool(_RE_OPENS_EVENT.search(line))


def _finalize_event(
    events: List[RawGCEvent],
    current_lines: List[str],
    start_line: int,
    has_heap_before: bool,
    has_heap_after: bool,
) -> None:
    """Append the current event to events if it has any content."""
    if current_lines:
        events.append(RawGCEvent(
            raw_lines=list(current_lines),
            start_line=start_line,
            has_heap_before=has_heap_before,
            has_heap_after=has_heap_after,
        ))


def _strip_timestamp(line: str) -> str:
    """Strip JDK8 timestamp prefix from a line, returning the body."""
    m = _RE_TS.match(line)
    if m:
        return m.group(m.lastindex) or ""
    m = _RE_TS_DATE_ONLY.match(line)
    if m:
        return m.group(m.lastindex) or ""
    return line


def parse_to_raw_events(text: str) -> List[RawGCEvent]:
    """Parse raw text into a list of RawGCEvent (each with raw_lines).

    State machine transitions:
      SEEKING
        ├─ {Heap before GC / lone {         → IN_HEAP_BEFORE
        ├─ Heap after GC                    → IN_HEAP_AFTER
        ├─ [GC / [Full GC / [CMS-concurrent- → IN_EVENT (open event)
        └─ safepoint / other                → ignored

      IN_HEAP_BEFORE
        ├─ [GC nested event start           → IN_EVENT (event can be nested)
        └─ any other content                → skipped (until `}` or nested event)

      IN_HEAP_AFTER
        ├─ `}`                              → SEEKING (orphan after-block closes)
        └─ any other content                → skipped

      IN_EVENT
        ├─ [Times: ...] / `secs]` close     → SEEKING (event finalized)
        ├─ new [GC / [Full GC event         → SEEKING then IN_EVENT (flush+open)
        ├─ Heap after GC block              → finalize event, then IN_HEAP_AFTER
        ├─ `{Heap before GC` (rare)         → finalize event, then IN_HEAP_BEFORE
        ├─ safepoint                        → finalize event, then SEEKING
        └─ continuation line (no timestamp) → append to current event
    """
    events: List[RawGCEvent] = []
    state = _State.SEEKING
    current_lines: List[str] = []
    has_heap_before = False
    has_heap_after = False
    start_line = 0

    for line_idx, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue

        is_indented = _is_indented_raw(raw_line)
        is_ts = _is_timestamped(line)
        body = _strip_timestamp(line) if is_ts else line

        # --- SEEKING ---
        if state == _State.SEEKING:
            if _RE_OPENS_HEAP_BEFORE.search(body):
                state = _State.IN_HEAP_BEFORE
                has_heap_before = False
                continue
            if _RE_OPENS_HEAP_AFTER.search(body):
                state = _State.IN_HEAP_AFTER
                has_heap_after = False
                continue
            if _open_new_event(body):
                current_lines = [line]
                start_line = line_idx
                has_heap_after = False
                state = _State.IN_EVENT
                continue
            if _is_safepoint(line):
                continue
            # Non-event line (CommandLine flags: ..., version strings, etc.):
            # emit as a standalone single-line event so downstream code that
            # scans lines (e.g. collector detection looking for
            # "UseConcMarkSweepGC") still sees them.
            events.append(RawGCEvent(
                raw_lines=[line],
                start_line=line_idx,
                has_heap_before=False,
                has_heap_after=False,
            ))
            continue

        # --- IN_HEAP_BEFORE ---
        if state == _State.IN_HEAP_BEFORE:
            # Nested event inside heap-before block (renaissance pattern):
            # mark has_heap_before=True since the event is logically preceded
            # by this heap block.
            if _open_new_event(body):
                current_lines = [line]
                start_line = line_idx
                has_heap_before = True
                has_heap_after = False
                state = _State.IN_EVENT
                continue
            # `}` closes the heap block (only relevant if no nested event)
            if line == "}":
                has_heap_before = True
                state = _State.SEEKING
                continue
            # Any other content (indented heap content, etc.) is skipped
            continue

        # --- IN_HEAP_AFTER ---
        if state == _State.IN_HEAP_AFTER:
            if line == "}":
                # If we have an open event, finalize it now (heap-after block closes)
                if current_lines:
                    _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                    current_lines = []
                    has_heap_before = False
                    has_heap_after = False
                else:
                    has_heap_after = True
                state = _State.SEEKING
                continue
            # Any other content (indented heap content, etc.) is skipped
            continue

        # --- IN_EVENT ---
        if state == _State.IN_EVENT:
            # New event starts (unindented [GC / [Full GC / [CMS-concurrent-):
            # MUST come BEFORE the generic secs] check, because a new event
            # line itself contains `secs]` in its closing line — and we want
            # it to flush the previous event, not be absorbed as a continuation.
            # EXCEPTION: G1 Full GC with embedded concurrent events like:
            #   [Full GC (cause) ... [GC concurrent-...start]
            #     ...
            #      NNNM->NNNM(NNNM), X secs]    <- closing `]` here
            # The current event body has unbalanced brackets (`[` count != `]`
            # count), so the Full GC is still open. The new `[GC ...` line is
            # logically a continuation.
            if not is_indented and _open_new_event(body):
                merged_so_far = " ".join(current_lines)
                if current_lines and not _brackets_balanced(merged_so_far):
                    # Event still open (unbalanced brackets): append as continuation
                    current_lines.append(line)
                    continue
                _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                current_lines = [line]
                start_line = line_idx
                has_heap_before = False
                has_heap_after = False
                state = _State.IN_EVENT
                continue

            # `[Times: ...]` closes the event immediately
            if body.startswith("[Times:"):
                current_lines.append(line)
                _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                current_lines = []
                has_heap_before = False
                has_heap_after = False
                state = _State.SEEKING
                continue

            # `[Times: ...]` line: append as continuation (matches the original
            # `_preprocess_lines` behavior where `[Times:` was always treated
            # as a continuation of the prior event). The event will be closed
            # when a new [GC line starts (or at EOF).
            if body.startswith("[Times:"):
                current_lines.append(line)
                continue

            # Closing line with `secs]`: append and close event
            if _is_event_close(body):
                current_lines.append(line)
                _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                current_lines = []
                has_heap_before = False
                has_heap_after = False
                state = _State.SEEKING
                continue

            # Heap after GC block: keep event open (don't flush yet), mark
            # has_heap_after=True, transition to IN_HEAP_AFTER. The event
            # will be finalized when the heap-after block closes (`}`).
            if _RE_OPENS_HEAP_AFTER.search(body):
                has_heap_after = True
                state = _State.IN_HEAP_AFTER
                continue

            # Heap before GC block (rare mid-event): flush event, enter heap block
            if _RE_OPENS_HEAP_BEFORE.search(body):
                _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                current_lines = []
                has_heap_before = False
                has_heap_after = False
                state = _State.IN_HEAP_BEFORE
                continue

            # Safepoint line: flush event
            if _is_safepoint(line):
                _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
                current_lines = []
                has_heap_before = False
                has_heap_after = False
                state = _State.SEEKING
                continue

            # Default: continuation line (no timestamp) — append
            if not is_ts:
                current_lines.append(line)
                continue

            # Other timestamped line (shouldn't normally happen): flush and treat as new
            _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)
            current_lines = []
            has_heap_before = False
            has_heap_after = False
            state = _State.SEEKING
            continue

    # ------ end of input: flush any open event ------
    if current_lines:
        _finalize_event(events, current_lines, start_line, has_heap_before, has_heap_after)

    return events


def _flatten_to_merged_lines(events: List[RawGCEvent]) -> List[str]:
    """Convert list of RawGCEvent back to merged single-line strings (for
    backward compatibility with metric extractors that operate on a single
    string).

    Each event's raw_lines is joined with spaces (matching the original
    _preprocess_lines behavior where multi-line events were concat'd with
    a single space). The first line is the line with the timestamp prefix;
    continuation lines are appended with a space separator.
    """
    merged = []
    for ev in events:
        if not ev.raw_lines:
            continue
        if len(ev.raw_lines) == 1:
            merged.append(ev.raw_lines[0])
        else:
            merged.append(" ".join(ev.raw_lines))
    return merged


def _flatten_to_preprocessed_lines(events: List[RawGCEvent]) -> List[str]:
    """Alias for _flatten_to_merged_lines — kept for clarity at call sites."""
    return _flatten_to_merged_lines(events)
