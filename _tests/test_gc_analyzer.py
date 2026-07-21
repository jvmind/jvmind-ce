"""GC 解析器单元测试：覆盖 JDK9+/JDK8、ZGC、Shenandoah 与并发阶段统计。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from react_agent.gc_analyzer import analyze
from react_agent.gc_analyzer.base import GCEvent
from react_agent.gc_analyzer.compute_stats import _diagnose_memory


BASE = os.path.dirname(__file__)


def _load(name: str) -> str:
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as f:
        return f.read()


def test_g1_jdk9_baseline():
    stats = analyze(_load("gc-jdk11-g1.log"))
    assert stats["collector"] == "G1"
    assert stats["by_category"]["Full"]["count"] == 3
    assert stats["by_category"]["Young"]["count"] > 1000
    assert stats["events_total"] > 1000
    assert "Concurrent" in stats["by_category"]
    assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0


def test_jdk8_samples_still_parse():
    g1 = analyze(_load("gc-jdk8-g1-full.log"))
    assert g1["jdk_version"] == "8"
    assert g1["collector"] == "G1"
    assert g1["by_category"]["Full"]["count"] == 5
    assert g1["by_category"]["Concurrent"]["total_pause_ms"] == 0

    parallel = analyze(_load("gc-jdk8-parallel.log"))
    assert parallel["jdk_version"] == "8"
    assert parallel["collector"] == "Parallel"
    assert parallel["by_category"]["Full"]["count"] > 0


def test_zgc_multiple_pauses_same_gc_id_are_not_deduped():
    stats = analyze(_load("gc-jdk11-zgc.log"))
    assert stats["collector"] == "Z"
    assert stats["heap_max_mb"] > 0
    assert stats["events_total"] > 0
    assert stats["by_category"]["ZGC"]["count"] > 0
    assert stats["by_category"]["Concurrent"]["count"] > 0
    assert stats["total_pause_ms"] > 0
    assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0
    assert all(e["cat"] != "Concurrent" for e in stats["slowest"])


def test_shenandoah_concurrent_duration_not_counted_as_pause():
    stats = analyze(_load("gc-jdk11-shenandoah.log"))
    assert stats["collector"] == "Shenandoah"
    assert stats["heap_max_mb"] > 0
    assert stats["events_total"] > 0
    assert stats["by_category"]["Shenandoah"]["count"] > 0
    assert stats["total_pause_ms"] > 0
    assert stats["by_category"]["Shenandoah"]["total_pause_ms"] > 0
    assert stats["by_category"]["Shenandoah"]["max_pause_ms"] > 0
    assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0


def test_jdk8_g1_sample_recognizes_all_event_types():
    stats = analyze(_load("jdk8_g1_sample.txt"))
    assert stats["jdk_version"] == "8"
    assert stats["collector"] == "G1"
    assert stats["events_total"] == 11
    cats = stats["by_category"]
    for cat in ("Young", "InitialMark", "Mixed", "Concurrent", "Remark", "Cleanup"):
        assert cat in cats
    assert cats["InitialMark"]["count"] == 1
    assert cats["Cleanup"]["count"] == 1
    assert cats["Concurrent"]["count"] == 6
    assert cats["Concurrent"]["total_pause_ms"] == 0
    assert stats["heap_max_mb"] == 64.0
    assert cats["Young"]["total_freed_mb"] > 0
    heap_points = [p for p in stats["series"] if p["total"] == 64.0 and p["before"] > 0]
    assert heap_points
    assert heap_points[0]["before"] > heap_points[0]["after"]
    assert stats["avg_heap_usage_pct"] is not None
    assert stats["max_heap_usage_pct"] is not None


def test_jdk8_g1_full_gc_with_embedded_concurrent_events_is_full():
    """G1 Full GC 触发 marking cycle 时，日志会跨多行输出：
        [Full GC (cause) <ts>: [GC concurrent-root-region-scan-start]
        <ts>: [GC concurrent-root-region-scan-end, X secs]
        <ts>: [GC concurrent-mark-start]
         NNNM->NNNM(NNNNM), X secs]
           [Eden: ...]
         [Times: ...]

    应正确识别为 Full GC（不是 Concurrent / Mixed），且嵌套并发事件不被单独计数。
    """
    stats = analyze("""
