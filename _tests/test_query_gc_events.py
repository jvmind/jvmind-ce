"""Unit tests for query_gc_events tool and the compute_stats events field."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from react_agent.gc_analyzer import analyze


_BASE = os.path.dirname(__file__)


def _load(name: str) -> str:
    with open(os.path.join(_BASE, name), "r", encoding="utf-8") as f:
        return f.read()


def test_compute_stats_includes_events_field():
    """compute_stats must populate stats['events'] with one dict per GCEvent."""
    stats = analyze(_load("gc-jdk11-g1.log"))
    assert "events" in stats
    assert isinstance(stats["events"], list)
    assert len(stats["events"]) == stats["events_total"]
    sample = stats["events"][0]
    for key in ("id", "t", "cat", "cause", "dur", "before", "after", "total", "raw", "concurrent"):
        assert key in sample, f"missing key: {key}"


class _FakeMemory:
    """Minimal memory stub returning a preloaded report dict."""

    def __init__(self, report=None):
        self._report = report

    def get_gc_report(self, session_id, report_id):
        if self._report and self._report["id"] == report_id:
            return self._report
        return None


def _sample_report_with_events():
    """Build a fake report dict matching get_gc_report() shape."""
    stats = analyze(_load("gc-jdk11-g1.log"))
    return {
        "id": "gc_test01",
        "filename": "test.log",
        "stats": stats,
        "ai_conclusion": "",
        "created_at": "2026-07-13 00:00:00",
    }


def test_query_no_filter_returns_first_20():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01")
    # Header lines
    assert "GC Events Query Result" in out
    assert "Report: gc_test01" in out
    # Matched counter should equal total events
    assert f"Matched: {mem._report['stats']['events_total']}" in out
    assert "Returned: 20" in out
    assert "Offset: 0" in out
    assert "Limit: 20" in out
    # Should contain at least one event line
    assert "- GC#" in out


def test_query_report_not_found_returns_bilingual_error():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_missing")
    assert "gc_missing" in out
    assert "not found" in out.lower()
    assert "未找到" in out


def test_query_old_report_without_events_prompts_reupload():
    """Reports persisted before this tool existed lack stats['events']."""
    from react_agent.gc_analyzer import query_events
    old_report = {
        "id": "gc_old",
        "filename": "old.log",
        "stats": {"events_total": 10, "collector": "G1", "heap_max_mb": 256.0,
                  "duration_sec": 100.0, "by_category": {}, "slowest": []},
        "ai_conclusion": "",
        "created_at": "2026-07-01 00:00:00",
    }
    mem = _FakeMemory(old_report)
    out = query_events(mem, "sid1", report_id="gc_old")
    assert "re-upload" in out.lower() or "重新上传" in out


def test_query_filter_by_category():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", category="Full")
    assert "category=Full" in out
    assert "Matched:" in out
    # Every event line must be Full
    for line in out.splitlines():
        if line.strip().startswith("- GC#"):
            assert "[Full]" in line, f"non-Full event in Full-only filter: {line}"


def test_query_filter_by_cause_substring_case_insensitive():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", cause="allocation")
    assert "cause=allocation" in out
    # All matched events must contain "allocation" in their cause (case-insensitive)
    assert "Matched:" in out
    import re
    matched_total = int(re.search(r"Matched: (\d+)", out).group(1))
    # On JDK11 G1 with Full GC, causes include "G1 Evacuation Pause" / "Allocation Failure"
    if matched_total > 0:
        for line in out.splitlines():
            if line.strip().startswith("- GC#"):
                m = re.search(r"\(cause=(.+?)\)\s*$", line)
                assert m and "allocation" in m.group(1).lower(), f"unexpected cause: {line}"


def test_query_filter_by_time_range():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    stats = mem._report["stats"]
    full_max_t = max((e["t"] or 0) for e in stats["events"])
    out = query_events(mem, "sid1", report_id="gc_test01",
                       time_start=0.0, time_end=full_max_t / 2)
    assert "time_start=0.0" in out
    assert "time_end=" in out
    import re
    matched_total = int(re.search(r"Matched: (\d+)", out).group(1))
    assert matched_total < stats["events_total"]


def test_query_filter_by_duration_min():
    import re
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", duration_min=10.0)
    assert "duration_min=10.0" in out
    # All listed events should have duration >= 10ms
    for line in out.splitlines():
        if line.strip().startswith("- GC#"):
            m = re.search(r"dur=([0-9.]+)ms", line)
            if m:
                assert float(m.group(1)) >= 10.0


def test_query_filter_combined_and():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out_a = query_events(mem, "sid1", report_id="gc_test01", category="Full")
    out_b = query_events(mem, "sid1", report_id="gc_test01",
                         category="Full", duration_min=0.0)
    # Adding duration_min=0.0 (which always matches) must not change result
    import re
    a_total = int(re.search(r"Matched: (\d+)", out_a).group(1))
    b_total = int(re.search(r"Matched: (\d+)", out_b).group(1))
    assert a_total == b_total


def test_query_gc_id_single_event():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    target_id = mem._report["stats"]["events"][5]["id"]
    out = query_events(mem, "sid1", report_id="gc_test01", gc_id=target_id)
    assert f"gc_id={target_id}" in out
    assert "Matched: 1" in out
    assert f"- GC#{target_id}" in out


def test_query_pagination_offset_limit():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    total = mem._report["stats"]["events_total"]

    page1 = query_events(mem, "sid1", report_id="gc_test01", limit=5, offset=0)
    page2 = query_events(mem, "sid1", report_id="gc_test01", limit=5, offset=5)

    assert "Offset: 0" in page1 and "Limit: 5" in page1 and "Returned: 5" in page1
    assert "Offset: 5" in page2 and "Limit: 5" in page2 and "Returned: 5" in page2
    assert f"Matched: {total}" in page1
    # Pages should contain different event lines
    page1_ids = [line for line in page1.splitlines() if line.strip().startswith("- GC#")]
    page2_ids = [line for line in page2.splitlines() if line.strip().startswith("- GC#")]
    assert len(page1_ids) == 5
    assert len(page2_ids) == 5
    assert page1_ids != page2_ids


def test_query_offset_beyond_total_returns_empty_no_error():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", limit=10, offset=999999)
    assert "Matched:" in out
    assert "Returned: 0" in out
    # Must not raise
    assert "Error" not in out


def test_query_truncated_flag_when_matched_gt_returned():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", limit=1)
    assert "[truncated]" in out


def test_query_limit_clamped_at_100():
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", limit=10000)
    assert "Limit: 100" in out


def test_query_output_under_2500_chars():
    """Even with a large matched set, the formatted text must respect the 2500 cap."""
    from react_agent.gc_analyzer import query_events
    mem = _FakeMemory(_sample_report_with_events())
    out = query_events(mem, "sid1", report_id="gc_test01", limit=100)
    assert len(out) <= 2500, f"output length {len(out)} exceeds 2500 cap"
    if len(out) >= 2500:
        # Should contain truncation hint
        assert "truncated" in out.lower()


def test_query_gc_events_in_default_tools_registry():
    from react_agent.tools import default_tools
    reg = default_tools()
    tool = reg.get("query_gc_events")
    assert tool is not None
    assert tool.name == "query_gc_events"
    # OpenAI schema export
    specs = reg.to_openai_tools()
    spec_names = {s["function"]["name"] for s in specs}
    assert "query_gc_events" in spec_names
    spec = next(s for s in specs if s["function"]["name"] == "query_gc_events")
    params = spec["function"]["parameters"]
    assert params["type"] == "object"
    props = params["properties"]
    for key in ("report_id", "category", "cause", "time_start", "time_end",
                "duration_min", "limit", "offset", "gc_id"):
        assert key in props, f"missing parameter: {key}"
    assert "report_id" in params["required"]
    # category must be enum
    assert "enum" in props["category"]
    for c in ("Young", "Full", "Mixed", "Concurrent", "InitialMark",
              "Remark", "Cleanup", "ZGC", "Shenandoah", "Other"):
        assert c in props["category"]["enum"]


def test_toolcall_to_arg_query_gc_events_passes_json_through():
    """query_gc_events needs the JSON object intact (multi-key args)."""
    from react_agent.agent import ReActAgent
    a = ReActAgent(api_key="x", base_url="https://fake.local/v1", model="m")
    args = '{"report_id":"gc_x","category":"Full","limit":10,"offset":5}'
    out = a._toolcall_to_arg("query_gc_events", args)
    assert out == args, f"expected passthrough, got {out!r}"


def test_execute_tool_query_gc_events_dispatches(monkeypatch):
    from react_agent.agent import ReActAgent
    captured = {}
    def _fake_query_events(memory, session_id, **kw):
        captured["memory"] = memory
        captured["session_id"] = session_id
        captured["kwargs"] = kw
        return "stub-result"
    import react_agent.gc_analyzer as ga
    monkeypatch.setattr(ga, "query_events", _fake_query_events)

    a = ReActAgent(api_key="x", base_url="https://fake.local/v1", model="m")
    a.memory = object()  # placeholder; captured by reference
    args = '{"report_id":"gc_x","category":"Full","limit":10,"offset":5}'
    out = a._execute_tool("sid1", "query_gc_events", args)
    assert out == "stub-result"
    assert captured["session_id"] == "sid1"
    kw = captured["kwargs"]
    assert kw["report_id"] == "gc_x"
    assert kw["category"] == "Full"
    assert kw["limit"] == 10
    assert kw["offset"] == 5
    assert captured["memory"] is a.memory


def test_langgraph_build_all_tools_includes_query_gc_events():
    from react_agent.graph.tools import build_all_tools
    tools = build_all_tools(memory=_FakeMemory())
    names = [t.name for t in tools]
    assert "query_gc_events" in names
    tool = next(t for t in tools if t.name == "query_gc_events")
    # Verify the proper _build_query_gc_events wrapper was used, not the
    # generic _build_generic_tool fallback (which would name the func
    # `_invoke_query_gc_events` and join args with ',').
    assert tool.func.__name__ == "query_gc_events", (
        f"expected proper wrapper (func name 'query_gc_events'), "
        f"got {tool.func.__name__!r} — generic fallback may be in use"
    )


def test_langgraph_query_gc_events_invoke_with_state(monkeypatch):
    """The LangGraph StructuredTool must read session_id from InjectedState."""
    from react_agent.graph.tools import build_all_tools
    captured = {}
    def _fake_query_events(memory, session_id, **kw):
        captured["session_id"] = session_id
        captured["kwargs"] = kw
        return "stub-result"
    import react_agent.gc_analyzer as ga
    monkeypatch.setattr(ga, "query_events", _fake_query_events)

    tools = build_all_tools(memory=_FakeMemory())
    tool = next(t for t in tools if t.name == "query_gc_events")
    out = tool.invoke({
        "report_id": "gc_x",
        "category": "Full",
        "limit": 10,
        "offset": 5,
        "state": {"session_id": "sid_lg"},
    })
    assert out == "stub-result"
    assert captured["session_id"] == "sid_lg"
    assert captured["kwargs"]["report_id"] == "gc_x"
    assert captured["kwargs"]["category"] == "Full"

