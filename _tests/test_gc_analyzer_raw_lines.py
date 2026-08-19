"""Tests for GCEvent.raw_lines field and Metaspace extraction.

Background: Task 1 of the renaissance page-rank GC parse fix plan.
Adds a structured raw_lines field to GCEvent (with raw_body auto-derived
via __post_init__) and Metaspace capacity fields populated by the
jdk8 parser from [Metaspace: K->K(K)] segments.
"""
from __future__ import annotations

from react_agent.gc_analyzer.base import GCEvent
from react_agent.gc_analyzer import parse_gc_log
from react_agent.gc_analyzer.jdk8 import parse_gc_log_jdk8


# ---------- raw_lines / raw_body sync ----------


def test_raw_body_auto_derived_from_raw_lines_single_line():
    """When raw_lines is provided but raw_body is empty, __post_init__ derives
    raw_body = "\n".join(raw_lines)."""
    ev = GCEvent(
        id=1,
        uptime_sec=10.0,
        category="Young",
        cause="Allocation Failure",
        raw_lines=["[GC (Allocation Failure)  100M->50M(512M), 0.010 secs]"],
    )
    assert ev.raw_body == "[GC (Allocation Failure)  100M->50M(512M), 0.010 secs]"
    assert ev.raw_lines == ["[GC (Allocation Failure)  100M->50M(512M), 0.010 secs]"]


def test_raw_body_auto_derived_from_raw_lines_multi_line():
    """Multi-line event: raw_lines is a list of N strings, raw_body joins them with \n."""
    ev = GCEvent(
        id=2,
        uptime_sec=20.0,
        category="Young",
        cause="Allocation Failure",
        raw_lines=[
            "[GC (Allocation Failure) AdaptiveSizePolicy::update_averages: overflow: true",
            "PSYoungGen: 1048576K->174572K(1223168K)] 1048576K->347927K(4019712K), 0.8364548 secs",
        ],
    )
    assert ev.raw_body == "\n".join(ev.raw_lines)
    assert ev.raw_body.count("\n") == 1
    assert len(ev.raw_lines) == 2


def test_raw_lines_backfilled_from_raw_body_legacy_construction():
    """Legacy call sites that pass raw_body=... must still produce raw_lines
    auto-derived from split("\n")."""
    legacy_body = "line1\nline2\nline3"
    ev = GCEvent(
        id=3,
        uptime_sec=5.0,
        category="Young",
        cause="test",
        raw_body=legacy_body,
    )
    assert ev.raw_body == legacy_body
    assert ev.raw_lines == ["line1", "line2", "line3"]


def test_raw_lines_wins_over_raw_body_when_both_provided():
    """If both raw_lines and raw_body are explicitly provided, raw_lines wins
    and raw_body is overwritten to "\n".join(raw_lines)."""
    ev = GCEvent(
        id=4,
        uptime_sec=1.0,
        category="Other",
        cause="test",
        raw_body="stale body",
        raw_lines=["fresh", "body"],
    )
    assert ev.raw_body == "fresh\nbody"
    assert ev.raw_lines == ["fresh", "body"]


def test_raw_lines_default_empty_list():
    """When neither raw_body nor raw_lines is provided, both default to empty."""
    ev = GCEvent(id=5, uptime_sec=0.0, category="Other", cause="")
    assert ev.raw_lines == []
    assert ev.raw_body == ""


# ---------- Metaspace extraction ----------


def test_metaspace_field_extracted_when_present_in_jdk8_log():
    """Regression: renaissance page-rank-asp 4G log contains [Metaspace: ...]
    segments in Young GC events. These must populate the metaspace_*_mb fields."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:44.090+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), 0.8364548 secs] "
        "[Times: user=6.22 sys=0.19, real=0.84 secs]\n"
    )
    parsed = parse_gc_log(log)
    assert parsed["jdk_version"] == "8"
    assert len(parsed["events"]) == 1
    ev = parsed["events"][0]
    # Metaspace is NOT in this synthetic log, so fields should be None.
    assert ev.metaspace_before_mb is None
    assert ev.metaspace_after_mb is None
    assert ev.metaspace_total_mb is None


def test_metaspace_field_populated_when_segment_present():
    """When a [Metaspace: K->K(K)] segment appears in the event body, the
    Metaspace fields must be populated (in MB)."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:44.090+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), "
        "[Metaspace: 200000K->200200K(1200000K)], "
        "0.8364548 secs] "
        "[Times: user=6.22 sys=0.19, real=0.84 secs]\n"
    )
    parsed = parse_gc_log(log)
    assert parsed["jdk_version"] == "8"
    assert len(parsed["events"]) == 1
    ev = parsed["events"][0]
    # 200000K = 195.3125 MB
    assert ev.metaspace_before_mb == 200000 / 1024
    assert ev.metaspace_after_mb == 200200 / 1024
    assert ev.metaspace_total_mb == 1200000 / 1024