CommandLine flags: -XX:+PrintGC -XX:+PrintGCDetails -XX:+UseG1GC
2026-07-12T23:07:20.386+0800: 11.127: [Full GC (Metadata GC Threshold) 2026-07-12T23:07:20.386+0800: 11.127: [GC concurrent-root-region-scan-start]
2026-07-12T23:07:20.387+0800: 11.127: [GC concurrent-root-region-scan-end, 0.0007813 secs]
2026-07-12T23:07:20.387+0800: 11.128: [GC concurrent-mark-start]
 386M->303M(2048M), 0.4885603 secs]
   [Eden: 0.0B(1152.0M)->0.0B(1228.0M) Survivors: 77824.0K->0.0B Heap: 386.9M(2048.0M)->303.4M(2048.0M)], [Metaspace: 45567K->45567K(1097728K)]
 [Times: user=0.85 sys=0.04, real=0.49 secs]
""")
    cats = stats["by_category"]
    # 关键断言：必须是 Full GC
    assert cats.get("Full", {}).get("count", 0) == 1, (
        f"G1 多行 Full GC 被误判: {cats}"
    )
    # 嵌套的并发事件不应被单独计数（root-region-scan-end 在 Full GC 关闭前）
    assert cats.get("Concurrent", {}).get("count", 0) == 0
    # 唯一事件就是 Full GC
    assert stats["events_total"] == 1
    ev = stats["series"][0]
    assert ev["cat"] == "Full"
    assert ev["before"] == 386.0
    assert ev["after"] == 303.0
    assert ev["total"] == 2048.0
    # Top 10 Slowest Events UI 显示 raw_body（不是 raw_type），必须含完整多行
    slowest = stats["slowest"]
    assert len(slowest) == 1
    assert "concurrent-mark-start" in slowest[0]["raw_type"]
    assert "concurrent-root-region-scan-end" in slowest[0]["raw_type"]
    assert "386M->303M" in slowest[0]["raw_type"]


def test_jdk8_g1_standalone_concurrent_mark_start_with_heap_delta_is_mixed():
    """G1 日志中独立一行 `[GC concurrent-mark-start] NNNM->NNNM(NNNNM)`（无 [Full GC 前缀）
    算 Mixed GC（标记周期起点 + 老年代 regions 被回收）。
    """
    stats = analyze("""
CommandLine flags: -XX:+PrintGC -XX:+PrintGCDetails -XX:+UseG1GC
2026-06-17T09:48:38.567+0800: 1.268: [GC concurrent-mark-start] 375M->299M(2048M), 0.4866134 secs] [Eden: 0.0B(1172.0M)->0.0B(1228.0M) Survivors: 57344.0K->0.0B Heap: 375.5M(2048.0M)->299.8M(2048.0M)], [Metaspace: 45614K->45614K(1097728K)] [Times: user=0.74 sys=0.08, real=0.49 secs]
""")
    cats = stats["by_category"]
    assert cats.get("Young", {}).get("count", 0) == 0
    assert stats["events_total"] == 1
    ev = stats["series"][0]
    assert ev["cat"] == "Mixed", f"got {ev['cat']}"
    assert ev["before"] == 375.0
    assert ev["after"] == 299.0
    assert ev["total"] == 2048.0


def test_unified_cms_collector_detection():
    stats = analyze("""
[0.005s][info][gc] Using Concurrent Mark Sweep
[0.091s][info][gc] GC(0) Pause Young (Allocation Failure) 17M->6M(61M) 3.359ms
""")
    assert stats["collector"] == "CMS"
    assert stats["by_category"]["Young"]["count"] == 1


def test_jdk8_cms_flag_identifies_cms_collector():
    stats = analyze("""
CommandLine flags: -XX:+PrintGC -XX:+PrintGCDetails -XX:+UseConcMarkSweepGC -XX:+UseParNewGC
0.091: [GC (Allocation Failure) [ParNew: 17445K->2172K(19648K), 0.001 secs] 17445K->6670K(63360K), 0.0033590 secs]
""")
    assert stats["jdk_version"] == "8"
    assert stats["collector"] == "CMS"


def test_generational_zgc_y_o_prefixes_are_classified():
    stats = analyze("""
