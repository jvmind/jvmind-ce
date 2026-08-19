"""Tests for query_gc_events output format and raw_body preview behavior.

Background: query_gc_events used to truncate each event's raw_body to
120 chars + `...`, which confused the LLM (interpreted the `...` as data
corruption rather than display truncation). The fix increases the
preview to 500 chars, marks truncation explicitly as
`raw[preview 500/1039 chars]: ...`, and adds a hint pointing to
read_gc_report for the full uncut content.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import pytest

from react_agent.gc_analyzer import parse_gc_log, compute_stats
from react_agent.gc_analyzer import query_events


_BASE = Path(__file__).resolve().parent


def _build_memory(report_id: str = "79eba75337",
                  filename: str = "gc-jdk8-parallel-page-rank-4G.log"):
    """Build a fake memory stub with the renaissance page-rank-asp 4G log
    parsed and persisted. Used by query_events which calls
    memory.get_gc_report(session_id, report_id).
    """
    log_path = _BASE / "gc-jdk8-parallel-page-rank-4G.log"
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_gc_log(text)
    stats = compute_stats(parsed)

    class _Memory:
        def get_gc_report(self, sid, rid):
            return {
                "id": report_id,
                "filename": filename,
                "stats": stats,
                "has_ai": False,
                "created_at": "2026-08-02",
            }

    return _Memory()


@pytest.fixture
def renaissance_memory():
    """Memory stub backed by the 4G renaissance page-rank-asp log.

    Skips if the fixture file is unavailable (e.g. CI without real logs).
    """
    mem = _build_memory()
    if mem is None:
        pytest.skip("gc-jdk8-parallel-page-rank-4G.log fixture missing")
    return mem


# ---------- Preview behavior ----------


def test_query_gc_events_full_gc_shows_head_tail_preview(renaissance_memory):
    """The Full GC raw_body (~1039 chars) shows a head+tail preview
    capturing both cause context AND closing pause duration. Hint to
    use read_gc_report is appended at the start.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Full", limit=20,
    )
    # Head+tail preview marker
    assert "raw[preview head 500 + tail 400 / 1039 chars]:" in result
    # Hint at the start (survives max_chars truncation)
    assert "read_gc_report(79eba75337)" in result
    # Cause/header info still present
    assert "GC#57" in result
    assert "cause=Ergonomics" in result


def test_query_gc_events_full_gc_preview_includes_continuation_content(renaissance_memory):
    """The 500-char head preview must include the AdaptiveSizeStart /
    PSAdaptiveSizePolicy content that drives cause diagnosis. Before
    the fix, the 120-char cut hid all of this.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Full", limit=20,
    )
    # Head carries AdaptiveSize content
    assert "AdaptiveSizeStart" in result
    assert "PSAdaptiveSizePolicy" in result
    assert "compute_eden_space_size" in result


def test_query_gc_events_full_gc_preview_includes_closing_secs_line(renaissance_memory):
    """The 400-char tail preview must include the closing `[PSYoungGen: ...]
    ..., 0.7079552 secs]` line. Without this, the LLM can't see the actual
    pause duration. Before the fix (120-char), this was entirely cut off.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Full", limit=20,
    )
    # Tail carries the closing line
    assert "0.7079552 secs" in result
    assert "[Times:" in result