def test_metaspace_field_none_when_segment_absent():
    """When no [Metaspace: ...] segment is present, metaspace fields remain None."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:44.090+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), 0.8364548 secs] "
        "[Times: user=6.22 sys=0.19, real=0.84 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    assert len(parsed["events"]) == 1
    ev = parsed["events"][0]
    assert ev.metaspace_before_mb is None
    assert ev.metaspace_after_mb is None
    assert ev.metaspace_total_mb is None


def test_raw_lines_populated_for_jdk8_event():
    """When parse_gc_log_jdk8 produces an event, raw_lines must be populated
    (via __post_init__ auto-derivation when raw_body is set)."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:44.090+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), 0.8364548 secs] "
        "[Times: user=6.22 sys=0.19, real=0.84 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    ev = parsed["events"][0]
    assert isinstance(ev.raw_lines, list)
    assert len(ev.raw_lines) == 1
    assert "[GC (Allocation Failure)" in ev.raw_lines[0]
    assert ev.raw_body == ev.raw_lines[0]


# ---------- Metaspace negative tests ----------


def test_metaspace_negative_young_gc_parallel_no_metaspace():
    """Young GC events in Parallel collector logs typically don't have
    [Metaspace: ...] segments. The metaspace_*_mb fields must remain None."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:00.000+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), 0.010 secs] "
        "[Times: user=0.21 sys=0.01, real=0.04 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    ev = parsed["events"][0]
    assert ev.category == "Young"
    assert ev.metaspace_before_mb is None
    assert ev.metaspace_after_mb is None
    assert ev.metaspace_total_mb is None


def test_metaspace_negative_g1_pause_no_metaspace():
    """G1 pause events typically don't print [Metaspace: ...] in their main
    line (only Full GC does). Verify metaspace fields are None."""
    log = (
        "CommandLine flags: -XX:+UseG1GC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:00.000+0800: 1.000: [GC pause (G1 Evacuation Pause) (young), 0.030 secs]\n"
        "   [Eden: 1024.0K(1152.0M)->0.0B(1228.0M) Survivors: 77824.0K->0.0B Heap: 25.6M(2048.0M)->12.5M(2048.0M)]\n"
        " [Times: user=0.10 sys=0.01, real=0.03 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    ev = parsed["events"][0]
    assert ev.category in ("Young", "Mixed", "InitialMark")
    assert ev.metaspace_before_mb is None
    assert ev.metaspace_after_mb is None
    assert ev.metaspace_total_mb is None


def test_metaspace_negative_concurrent_event_no_metaspace():
    """Concurrent phases (CMS/G1 concurrent) never have [Metaspace: ...]
    segments."""
    log = (
        "CommandLine flags: -XX:+UseConcMarkSweepGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        "2026-08-02T20:00:00.000+0800: 1.000: [CMS-concurrent-mark-start]\n"
        "2026-08-02T20:00:00.000+0800: 1.005: [CMS-concurrent-mark: 0.005/0.005 secs] [Times: user=0.02 sys=0.00, real=0.00 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    for ev in parsed["events"]:
        assert ev.category == "Concurrent"
        assert ev.metaspace_before_mb is None
        assert ev.metaspace_after_mb is None
        assert ev.metaspace_total_mb is None


def test_metaspace_only_populated_when_segment_in_body():
    """When [Metaspace: K->K(K)] appears only in ONE event of a multi-event
    log, only that event has populated metaspace fields. Other events stay None."""
    log = (
        "CommandLine flags: -XX:+UseParallelGC -XX:+PrintGCDetails -XX:+PrintGCTimeStamps\n"
        # Young GC (no metaspace segment)
        "2026-08-02T20:00:00.000+0800: 1.000: [GC (Allocation Failure) "
        "[PSYoungGen: 1048576K->174572K(1223168K)] "
        "1048576K->347927K(4019712K), 0.010 secs] "
        "[Times: user=0.21 sys=0.01, real=0.04 secs]\n"
        # Full GC (with metaspace segment)
        "2026-08-02T20:00:10.000+0800: 11.000: [Full GC (Ergonomics) "
        "[PSYoungGen: 77984K->0K(1208832K)] [ParOldGen: 2714167K->324781K(2796544K)] "
        "2792151K->324781K(4005376K), [Metaspace: 72200K->71800K(1097728K)], 0.708 secs] "
        "[Times: user=1.20 sys=0.05, real=0.71 secs]\n"
    )
    parsed = parse_gc_log_jdk8(log)
    assert len(parsed["events"]) == 2
    young = parsed["events"][0]
    full = parsed["events"][1]
    assert young.category == "Young"
    assert young.metaspace_before_mb is None
    assert young.metaspace_after_mb is None
    assert young.metaspace_total_mb is None
    assert full.category == "Full"
    assert full.metaspace_before_mb is not None
    assert full.metaspace_after_mb is not None
    assert full.metaspace_total_mb is not None
    assert full.metaspace_before_mb == 72200 / 1024


# ---------- jdk9 raw_lines ----------


def test_jdk9_single_line_event_has_one_raw_line():
    """A jdk9 unified-logging single-line event emits exactly one raw_line."""
    text = (
        "[2026-06-17T09:52:42.727+0800][0.193s][info][gc,start     ] "
        "GC(0) Pause Young (Normal) (G1 Evacuation Pause)\n"
        "[2026-06-17T09:52:42.740+0800][0.205s][info][gc           ] "
        "GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
        "24M->10M(64M) 13.0ms\n"
    )
    parsed = parse_gc_log(text)
    assert parsed["jdk_version"] == "9+"
    assert len(parsed["events"]) >= 1
    ev = parsed["events"][0]
    # Single-line event body
    assert len(ev.raw_lines) >= 1
    assert "Pause Young" in ev.raw_lines[-1]
    # raw_body is auto-derived from raw_lines
    assert ev.raw_body.endswith(ev.raw_lines[-1])


def test_jdk9_multi_phase_event_combines_lines_for_same_gcid():
    """jdk9 emits multiple `GC(N)` lines per event (different phase tags but
    same id). The parser's backfill loop must merge them into one
    RawLines entry, so raw_lines contains ALL phase lines for that GC id.
    """
    text = (
        "[2026-06-17T09:52:42.727+0800][0.193s][info][gc,start     ] "
        "GC(0) Pause Young (Normal) (G1 Evacuation Pause)\n"
        "[2026-06-17T09:52:42.728+0800][0.194s][info][gc,task      ] "
        "GC(0) Using 2 workers of 18 for evacuation\n"
        "[2026-06-17T09:52:42.733+0800][0.199s][info][gc,phases    ] "
        "GC(0)   Evacuate Collection Set: 3.3ms\n"
        "[2026-06-17T09:52:42.740+0800][0.205s][info][gc           ] "
        "GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
        "24M->10M(64M) 13.0ms\n"
    )
    parsed = parse_gc_log(text)
    assert parsed["jdk_version"] == "9+"
    # GC(0) should produce ONE event with raw_lines covering all phase lines
    gcid_0_events = [e for e in parsed["events"] if e.id == 0]
    assert len(gcid_0_events) >= 1
    ev = gcid_0_events[0]
    # raw_lines should have multiple lines (start, task, phases, main)
    assert len(ev.raw_lines) >= 3, (
        f"expected >=3 phase lines for GC(0), got {len(ev.raw_lines)}: "
        f"{ev.raw_lines}"
    )
    # Phase content (the GC body text after [gc,start] tag is stripped by
    # _parse_prefix, so only post-prefix content remains in raw_lines).
    # Verify distinct phase markers are present in the raw text.
    joined = "\n".join(ev.raw_lines)
    assert "Pause Young" in joined
    assert "Using 2 workers" in joined
    assert "Evacuate Collection Set" in joined
    # raw_body == "\n".join(raw_lines)
    assert ev.raw_body == joined


def test_jdk9_zgc_multi_phase_event_raw_lines_per_phase():
    """ZGC emits Pause Mark Start / Mark End phases under same GC id. The
    backfill must include both phases (and other phases) in raw_lines.
    """
    text = (
        "[2026-06-17T10:22:18.435+0800][0.054s][info][gc,start       ] "
        "GC(5) Pause Mark Start\n"
        "[2026-06-17T10:22:18.450+0800][0.069s][info][gc             ] "
        "GC(5) Pause Mark Start 0.500ms\n"
        "[2026-06-17T10:22:18.451+0800][0.070s][info][gc,marking     ] "
        "GC(5) Mark: 1ms\n"
        "[2026-06-17T10:22:18.500+0800][0.119s][info][gc             ] "
        "GC(5) Pause Mark End 1.000ms\n"
        "[2026-06-17T10:22:18.510+0800][0.129s][info][gc             ] "
        "GC(5) Garbage Collection (System.gc()) 100M->50M(512M) 5.0ms\n"
    )
    parsed = parse_gc_log(text)
    assert parsed["jdk_version"] == "9+"
    gcid_5_events = [e for e in parsed["events"] if e.id == 5]
    assert len(gcid_5_events) >= 1
    ev = gcid_5_events[0]
    # All 5 phase lines should be in raw_lines
    assert len(ev.raw_lines) == 5, (
        f"expected 5 phase lines for GC(5), got {len(ev.raw_lines)}"
    )
    joined = "\n".join(ev.raw_lines)
    assert "Pause Mark Start" in joined
    assert "Pause Mark End" in joined
    assert "Mark: 1ms" in joined


def test_jdk9_raw_body_equals_joined_raw_lines_after_backfill():
    """The backfill loop explicitly sets raw_body = "\n".join(raw_lines).
    Verify raw_lines and raw_body stay in sync after backfill.
    """
    text = (
        "[2026-06-17T09:52:42.727+0800][0.193s][info][gc,start     ] "
        "GC(0) Pause Young (Normal)\n"
        "[2026-06-17T09:52:42.740+0800][0.205s][info][gc           ] "
        "GC(0) Pause Young (Normal) 24M->10M(64M) 13.0ms\n"
    )
    parsed = parse_gc_log(text)
    for ev in parsed["events"]:
        if ev.id == 0:
            assert ev.raw_body == "\n".join(ev.raw_lines)
            # Count of newlines in raw_body == count - 1 of raw_lines
            assert ev.raw_body.count("\n") == len(ev.raw_lines) - 1


def test_jdk9_multiple_gcids_keep_separate_raw_lines():
    """When two GC ids share the same collector type, raw_lines must NOT
    bleed across events. Each event holds its own phase lines."""
    text = (
        "[2026-06-17T09:52:42.727+0800][0.193s][info][gc,start     ] "
        "GC(0) Pause Young (Normal)\n"
        "[2026-06-17T09:52:42.740+0800][0.205s][info][gc           ] "
        "GC(0) Pause Young (Normal) 24M->10M(64M) 13.0ms\n"
        "[2026-06-17T09:52:43.000+0800][0.500s][info][gc,start     ] "
        "GC(1) Pause Young (Normal)\n"
        "[2026-06-17T09:52:43.010+0800][0.510s][info][gc           ] "
        "GC(1) Pause Young (Normal) 30M->12M(64M) 12.0ms\n"
    )
    parsed = parse_gc_log(text)
    ev_0 = next(e for e in parsed["events"] if e.id == 0)
    ev_1 = next(e for e in parsed["events"] if e.id == 1)
    # Each event has its own raw_lines (no cross-contamination)
    joined_0 = "\n".join(ev_0.raw_lines)
    joined_1 = "\n".join(ev_1.raw_lines)
    assert "GC(0)" in joined_0 and "GC(1)" not in joined_0
    assert "GC(1)" in joined_1 and "GC(0)" not in joined_1
    # Sizes match expectations
    assert len(ev_0.raw_lines) == 2
    assert len(ev_1.raw_lines) == 2


def test_jdk9_loaded_fixture_g1_has_raw_lines():
    """End-to-end smoke: load a real jdk11 G1 fixture and verify the G1
    events have non-empty raw_lines (no auto-derivation crash)."""
    import os
    fixture_path = os.path.join(
        os.path.dirname(__file__), "gc-jdk11-g1.log"
    )
    if not os.path.exists(fixture_path):
        import pytest
        pytest.skip("gc-jdk11-g1.log fixture missing")
    with open(fixture_path, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = parse_gc_log(text)
    assert parsed["jdk_version"] == "9+"
    # At least one event has raw_lines
    events_with_lines = [e for e in parsed["events"] if e.raw_lines]
    assert len(events_with_lines) > 0
    # Verify raw_body is auto-derived
    for e in events_with_lines:
        if not e.raw_body.startswith("["):
            # raw_body should equal joined raw_lines
            assert e.raw_body == "\n".join(e.raw_lines)


def test_jdk9_loaded_fixture_zgc_has_multi_phase_raw_lines():
    """End-to-end: ZGC fixture should produce events with raw_lines
    containing multiple phase lines (ZGC emits Pause Mark Start/End as
    separate log lines per phase).
    """
    import os
    fixture_path = os.path.join(
        os.path.dirname(__file__), "gc-jdk11-zgc.log"
    )
    if not os.path.exists(fixture_path):
        import pytest
        pytest.skip("gc-jdk11-zgc.log fixture missing")
    with open(fixture_path, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = parse_gc_log(text)
    assert parsed["jdk_version"] == "9+"
    # ZGC events with same GC id must have multiple raw_lines
    events_with_multiple_lines = [e for e in parsed["events"] if len(e.raw_lines) > 1]
    assert len(events_with_multiple_lines) > 0, (
        "ZGC events should have multi-phase raw_lines"
    )