[0.005s][info][gc] Using The Z Garbage Collector
[0.007s][info][gc,init] Max Capacity: 64M
[0.089s][info][gc,phases] GC(0) Y: Pause Mark Start (Major) 0.014ms
[0.091s][info][gc,phases] GC(0) Y: Concurrent Mark 1.380ms
[0.096s][info][gc,phases] GC(0) Y: Pause Relocate Start 0.012ms
[0.107s][info][gc,phases] GC(0) O: Concurrent Mark 0.345ms
""")
    assert stats["collector"] == "Z"
    assert stats["heap_max_mb"] == 64.0
    assert stats["by_category"]["ZGC"]["count"] == 2
    assert stats["by_category"]["Concurrent"]["count"] == 2
    assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0


def test_jdk8_g1_pause_without_inline_heap_still_becomes_event():
    stats = analyze("""
CommandLine flags: -XX:+PrintGC -XX:+PrintGCDetails -XX:+UseG1GC
2026-06-17T09:48:37.904+0800: 0.105: [GC pause (G1 Evacuation Pause) (young), 0.0274956 secs]
   [Eden: 24576.0K(24576.0K)->0.0B(33792.0K) Survivors: 0.0B->3072.0K Heap: 26014.0K(65536.0K)->8396.5K(65536.0K)]
""")
    assert stats["jdk_version"] == "8"
    assert stats["collector"] == "G1"
    assert stats["events_total"] == 1
    assert stats["heap_max_mb"] == 64.0
    assert stats["by_category"]["Young"]["count"] == 1
    assert stats["by_category"]["Young"]["total_freed_mb"] == 17.2
    assert stats["series"][0]["before"] == 25.4
    assert stats["series"][0]["after"] == 8.2
    assert stats["series"][0]["total"] == 64.0


def test_zgc_summary_backfills_heap_to_pause_events():
    stats = analyze("""
[0.010s][info][gc] Using The Z Garbage Collector
[0.020s][info][gc,init] Max Capacity: 64M
[0.128s][info][gc,phases   ] GC(0) Pause Mark Start 0.015ms
[0.132s][info][gc,phases   ] GC(0) Pause Mark End 0.010ms
[0.141s][info][gc,phases   ] GC(0) Pause Relocate Start 0.007ms
[0.163s][info][gc          ] GC(0) Garbage Collection (Warmup) 52M(81%)->30M(47%)
""")
    assert stats["collector"] == "Z"
    assert stats["heap_max_mb"] == 64.0
    assert stats["events_total"] >= 3
    # series contains non-concurrent events
    series = stats["series"]
    assert len(series) >= 3
    heap_points = [p for p in series if p["before"] > 0]
    # All three Pause events should have heap > 0
    assert len(heap_points) == len(series)
    first = series[0]
    assert first["before"] == 52.0
    assert first["after"] == 30.0
    # category counts
    assert "ZGC" in stats["by_category"]
    assert stats["by_category"]["ZGC"]["count"] == 3


def test_gc_start_associates_uptime_with_completion_line():
    stats = analyze("""
[0.005s][info][gc] Using G1
[172.536s][info][gc,start    ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation)
[172.554s][info][gc          ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation) 919M->919M(2048M) 17.158ms
""")
    assert stats["collector"] == "G1"
    assert stats["events_total"] == 1
    assert stats["by_category"]["Young"]["count"] == 1
    ev = stats["series"][0]
    assert ev["t"] == 172.536
    assert ev["dur"] == 17.158
    assert ev["before"] > 900
    assert ev["after"] > 900
    assert stats["parsed_lines"] == 2
    assert stats["total_lines"] == 3


def test_full_gc_start_log_with_intermediate_lines():
    stats = analyze("""[2026-06-23T10:32:23.248+0800][9.819s][info][gc,start    ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation)