def test_query_gc_events_young_gc_no_preview_marker(renaissance_memory):
    """Young GC raw_body (~875 chars) fits in head+tail budget (900 chars)
    so it's shown whole — no preview marker.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Young", limit=1,
    )
    assert "[preview" not in result
    # Whole raw body present
    assert "Allocation Failure" in result
    assert "0.8364548 secs" in result
    # Hint still present
    assert "read_gc_report(79eba75337)" in result


def test_query_gc_events_short_raw_body_no_truncation_marker(renaissance_memory):
    """Events with raw_body fitting in head+tail budget (≤900 chars) must
    NOT show the `[preview N/M]` marker (no truncation). Use a synthetic
    event body."""
    text = (
        "CommandLine flags: -XX:+UseParallelGC\n"
        "2026-08-02T20:00:00.000+0800: 1.000: [GC (Allocation Failure) "
        "100M->50M(512M), 0.010 secs]\n"
    )
    parsed = parse_gc_log(text)
    stats = compute_stats(parsed)

    class _Mem:
        def get_gc_report(self, sid, rid):
            return {
                "id": "test-rid",
                "filename": "synthetic.log",
                "stats": stats,
                "has_ai": False,
                "created_at": "2026-08-02",
            }

    result = query_events(_Mem(), "fake-sid", report_id="test-rid", limit=20)
    # Short body: no [preview N/M marker
    assert "[preview" not in result
    # But still includes the raw line
    assert "Allocation Failure" in result
    # Hint still present (raw was emitted)
    assert "read_gc_report(test-rid)" in result


def test_query_gc_events_hint_at_top_survives_truncation(renaissance_memory):
    """The `read_gc_report(...)` hint must appear at the TOP of the output
    so it survives max_chars truncation at the bottom.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Young", limit=20,
    )
    # Find positions of hint and the 'truncated output' suffix
    hint_pos = result.find("read_gc_report(79eba75337)")
    truncate_pos = result.find("refine filter")
    if truncate_pos > 0:
        # Hint must come before truncation point
        assert hint_pos < truncate_pos, (
            f"hint at {hint_pos} must appear before truncate suffix at {truncate_pos}"
        )
    else:
        # No truncation — hint still present
        assert hint_pos > 0


def test_query_gc_events_hint_appears_once_per_call(renaissance_memory):
    """The `read_gc_report(...)` hint must appear exactly once per call
    even when multiple events have raw_body.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Young", limit=3,
    )
    # The hint appears once (at top)
    # Note: occurrences come from the hint text + the report_id in the header
    assert result.count("For full raw_body of any event: read_gc_report") == 1


def test_query_gc_events_hint_suppressed_when_no_raw(renaissance_memory):
    """If no event has raw_body, the hint should NOT be appended
    (avoids confusing the LLM with a useless suggestion)."""
    text = (
        "CommandLine flags: -XX:+UseParallelGC\n"
        "2026-08-02T20:00:00.000+0800: 1.000: [GC (Allocation Failure) "
        "100M->50M(512M), 0.010 secs]\n"
    )
    parsed = parse_gc_log(text)
    stats = compute_stats(parsed)
    # Strip raw_body from each event to simulate "no raw" case
    for e in stats["events"]:
        e["raw"] = ""

    class _Mem:
        def get_gc_report(self, sid, rid):
            return {
                "id": "no-raw-rid",
                "filename": "synthetic.log",
                "stats": stats,
                "has_ai": False,
                "created_at": "2026-08-02",
            }

    result = query_events(_Mem(), "fake-sid", report_id="no-raw-rid", limit=20)
    # No raw → no hint
    assert "For full raw_body of any event" not in result


# ---------- Backward compatibility ----------


def test_query_gc_events_output_header_unchanged(renaissance_memory):
    """Verify the existing header format (Report/Collector/Heap/Duration)
    is preserved by the new preview logic.
    """
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Full", limit=20,
    )
    assert "GC Events Query Result" in result
    assert "Report: 79eba75337 (gc-jdk8-parallel-page-rank-4G.log)" in result
    assert "Collector: Parallel" in result
    assert "Heap:" in result
    assert "Duration:" in result
    assert "Filter: category=Full" in result
    assert "Matched: 1" in result


def test_query_gc_events_output_max_chars_still_enforced(renaissance_memory):
    """Even with 500-char previews, the max_chars=2500 ceiling is still
    honored. Multiple events that would overflow should be cut off with
    the existing 'refine filter' suffix.
    """
    # Limit=20 returns 20 events, each with ~500 char preview + ~200 char
    # metadata = ~14000 chars, well over 2500.
    result = query_events(
        renaissance_memory, "fake-sid",
        report_id="79eba75337", category="Young", limit=20,
    )
    assert "refine filter" in result or "truncated" in result.lower()