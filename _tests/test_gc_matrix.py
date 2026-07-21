"""GC 大样例矩阵验证：覆盖 JDK8/11/17/21/25 与多种收集器。

这些测试关注稳定的不变量：格式识别、收集器识别、事件存在、统计结构与
暂停时间一致性。大日志不做精确事件数量断言，以免解析能力增强时频繁改测。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from react_agent.gc_analyzer import compute_stats, parse_gc_log, summary_for_llm


BASE = os.path.dirname(__file__)
SERIES_KEYS = {"id", "t", "cat", "before", "after", "total", "dur", "pct"}


GC_FIXTURES = {
    "gc-jdk8-cms.log": {"jdk": "8", "collector": "CMS", "required": {"Young"}, "allowed": {"Young", "Full", "Concurrent", "Remark", "InitialMark"}},
    "gc-jdk8-g1.log": {"jdk": "8", "collector": "G1", "required": {"Young", "Mixed", "Concurrent", "Remark", "Cleanup"}, "allowed": {"Young", "Mixed", "InitialMark", "Concurrent", "Remark", "Cleanup", "Full"}},
    "gc-jdk8-parallel.log": {"jdk": "8", "collector": "Parallel", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk8-serial.log": {"jdk": "8", "collector": "Serial", "required": {"Young"}, "allowed": {"Young", "Full"}},

    "gc-jdk11-cms.log": {"jdk": "9+", "collector": "CMS", "required": {"Young"}, "allowed": {"Young", "Full", "Concurrent", "Remark", "InitialMark"}},
    "gc-jdk11-g1.log": {"jdk": "9+", "collector": "G1", "required": {"Young"}, "allowed": {"Young", "Mixed", "InitialMark", "Concurrent", "Remark", "Cleanup", "Full"}},
    "gc-jdk11-parallel.log": {"jdk": "9+", "collector": "Parallel", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk11-serial.log": {"jdk": "9+", "collector": "Serial", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk11-shenandoah.log": {"jdk": "9+", "collector": "Shenandoah", "required": {"Shenandoah"}, "allowed": {"Shenandoah", "Concurrent", "Other", "Full"}},
    "gc-jdk11-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC", "Concurrent"}, "allowed": {"ZGC", "Concurrent", "Other"}},

    "gc-jdk17-g1.log": {"jdk": "9+", "collector": "G1", "required": {"Young"}, "allowed": {"Young", "Mixed", "InitialMark", "Concurrent", "Remark", "Cleanup", "Full"}},
    "gc-jdk17-parallel.log": {"jdk": "9+", "collector": "Parallel", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk17-serial.log": {"jdk": "9+", "collector": "Serial", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk17-shenandoah.log": {"jdk": "9+", "collector": "Shenandoah", "required": {"Shenandoah"}, "allowed": {"Shenandoah", "Concurrent", "Other", "Full"}},
    "gc-jdk17-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC", "Concurrent"}, "allowed": {"ZGC", "Concurrent", "Other"}},

    "gc-jdk21-g1.log": {"jdk": "9+", "collector": "G1", "required": {"Young"}, "allowed": {"Young", "Mixed", "InitialMark", "Concurrent", "Remark", "Cleanup", "Full"}},
    "gc-jdk21-generational-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC", "Concurrent", "Young"}, "allowed": {"ZGC", "Concurrent", "Other", "Young"}},
    "gc-jdk21-parallel.log": {"jdk": "9+", "collector": "Parallel", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk21-serial.log": {"jdk": "9+", "collector": "Serial", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk21-shenandoah.log": {"jdk": "9+", "collector": "Shenandoah", "required": {"Shenandoah"}, "allowed": {"Shenandoah", "Concurrent", "Other", "Full"}},
    "gc-jdk21-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC", "Concurrent"}, "allowed": {"ZGC", "Concurrent", "Other"}},

    "gc-jdk25-g1.log": {"jdk": "9+", "collector": "G1", "required": {"Young"}, "allowed": {"Young", "Mixed", "InitialMark", "Concurrent", "Remark", "Cleanup", "Full"}},
    "gc-jdk25-generational-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC", "Concurrent", "Young"}, "allowed": {"ZGC", "Concurrent", "Other", "Young"}},
    "gc-jdk25-parallel.log": {"jdk": "9+", "collector": "Parallel", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk25-serial.log": {"jdk": "9+", "collector": "Serial", "required": {"Young"}, "allowed": {"Young", "Full"}},
    "gc-jdk25-shenandoah.log": {"jdk": "9+", "collector": "Shenandoah", "required": {"Shenandoah"}, "allowed": {"Shenandoah", "Concurrent", "Other", "Full"}},
    "gc-jdk25-zgc.log": {"jdk": "9+", "collector": "Z", "required": {"ZGC"}, "allowed": {"ZGC", "Concurrent", "Other", "Young"}},
}


def _load(name: str) -> str:
    with open(os.path.join(BASE, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _assert_fixture(name: str, cfg: dict) -> None:
    parsed = parse_gc_log(_load(name))
    stats = compute_stats(parsed)

    assert parsed["jdk_version"] == cfg["jdk"], name
    assert parsed["collector"] == cfg["collector"], name
    assert stats["collector"] == cfg["collector"], name
    assert len(parsed["events"]) > 0, name
    assert stats["events_total"] == len(parsed["events"]), name
    assert parsed["parsed_lines"] > 0, name
    assert parsed["total_lines"] >= parsed["parsed_lines"], name

    for event in parsed["events"]:
        assert event.category, name
        assert event.cause, name
        assert event.duration_ms >= 0, name

    assert stats["events_total"] > 0, name
    assert stats["total_pause_ms"] >= 0, name
    assert stats["duration_sec"] >= 0, name
    assert stats["events_per_minute"] >= 0, name
    assert stats["by_category"], name
    if stats["heap_max_mb"] is not None:
        assert stats["heap_max_mb"] > 0, name

    categories = set(stats["by_category"].keys())
    assert cfg["required"] <= categories, f"{name}: missing {cfg['required'] - categories}, got {categories}"
    assert categories <= cfg["allowed"], f"{name}: unexpected {categories - cfg['allowed']}, got {categories}"

    if "Concurrent" in stats["by_category"]:
        assert stats["by_category"]["Concurrent"]["total_pause_ms"] == 0, name
    assert all(item["cat"] != "Concurrent" for item in stats["slowest"]), name
    assert all(point["count"] >= 0 for point in stats["frequency_series"]), name
    assert all(SERIES_KEYS <= set(point.keys()) for point in stats["series"]), name

    non_concurrent_pause = sum(
        data["total_pause_ms"]
        for cat, data in stats["by_category"].items()
        if cat != "Concurrent"
    )
    assert abs(stats["total_pause_ms"] - non_concurrent_pause) <= 0.02, name


def test_gc_fixture_matrix():
    for name, cfg in sorted(GC_FIXTURES.items()):
        _assert_fixture(name, cfg)


def test_gc_summary_for_llm_smoke():
    for name in ("gc-jdk8-g1.log", "gc-jdk21-shenandoah.log", "gc-jdk25-generational-zgc.log"):
        stats = compute_stats(parse_gc_log(_load(name)))
        summary = summary_for_llm(stats)
        assert "JDK Version:" in summary, name
        assert "GC Collector:" in summary, name
        assert "Total GC Events:" in summary, name
        assert "Total Pause Time:" in summary, name
        assert "By Category:" in summary, name


def main():
    test_gc_fixture_matrix()
    print("gc fixture matrix ok")
    test_gc_summary_for_llm_smoke()
    print("gc summary smoke ok")
    print("\n✅ GC matrix tests passed")


if __name__ == "__main__":
    main()