[2026-06-23T10:32:23.252+0800][9.824s][info][gc,task     ] GC(0) Using 18 workers of 18 for evacuation
[2026-06-23T10:32:23.256+0800][9.827s][info][gc,phases   ] GC(0)   Pre Evacuate Collection Set: 0.68ms
[2026-06-23T10:32:23.257+0800][9.829s][info][gc,phases   ] GC(0)   Merge Heap Roots: 0.21ms
[2026-06-23T10:32:23.258+0800][9.830s][info][gc,phases   ] GC(0)   Evacuate Collection Set: 1.43ms
[2026-06-23T10:32:23.259+0800][9.831s][info][gc,phases   ] GC(0)   Post Evacuate Collection Set: 0.32ms
[2026-06-23T10:32:23.259+0800][9.831s][info][gc,phases   ] GC(0)   Other: 3.09ms
[2026-06-23T10:32:23.260+0800][9.832s][info][gc,heap     ] GC(0) Eden regions: 1->0(468)
[2026-06-23T10:32:23.261+0800][9.833s][info][gc,heap     ] GC(0) Survivor regions: 0->1(13)
[2026-06-23T10:32:23.262+0800][9.834s][info][gc,heap     ] GC(0) Old regions: 2->2
[2026-06-23T10:32:23.263+0800][9.835s][info][gc,heap     ] GC(0) Humongous regions: 918->918
[2026-06-23T10:32:23.264+0800][9.836s][info][gc,metaspace] GC(0) Metaspace: 100K(320K)->100K(320K) NonClass: 93K(192K)->93K(192K) Class: 6K(128K)->6K(128K)
[2026-06-23T10:32:23.265+0800][9.836s][info][gc          ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation) 919M->919M(2048M) 17.158ms
[2026-06-23T10:32:23.265+0800][9.837s][info][gc,cpu      ] GC(0) User=0.00s Sys=0.01s Real=0.02s
""")
    assert stats["collector"] == "Unknown"
    assert stats["heap_max_mb"] == 2048.0
    assert stats["events_total"] == 1
    assert stats["by_category"]["Young"]["count"] == 1
    ev = stats["series"][0]
    assert ev["t"] == 9.819
    assert ev["dur"] == 17.158
    assert ev["before"] == 919.0
    assert ev["after"] == 919.0
    assert ev["total"] == 2048.0
    assert ev["cat"] == "Young"
    assert stats["parsed_lines"] == 2
    assert stats["total_lines"] == 14


def test_cause_extraction_multiple_parens():
    stats = analyze("""
[0.024s][info][gc] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation) 44M->35M(64M) 7.318ms
""")
    ev = stats["slowest"][0]
    assert ev["cause"] == "G1 Humongous Allocation"


def test_cause_extraction_normal_g1():
    stats = analyze("""
