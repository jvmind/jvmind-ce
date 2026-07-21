"""Shared statistics computation for GC analysis (works for both JDK9+ and JDK8 formats)."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import GCEvent


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _linear_slope(values):
    """Compute linear regression slope of values vs index."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator != 0 else 0.0


def _safe_ratio(a, b):
    if b == 0:
        return 0.0
    return a / b


def _get_major_events(events, collector):
    if collector == "Parallel":
        return [e for e in events if e.category == "Full" and e.heap_before_mb > 0 and e.heap_total_mb > 0]
    elif collector == "G1":
        return [e for e in events if e.category in ("Full", "Mixed") and e.heap_before_mb > 0 and e.heap_total_mb > 0]
    return []


def _diagnose_memory(events, collector, heap_max_mb, max_heap_usage_pct, avg_heap_usage_pct, by_category):
    if not collector or collector not in ("Parallel", "G1"):
        return {"leak_risk": "none", "oom_risk": "none", "findings": [], "recommendations_zh": [], "recommendations_en": [], "collector": collector}

    findings = []
    major = _get_major_events(events, collector)

    # ── Rule 1: heap floor rising after major GC ──
    if len(major) >= 3:
        ratios = [_safe_ratio(e.heap_after_mb, e.heap_total_mb) for e in major]
        slope = _linear_slope(ratios)
        n = len(major)
        start_pct = ratios[0] * 100
        end_pct = ratios[-1] * 100
        if slope > 0.002 and ratios[-1] > 0.80:
            findings.append({
                "rule": "heap_floor_rising",
                "severity": "high",
                "title_zh": "Full GC 后堆占用持续升高",
                "title_en": "Heap usage rising after Full GC",
                "detail_zh": f"连续 {n} 次回收，堆占用率从 {start_pct:.0f}% 升至 {end_pct:.0f}%",
                "detail_en": f"Over {n} collections, heap usage rose from {start_pct:.0f}% to {end_pct:.0f}%",
            })
        elif slope > 0.001 and ratios[-1] > 0.70:
            findings.append({
                "rule": "heap_floor_rising",
                "severity": "medium",
                "title_zh": "Full GC 后堆占用缓慢上升",
                "title_en": "Heap usage slowly rising after Full GC",
                "detail_zh": f"连续 {n} 次回收，堆占用率从 {start_pct:.0f}% 升至 {end_pct:.0f}%",
                "detail_en": f"Over {n} collections, heap usage rose from {start_pct:.0f}% to {end_pct:.0f}%",
            })

    # ── Rule 2: declining reclaim effectiveness ──
    if len(major) >= 3:
        reclaims = [_safe_ratio(e.heap_before_mb - e.heap_after_mb, e.heap_before_mb) for e in major]
        avg_reclaim = sum(reclaims) / len(reclaims) if reclaims else 0
        reclaim_slope = _linear_slope(reclaims)
        avg_pct = avg_reclaim * 100
        if avg_reclaim < 0.05:
            severity = "high" if reclaim_slope < -0.002 else "medium"
            trend_label_zh = "持续恶化" if reclaim_slope < -0.002 else "持续偏低"
            trend_label_en = "worsening" if reclaim_slope < -0.002 else "consistently low"
            findings.append({
                "rule": "reclaim_declining",
                "severity": severity,
                "title_zh": "回收效率极低",
                "title_en": "Extremely low reclaim efficiency",
                "detail_zh": f"平均回收率仅 {avg_pct:.1f}%，回收模式{trend_label_zh}",
                "detail_en": f"Average reclaim ratio only {avg_pct:.1f}%, pattern is {trend_label_en}",
            })
        elif reclaim_slope < -0.005:
            findings.append({
                "rule": "reclaim_declining",
                "severity": "medium",
                "title_zh": "回收效率持续下降",
                "title_en": "Reclaim efficiency declining",
                "detail_zh": f"回收率从 {reclaims[0]*100:.0f}% 降至 {reclaims[-1]*100:.0f}%",
                "detail_en": f"Reclaim ratio dropped from {reclaims[0]*100:.0f}% to {reclaims[-1]*100:.0f}%",
            })

    # ── Rule 3: post-GC high heap usage ──
    if len(major) >= 3:
        after_ratios = [_safe_ratio(e.heap_after_mb, e.heap_total_mb) for e in major]
        high_count = sum(1 for r in after_ratios if r > 0.85)
        if high_count >= 3:
            findings.append({
                "rule": "post_gc_high_usage",
                "severity": "medium",
                "title_zh": "回收后堆占用持续高位",
                "title_en": "Persistently high heap usage after GC",
                "detail_zh": f"连续 {high_count} 次回收后堆占用 > 85%，GC 无法有效腾出空间",
                "detail_en": f"Heap usage > 85% after {high_count} consecutive collections, GC cannot free enough space",
            })

    # ── Rule 4: OOM critical ──
    if max_heap_usage_pct is not None and max_heap_usage_pct >= 98:
        findings.append({
            "rule": "oom_critical",
            "severity": "high",
            "title_zh": "堆即将耗尽",
            "title_en": "Heap near exhaustion",
            "detail_zh": f"最大堆占用率达 {max_heap_usage_pct}%，即将发生 OOM",
            "detail_en": f"Max heap usage at {max_heap_usage_pct}%, OOM imminent",
        })
    elif avg_heap_usage_pct is not None and avg_heap_usage_pct >= 95:
        full_count = by_category.get("Full", {}).get("count", 0)
        has_mixed = by_category.get("Mixed", {}).get("count", 0) if collector == "G1" else 0
        if full_count > 0 or has_mixed > 0:
            # Average heap 95% + sustained Full/Mixed GC pressure = real OOM risk.
            # A single Full GC isn't enough to call "high"; require multiple events.
            severity = "high" if (full_count >= 3 or has_mixed >= 5) else "medium"
            findings.append({
                "rule": "oom_critical",
                "severity": severity,
                "title_zh": "高堆占用伴随 Full GC",
                "title_en": "High heap usage with Full GC",
                "detail_zh": f"平均堆占用 {avg_heap_usage_pct}%，且存在 Full GC，OOM 风险高",
                "detail_en": f"Avg heap usage {avg_heap_usage_pct}% with Full GC, high OOM risk",
            })

    # ── Rule 5: Allocation Failure Full GC with heap still full ──
    if heap_max_mb:
        for e in major:
            if e.category == "Full" and "allocation failure" in (e.cause or "").lower():
                if e.heap_total_mb > 0 and e.heap_after_mb / e.heap_total_mb > 0.90:
                    after_pct = e.heap_after_mb / e.heap_total_mb * 100
                    findings.append({
                        "rule": "alloc_failure_full",
                        "severity": "high",
                        "title_zh": "Allocation Failure 触发 Full GC 后堆仍满",
                        "title_en": "Heap still full after Allocation Failure Full GC",
                        "detail_zh": f"GC#{e.id} 因分配失败触发 Full GC，回收后堆占用仍达 {after_pct:.0f}%",
                        "detail_en": f"GC#{e.id}: Full GC triggered by allocation failure, heap still at {after_pct:.0f}%",
                    })
                    break

    # ── Rule 6: G1 Full GC (G1 specific) ──
    if collector == "G1":
        full_events = [e for e in events if e.category == "Full"]
        if full_events:
            n_full = len(full_events)
            # A single G1 Full GC may be intentional (System.gc() etc.) or a one-time event —
            # not necessarily OOM. Sustained Full GC pressure (>=3) is the real red flag.
            severity = "high" if n_full >= 3 else "medium"
            findings.append({
                "rule": "g1_full_gc",
                "severity": severity,
                "title_zh": "G1 发生 Full GC",
                "title_en": "G1 experienced Full GC",
                "detail_zh": f"G1 出现 {n_full} 次 Full GC，这通常是堆配置不足或 Humongous 分配过多的信号",
                "detail_en": f"G1 had {n_full} Full GC(s), which usually indicates insufficient heap or excessive Humongous allocation",
            })

        # ── Rule 7: G1 Mixed GC ineffective ──
        mixed = [e for e in events if e.category == "Mixed" and e.heap_before_mb > 0]
        if len(mixed) >= 3:
            before_mbs = [e.heap_before_mb for e in mixed]
            slope = _linear_slope(before_mbs)
            if slope > 0.5:
                findings.append({
                    "rule": "g1_mixed_ineffective",
                    "severity": "medium",
                    "title_zh": "G1 Mixed GC 回收跟不上晋升速率",
                    "title_en": "G1 Mixed GC cannot keep up with promotion rate",
                    "detail_zh": f"连续 {len(mixed)} 次 Mixed GC 前堆内存持续上升，增量回收不足以控制老年代增长",
                    "detail_en": f"Heap before Mixed GC keeps rising over {len(mixed)} collections, incremental reclamation insufficient",
                })

    # ── Determine risk levels ──
    leak_rules = {"heap_floor_rising", "reclaim_declining", "g1_mixed_ineffective", "post_gc_high_usage"}
    oom_rules = {"oom_critical", "alloc_failure_full", "g1_full_gc"}

    leak_high = [f for f in findings if f["severity"] == "high" and f["rule"] in leak_rules]
    leak_med = [f for f in findings if f["severity"] == "medium" and f["rule"] in leak_rules]
    oom_high = [f for f in findings if f["severity"] == "high" and f["rule"] in oom_rules]
    oom_med = [f for f in findings if f["severity"] == "medium" and f["rule"] in oom_rules]

    leak_risk = "none"
    oom_risk = "none"

    if leak_high:
        leak_risk = "high"
    elif len(leak_med) >= 2:
        leak_risk = "medium"
    elif leak_med:
        leak_risk = "low"

    if oom_high:
        oom_risk = "high"
    elif oom_med:
        oom_risk = "medium"

    # ── Recommendations ──
    recommendations_zh = []
    recommendations_en = []
    if leak_risk in ("high", "medium"):
        recommendations_zh.append("使用 jmap -dump 导出堆转储，用 MAT / JProfiler 分析大对象持有链")
        recommendations_en.append("Use jmap -dump to export heap dump, analyze with MAT / JProfiler")
        recommendations_zh.append("检查代码中是否存在持续增长的静态集合、缓存未设上限、ThreadLocal 未清理")
        recommendations_en.append("Check for unbounded static collections, caches without limits, unclosed ThreadLocal")
    if oom_risk in ("high", "medium"):
        if collector == "G1":
            recommendations_zh.append("考虑增大 -Xmx 或降低 -XX:InitiatingHeapOccupancyPercent 以提前触发 Mixed GC")
            recommendations_en.append("Consider increasing -Xmx or lowering -XX:InitiatingHeapOccupancyPercent to trigger Mixed GC earlier")
        else:
            recommendations_zh.append("考虑增大 -Xmx 堆容量，并排查是否存在内存泄漏导致堆压力")
            recommendations_en.append("Consider increasing -Xmx and investigate potential memory leaks causing heap pressure")
    if any(f["rule"] == "g1_full_gc" for f in findings):
        recommendations_zh.append("G1 发生 Full GC 是严重信号：检查 -XX:G1HeapRegionSize 是否合适，排查 Humongous Allocation")
        recommendations_en.append("G1 Full GC is critical: check -XX:G1HeapRegionSize and investigate Humongous Allocation")
    if any(f["rule"] == "alloc_failure_full" for f in findings):
        recommendations_zh.append("Allocation Failure 导致 Full GC：检查是否存在大对象频繁分配或年轻代配置过小")
        recommendations_en.append("Full GC from Allocation Failure: check for frequent large object allocation or undersized Young Gen")

    return {
        "leak_risk": leak_risk,
        "oom_risk": oom_risk,
        "collector": collector,
        "findings": findings,
        "recommendations_zh": recommendations_zh,
        "recommendations_en": recommendations_en,
    }