[0.024s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 25M->8M(64M) 14.961ms
""")
    ev = stats["slowest"][0]
    assert ev["cause"] == "G1 Evacuation Pause"


def test_cause_extraction_system_gc():
    stats = analyze("""
[0.005s][info][gc] Using G1
[1.000s][info][gc] GC(0) Pause Full (System.gc()) 50M->48M(64M) 10.234ms
""")
    assert stats["by_category"]["Full"]["count"] == 1
    ev = stats["slowest"][0]
    assert "System.gc" in ev["cause"]


def test_gc_marking_sub_phases_not_counted_as_events():
    stats = analyze("""
[0.010s][info][gc] Using G1
[9.819s][info][gc,start    ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation)
[9.836s][info][gc          ] GC(0) Pause Young (Concurrent Start) (G1 Humongous Allocation) 919M->919M(2048M) 17.158ms
[9.837s][info][gc          ] GC(1) Concurrent Mark Cycle
[9.839s][info][gc,marking  ] GC(1) Concurrent Scan Root Regions
[9.843s][info][gc,marking  ] GC(1) Concurrent Scan Root Regions 4.014ms
[9.851s][info][gc,marking  ] GC(1) Concurrent Mark 13.136ms
[9.871s][info][gc          ] GC(1) Concurrent Mark Cycle 33.801ms
""")
    assert stats["events_total"] == 2
    assert stats["by_category"]["Young"]["count"] == 1
    assert "Concurrent" in stats["by_category"]
    assert stats["by_category"]["Concurrent"]["count"] == 1


def test_gc_phases_still_create_events_for_zgc():
    stats = analyze("""
[0.010s][info][gc] Using The Z Garbage Collector
[0.020s][info][gc,init] Max Capacity: 64M
[0.128s][info][gc,phases   ] GC(0) Pause Mark Start 0.015ms
[0.132s][info][gc,phases   ] GC(0) Pause Mark End 0.010ms
[0.141s][info][gc,phases   ] GC(0) Pause Relocate Start 0.007ms
[0.163s][info][gc          ] GC(0) Garbage Collection (Warmup) 52M(81%)->30M(47%)
""")
    assert stats["collector"] == "Z"
    assert stats["by_category"]["ZGC"]["count"] == 3


def test_concurrent_cleanup_for_next_mark_is_concurrent_not_cleanup():
    stats = analyze("""
[0.010s][info][gc] Using G1
[9.861s][info][gc,start    ] GC(1) Pause Cleanup
[9.862s][info][gc          ] GC(1) Pause Cleanup 924M->924M(2048M) 0.856ms
[9.864s][info][gc,marking  ] GC(1) Concurrent Cleanup for Next Mark 5.212ms
[9.871s][info][gc          ] GC(1) Concurrent Mark Cycle 33.801ms
""")
    assert stats["by_category"]["Cleanup"]["count"] == 1
    assert "Concurrent" in stats["by_category"]
    assert stats["by_category"]["Concurrent"]["count"] == 1
    assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0


def _make_full_gc(cause, after_pct, id_=1):
    """Helper: build a synthetic Full GC event with given cause and post-GC heap %."""
    after_mb = 4000 * after_pct / 100
    return GCEvent(
        id=f"gc{id_}", uptime_sec=10.0, duration_ms=200,
        category="Full", cause=cause,
        heap_before_mb=4000, heap_after_mb=after_mb, heap_total_mb=4000,
        is_concurrent=False,
    )


def test_oom_risk_g1_single_full_gc_is_medium_not_high():
    """单个 G1 Full GC（即使伴随高堆占用）不应直接判 OOM High。
    System.gc() 这类主动触发常见，不应误报。"""
    events = [
        _make_full_gc("System.gc()", 40.0),  # 单次 Full GC
    ]
    result = _diagnose_memory(
        events, "G1", heap_max_mb=4000, max_heap_usage_pct=70.0,
        avg_heap_usage_pct=50.0,
        by_category={"Full": {"count": 1}, "Young": {"count": 100}},
    )
    rules_severity = [(f["rule"], f["severity"]) for f in result["findings"]]
    assert ("g1_full_gc", "medium") in rules_severity, rules_severity
    assert result["oom_risk"] != "high", result


def test_oom_risk_g1_sustained_full_gc_is_high():
    """G1 多次 Full GC（≥3）才是真正的高风险信号。"""
    events = [_make_full_gc("Allocation Failure", 60.0, id_=i) for i in range(1, 6)]
    result = _diagnose_memory(
        events, "G1", heap_max_mb=4000, max_heap_usage_pct=85.0,
        avg_heap_usage_pct=70.0,
        by_category={"Full": {"count": 5}},
    )
    rules_severity = [(f["rule"], f["severity"]) for f in result["findings"]]
    assert ("g1_full_gc", "high") in rules_severity, rules_severity
    assert result["oom_risk"] == "high", result


def test_oom_risk_single_full_gc_with_high_heap_is_medium_not_high():
    """平均堆占用 95% + 单次 Full GC 不应判 OOM High（之前是 High）。"""
    events = [
        GCEvent(id="y1", uptime_sec=5.0, duration_ms=50,
                category="Young", cause="G1 Evacuation Pause",
                heap_before_mb=3800, heap_after_mb=3700, heap_total_mb=4000,
                is_concurrent=False),
        _make_full_gc("Allocation Failure", 92.5, id_=2),
    ]
    result = _diagnose_memory(
        events, "G1", heap_max_mb=4000, max_heap_usage_pct=96.0,
        avg_heap_usage_pct=95.0,
        by_category={"Young": {"count": 1}, "Full": {"count": 1}},
    )
    oom_critical_findings = [f for f in result["findings"] if f["rule"] == "oom_critical"]
    assert oom_critical_findings, "oom_critical finding expected"
    assert oom_critical_findings[0]["severity"] == "medium", oom_critical_findings[0]


def test_oom_risk_max_heap_98_is_high():
    """最大堆占用 ≥ 98% 是真正的 OOM 临界，无论 Full GC 数量。"""
    events = [
        _make_full_gc("Allocation Failure", 99.0, id_=1),
    ]
    result = _diagnose_memory(
        events, "G1", heap_max_mb=4000, max_heap_usage_pct=98.0,
        avg_heap_usage_pct=90.0,
        by_category={"Full": {"count": 1}},
    )
    oom_critical_findings = [f for f in result["findings"] if f["rule"] == "oom_critical"]
    assert oom_critical_findings[0]["severity"] == "high", oom_critical_findings[0]
    assert result["oom_risk"] == "high", result


def test_oom_risk_parallel_collector_is_not_oom_flagged():
    """非 Parallel/G1 collector 不做 OOM 诊断（保持现状）。"""
    events = [
        _make_full_gc("Allocation Failure", 60.0),
    ]
    result = _diagnose_memory(
        events, "CMS", heap_max_mb=4000, max_heap_usage_pct=70.0,
        avg_heap_usage_pct=50.0,
        by_category={"Full": {"count": 1}},
    )
    assert result["oom_risk"] == "none", result
    assert result["leak_risk"] == "none", result


def main():
    test_g1_jdk9_baseline()
    print("g1 jdk9 baseline ok")
    test_jdk8_samples_still_parse()
    print("jdk8 samples ok")
    test_zgc_multiple_pauses_same_gc_id_are_not_deduped()
    print("zgc coverage ok")
    test_shenandoah_concurrent_duration_not_counted_as_pause()
    print("shenandoah coverage ok")
    test_jdk8_g1_sample_recognizes_all_event_types()
    print("jdk8 g1 detailed sample ok")
    test_jdk8_g1_full_gc_with_embedded_concurrent_events_is_full()
    test_jdk8_g1_standalone_concurrent_mark_start_with_heap_delta_is_mixed()
    test_unified_cms_collector_detection()
    print("unified cms detection ok")
    test_jdk8_cms_flag_identifies_cms_collector()
    print("jdk8 g1 full gc with embedded concurrent events ok")
    print("jdk8 g1 standalone concurrent-mark-start with heap delta ok")
    print("jdk8 cms detection ok")
    test_generational_zgc_y_o_prefixes_are_classified()
    print("generational zgc prefixes ok")
    test_jdk8_g1_pause_without_inline_heap_still_becomes_event()
    print("jdk8 g1 no-inline-heap pause ok")
    test_zgc_summary_backfills_heap_to_pause_events()
    print("zgc summary backfill ok")
    test_gc_start_associates_uptime_with_completion_line()
    print("gc,start uptime association ok")
    test_full_gc_start_log_with_intermediate_lines()
    print("full gc,start log ok")
    test_cause_extraction_multiple_parens()
    print("cause extraction multiple parens ok")
    test_cause_extraction_normal_g1()
    print("cause extraction normal G1 ok")
    test_cause_extraction_system_gc()
    print("cause extraction System.gc ok")
    test_gc_marking_sub_phases_not_counted_as_events()
    print("gc marking sub-phases filtered ok")
    test_gc_phases_still_create_events_for_zgc()
    print("gc phases for ZGC still ok")
    test_concurrent_cleanup_for_next_mark_is_concurrent_not_cleanup()
    print("concurrent cleanup classification ok")
    test_oom_risk_g1_single_full_gc_is_medium_not_high()
    print("oom_risk g1 single Full GC = medium ok")
    test_oom_risk_g1_sustained_full_gc_is_high()
    print("oom_risk g1 sustained Full GC = high ok")
    test_oom_risk_single_full_gc_with_high_heap_is_medium_not_high()
    print("oom_risk avg heap 95% + 1 Full GC = medium ok")
    test_oom_risk_max_heap_98_is_high()
    print("oom_risk max heap 98% = high ok")
    test_oom_risk_parallel_collector_is_not_oom_flagged()
    print("oom_risk Parallel (unsupported collector) stays none ok")
    print("\n✅ GC analyzer tests passed")


if __name__ == "__main__":
    main()