def compute_stats(parsed: Dict) -> Dict:
    """基于 parse_gc_log 的结果生成统计摘要 + 时间序列。"""
    events: List[GCEvent] = parsed["events"]
    by_cat: Dict[str, List[GCEvent]] = {}
    for e in events:
        by_cat.setdefault(e.category, []).append(e)

    cat_stats = {}
    for cat, evs in by_cat.items():
        pause_durations = [e.duration_ms for e in evs if e.duration_ms > 0 and not e.is_concurrent]
        freed = [(e.heap_before_mb - e.heap_after_mb) for e in evs if e.heap_before_mb > 0]
        cat_stats[cat] = {
            "count": len(evs),
            "total_pause_ms": round(sum(pause_durations), 3),
            "avg_pause_ms": round(sum(pause_durations) / len(pause_durations), 3) if pause_durations else 0,
            "max_pause_ms": round(max(pause_durations), 3) if pause_durations else 0,
            "p95_pause_ms": round(_percentile(pause_durations, 95), 3),
            "p99_pause_ms": round(_percentile(pause_durations, 99), 3),
            "avg_freed_mb": round(sum(freed) / len(freed), 2) if freed else 0,
            "total_freed_mb": round(sum(freed), 2) if freed else 0,
        }

    # Aggregate by cause (for percentage breakdowns like % of Full GC that is System.gc())
    by_cause: Dict[str, List[GCEvent]] = {}
    for e in events:
        by_cause.setdefault(e.cause, []).append(e)

    cause_stats = {}
    for cause, evs in by_cause.items():
        pause_durations = [e.duration_ms for e in evs if e.duration_ms > 0 and not e.is_concurrent]
        freed = [(e.heap_before_mb - e.heap_after_mb) for e in evs if e.heap_before_mb > 0]
        cause_stats[cause] = {
            "count": len(evs),
            "total_pause_ms": round(sum(pause_durations), 3),
            "avg_pause_ms": round(sum(pause_durations) / len(pause_durations), 3) if pause_durations else 0,
            "max_pause_ms": round(max(pause_durations), 3) if pause_durations else 0,
            "p95_pause_ms": round(_percentile(pause_durations, 95), 3),
            "p99_pause_ms": round(_percentile(pause_durations, 99), 3),
            "avg_freed_mb": round(sum(freed) / len(freed), 2) if freed else 0,
            "total_freed_mb": round(sum(freed), 2) if freed else 0,
        }

    # Aggregate by cause specifically for Full GC events (for percentage breakdown)
    by_cause_full: Dict[str, List[GCEvent]] = {}
    for e in events:
        if e.category == "Full":
            by_cause_full.setdefault(e.cause, []).append(e)

    cause_full_stats = {}
    for cause, evs in by_cause_full.items():
        pause_durations = [e.duration_ms for e in evs if e.duration_ms > 0 and not e.is_concurrent]
        freed = [(e.heap_before_mb - e.heap_after_mb) for e in evs if e.heap_before_mb > 0]
        cause_full_stats[cause] = {
            "count": len(evs),
            "total_pause_ms": round(sum(pause_durations), 3),
            "avg_pause_ms": round(sum(pause_durations) / len(pause_durations), 3) if pause_durations else 0,
            "max_pause_ms": round(max(pause_durations), 3) if pause_durations else 0,
            "p95_pause_ms": round(_percentile(pause_durations, 95), 3),
            "p99_pause_ms": round(_percentile(pause_durations, 99), 3),
            "avg_freed_mb": round(sum(freed) / len(freed), 2) if freed else 0,
            "total_freed_mb": round(sum(freed), 2) if freed else 0,
        }

    all_durations = [e.duration_ms for e in events if e.duration_ms > 0 and not e.is_concurrent]
    total_pause = sum(all_durations)
    duration_sec = 0.0
    if parsed.get("first_uptime") is not None and parsed.get("last_uptime") is not None:
        duration_sec = max(0.0, parsed["last_uptime"] - parsed["first_uptime"])
    throughput = 1.0 - (total_pause / 1000.0) / duration_sec if duration_sec > 0 else None

    # 分配率估算：Σ(本次回收后堆 → 下次回收前堆) 的差值正向部分 / 时间
    alloc_total_mb = 0.0
    prev = None
    for e in events:
        if prev is not None and e.heap_before_mb > prev.heap_after_mb:
            alloc_total_mb += e.heap_before_mb - prev.heap_after_mb
        prev = e
    alloc_rate = alloc_total_mb / duration_sec if duration_sec > 0 else None

    # 时间序列（前端绘图用，最多采样 200 个点保证体积，排除 Concurrent 事件）
    # 强制包含 top 10 by pause time (避免采样错过大 GC 事件造成误导)
    # ZGC/Shenandoah 一个 GC id 可能对应多个暂停阶段 (Pause Mark/Relocate Start 等),
    # 所以用 Python 对象 id 去重, 不要用 e.id 属性 (后者会丢阶段)
    max_rated = parsed.get("heap_max_mb") or 0
    step = max(1, len(events) // 200)
    sampled = [e for e in events[::step] if not e.is_concurrent]
    stw_events = [e for e in events if not e.is_concurrent and e.duration_ms > 0]
    top_pause = sorted(stw_events, key=lambda e: -e.duration_ms)[:10]

    def _to_point(e):
        return {
            "id": e.id,
            "t": round(e.uptime_sec or 0.0, 3),
            "cat": e.category,
            "before": round(e.heap_before_mb, 2),
            "after": round(e.heap_after_mb, 2),
            "total": round(e.heap_total_mb, 2),
            "dur": round(e.duration_ms, 3),
            "pct": round(e.heap_before_mb / max_rated * 100, 1) if max_rated > 0 else None,
        }

    series = []
    seen = set()
    for e in sampled + top_pause:
        oid = id(e)
        if oid in seen:
            continue
        seen.add(oid)
        series.append(_to_point(e))
    series.sort(key=lambda p: p["t"])

    # 计算日志的首个绝对时间戳（用于前端 X 轴显示实际时间）
    start_epoch_ms = None
    for e in events:
        if e.absolute_epoch_ms is not None:
            start_epoch_ms = e.absolute_epoch_ms
            break

    # Top 慢事件（前 10，仅统计 STW 停顿）
    slowest = sorted((e for e in events if not e.is_concurrent), key=lambda x: -x.duration_ms)[:10]
    slowest_list = [{
        "id": e.id, "t": round(e.uptime_sec or 0.0, 3),
        "abs_ms": e.absolute_epoch_ms,
        "cat": e.category, "cause": e.cause,
        "dur": round(e.duration_ms, 3),
        "before": round(e.heap_before_mb, 2),
        "after": round(e.heap_after_mb, 2),
        "raw_type": e.raw_body or e.raw_type,
    } for e in slowest]

    # GC 频率：每分钟事件数
    events_per_minute = round(len(events) / (duration_sec / 60), 2) if duration_sec > 0 else 0

    # 分桶频率序列（前端趋势图用，最多 20 个桶）
    frequency_series = []
    if duration_sec > 0:
        bucket_count = min(20, max(5, len(events) // 50))
        bucket_sec = duration_sec / bucket_count
        for i in range(bucket_count):
            t_start = i * bucket_sec
            t_end = (i + 1) * bucket_sec
            is_last = i == bucket_count - 1
            count = sum(
                1 for e in events
                if e.uptime_sec is not None
                and e.uptime_sec >= t_start
                and (e.uptime_sec <= t_end if is_last else e.uptime_sec < t_end)
            )
            frequency_series.append({
                "t": round(t_start + bucket_sec / 2, 1),
                "count": count,
            })

    # 堆占用百分比统计
    usage_pcts = [e.heap_before_mb / max_rated * 100 for e in events if max_rated > 0 and e.heap_before_mb > 0]
    avg_heap_usage_pct = round(sum(usage_pcts) / len(usage_pcts), 1) if usage_pcts else None
    max_heap_usage_pct = round(max(usage_pcts), 1) if usage_pcts else None

    result = {
        "collector": parsed["collector"],
        "heap_max_mb": round(parsed["heap_max_mb"], 2) if parsed.get("heap_max_mb") else None,
        "duration_sec": round(duration_sec, 3),
        "events_total": len(events),
        "total_pause_ms": round(total_pause, 3),
        "throughput": round(throughput, 5) if throughput is not None else None,
        "avg_alloc_rate_mb_s": round(alloc_rate, 2) if alloc_rate is not None else None,
        "avg_heap_usage_pct": avg_heap_usage_pct,
        "max_heap_usage_pct": max_heap_usage_pct,
        "events_per_minute": events_per_minute,
        "frequency_series": frequency_series,
        "by_category": cat_stats,
        "by_cause": cause_stats,
        "by_cause_full": cause_full_stats,
        "series": series,
        "series_total_stw": len(stw_events),
        "series_sampled_count": len(series),
        "slowest": slowest_list,
        "parsed_lines": parsed["parsed_lines"],
        "total_lines": parsed["total_lines"],
        "jdk_version": parsed.get("jdk_version"),
        "start_epoch_ms": start_epoch_ms,
        "jvm_args": parsed.get("jvm_args"),
    }
    result["events"] = [
        {
            "id": e.id,
            "t": e.uptime_sec,
            "cat": e.category,
            "cause": e.cause,
            "dur": e.duration_ms,
            "before": e.heap_before_mb,
            "after": e.heap_after_mb,
            "total": e.heap_total_mb,
            "raw": e.raw_body,
            "concurrent": e.is_concurrent,
        }
        for e in events
    ]
    result["diagnosis"] = _diagnose_memory(
        events, result["collector"], result["heap_max_mb"],
        result["max_heap_usage_pct"], result["avg_heap_usage_pct"], result["by_category"],
    )
    return result


def summary_for_llm(stats: Dict, max_chars: int = 2500) -> str:
    """Compact GC stats for LLM consumption."""
    jdk_ver = stats.get("jdk_version")
    lines = [
        f"JDK Version: {jdk_ver}" if jdk_ver else "JDK Version: unknown",
        f"GC Collector: {stats['collector']}",
        f"Log Duration: {stats['duration_sec']}s",
        f"Total GC Events: {stats['events_total']}",
        f"Total Pause Time: {stats['total_pause_ms']}ms",
    ]
    if stats.get("heap_max_mb"):
        lines.append(f"Heap Capacity: {stats['heap_max_mb']}MB")
    if stats.get("throughput") is not None:
        lines.append(f"Application Throughput (non-pause ratio): {stats['throughput']*100:.3f}%")
    if stats.get("avg_alloc_rate_mb_s") is not None:
        lines.append(f"Avg Allocation Rate: {stats['avg_alloc_rate_mb_s']}MB/s")
    if stats.get("events_per_minute") is not None:
        lines.append(f"GC Frequency: {stats['events_per_minute']} events/min")

    jvm_args = stats.get("jvm_args")
    if jvm_args:
        lines.append(f"JVM Args: {' '.join(jvm_args)}")

    lines.append("\nBy Category:")
    for cat, s in stats["by_category"].items():
        lines.append(
            f"  - {cat}: count={s['count']}, total_pause={s['total_pause_ms']}ms, "
            f"avg={s['avg_pause_ms']}ms, max={s['max_pause_ms']}ms, "
            f"p95={s['p95_pause_ms']}ms, p99={s['p99_pause_ms']}ms, "
            f"avg_freed={s['avg_freed_mb']}MB"
        )

    if stats["slowest"]:
        lines.append("\nTop 5 Slowest Events:")
        for e in stats["slowest"][:5]:
            lines.append(
                f"  - GC#{e['id']} @{e['t']}s [{e['cat']}] "
                f"{e['before']}MB->{e['after']}MB dur={e['dur']}ms (cause={e['cause']})"
            )

    diagnosis = stats.get("diagnosis")
    if diagnosis:
        dlines = [""]
        dlines.append(f"Memory Diagnosis (Collector: {diagnosis.get('collector', '?')}):")
        dlines.append(f"  Leak Risk: {diagnosis.get('leak_risk', 'none')}")
        dlines.append(f"  OOM Risk: {diagnosis.get('oom_risk', 'none')}")
        findings = diagnosis.get("findings", [])
        if findings:
            dlines.append("  Findings:")
            for f in findings:
                title = f.get("title_en") or f.get("title_zh", "")
                detail = f.get("detail_en") or f.get("detail_zh", "")
                dlines.append(f"    - [{f['severity']}] {title}: {detail}")
        recs = diagnosis.get("recommendations_en") or diagnosis.get("recommendations_zh", [])
        if recs:
            dlines.append("  Recommendations:")
            for r in recs:
                dlines.append(f"    - {r}")
        lines.extend(dlines)

    text = "\n".join(lines)
    if len(text) > max_chars:
        suffix = "\n...(truncated)"
        text = text[:max_chars - len(suffix)] + suffix
    return text
