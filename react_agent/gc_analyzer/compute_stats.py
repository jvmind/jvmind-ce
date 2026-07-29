"""Shared statistics computation for GC analysis (works for both JDK9+ and JDK8 formats)."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import GCEvent


# =============================================================================
# Rule Definitions (metadata only — user-facing strings live in frontend i18n)
# =============================================================================

RULE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "throughput_low": {
        "category": "performance",
        "applies_to": "all",
        "thresholds": {"medium": 0.95, "high": 0.90, "unit": "ratio"},
    },
    "stw_time_ratio_high": {
        "category": "performance",
        "applies_to": "all",
        "thresholds": {"medium": 0.05, "high": 0.10, "unit": "ratio"},
    },
    "gc_frequency_high": {
        "category": "performance",
        "applies_to": "all",
        "thresholds": {
            "young_per_min": {"medium": 30, "high": 60},
            "full_per_min":  {"medium": None, "high": 0.2},
        },
    },
    "reclaim_low": {
        "category": "oom",
        "applies_to": "all",
        "thresholds": {"avg_reclaim_ratio": 0.05, "min_events": 3},
    },
    "explicit_gc_called": {
        "category": "performance",
        "applies_to": "all",
        "thresholds": {"min_count": 1, "high_count": 3},
    },
    "single_pause_long": {
        "category": "performance",
        "applies_to": "all",
        "thresholds": {
            "Young":       {"medium": 200,  "high": 500,  "unit": "ms"},
            "Mixed":       {"medium": 300,  "high": 500,  "unit": "ms"},
            "Remark":      {"medium": 100,  "high": 200,  "unit": "ms"},
            "Cleanup":     {"medium": 100,  "high": 200,  "unit": "ms"},
            "InitialMark": {"medium": 200,  "high": 500,  "unit": "ms"},
            "Full":        {"medium": 1000, "high": 3000, "unit": "ms"},
            "ZGC":         {"medium": 1,    "high": 5,    "unit": "ms"},
            "Shenandoah":  {"medium": 100,  "high": 200,  "unit": "ms"},
        },
    },
    "g1_full_gc": {
        "category": "oom",
        "applies_to": "G1",
        "thresholds": {"n_count_high": 3},
        "extra_signals": ["evacuation_failure", "to_space_exhausted"],
    },
    "g1_mixed_ineffective": {
        "category": "leak",
        "applies_to": "G1",
        "thresholds": {"min_events": 3, "slope_mb_per_event": 0.5},
    },
    "g1_compaction_pause": {
        # G1 Compaction Pause is real heap pressure but NOT necessarily a
        # leak (could be transient burst, cache warmup, or allocation spike).
        # Better classified as performance — visible as a finding but not
        # escalated to leak_risk. The leak_risk should be determined by
        # g1_mixed_ineffective (steady heap growth) + reclaim_low instead.
        "category": "performance",
        "applies_to": "G1",
        # Even 1 G1 Compaction Pause is significant (G1 rarely does this).
        "thresholds": {"min_count": 1, "high_count": 3},
    },
    "evacuation_failure": {
        # Evacuation Failure indicates Old Gen can't hold promoted objects —
        # a precursor to Full GC, but often transient. NOT a leak signal.
        "category": "performance",
        "applies_to": "all",
        # Single Evacuation Failure is sometimes normal (transient old gen full);
        # 3+ starts to indicate systematic pressure. ≥ 10 is severe.
        "thresholds": {"min_count": 3, "high_count": 10},
    },
    "g1_humongous_allocation": {
        "category": "performance",
        "applies_to": "G1",
        # Humongous allocation is common in batch/ETL apps; only alert when sustained.
        "thresholds": {"min_count": 10, "high_count": 30},
    },
    # Phase CMS + ZGC: collector-specific rules (per user principle OOM = Full GC + cannot reclaim)
    "cms_concurrent_mode_failure": {
        "category": "performance",  # CMS 设计的 Full GC fallback; 配合 reclaim_low 才升级为 oom
        "applies_to": "CMS",
        "thresholds": {"min_count": 1},
    },
    "cms_promotion_failed": {
        "category": "performance",
        "applies_to": "CMS",
        "thresholds": {"min_count": 1},
    },
    "cms_remark_too_long": {
        "category": "performance",
        "applies_to": "CMS",
        "thresholds": {"p95_ms_medium": 500, "p95_ms_high": 1000},
    },
    "cms_fragmentation": {
        "category": "leak",
        "applies_to": "CMS",
        "thresholds": {"post_gc_pct_threshold": 0.7, "min_events": 3},
    },
    "zgc_allocation_stall": {
        "category": "oom",  # ZGC 的硬 fallback (堆无法满足分配)
        "applies_to": "Z",
        "thresholds": {"min_count": 1},
    },
    "zgc_pause_exceeds_target": {
        "category": "performance",
        "applies_to": "Z",
        "thresholds": {"p99_ms_medium": 1, "p99_ms_high": 5},
    },
    "zgc_concurrent_cycle_failure": {
        "category": "oom",  # ZGC 同步模式 = OOM 区间
        "applies_to": "Z",
        "thresholds": {"min_count": 1},
    },
}


# =============================================================================
# Rule functions — each returns a list of findings (or empty list)
# Signature: (events, collector, stats) -> List[dict]
# =============================================================================


def _make_finding(rule_id: str, severity: str, title_zh: str, title_en: str,
                  detail_zh: str, detail_en: str) -> Dict[str, Any]:
    return {
        "rule": rule_id,
        "severity": severity,
        "title_zh": title_zh,
        "title_en": title_en,
        "detail_zh": detail_zh,
        "detail_en": detail_en,
    }


def _rule_throughput_low(events, collector, stats) -> List[Dict[str, Any]]:
    tp = stats.get("throughput")
    if tp is None:
        return []
    th = RULE_DEFINITIONS["throughput_low"]["thresholds"]
    if tp < th["high"]:
        return [_make_finding(
            "throughput_low", "high",
            f"应用吞吐率仅 {tp*100:.1f}%",
            f"Application throughput only {tp*100:.1f}%",
            f"吞吐率（=1 - 暂停占比）{tp*100:.1f}%，低于 90% 高风险阈值",
            f"Throughput (1 - pause ratio) {tp*100:.1f}%, below 90% high-risk threshold",
        )]
    if tp < th["medium"]:
        return [_make_finding(
            "throughput_low", "medium",
            f"应用吞吐率 {tp*100:.1f}%",
            f"Application throughput {tp*100:.1f}%",
            f"吞吐率（=1 - 暂停占比）{tp*100:.1f}%，低于 95% 警告阈值",
            f"Throughput {tp*100:.1f}%, below 95% warning threshold",
        )]
    return []


def _rule_stw_time_ratio_high(events, collector, stats) -> List[Dict[str, Any]]:
    tp = stats.get("throughput")
    if tp is None:
        return []
    ratio = 1.0 - tp
    th = RULE_DEFINITIONS["stw_time_ratio_high"]["thresholds"]
    if ratio > th["high"]:
        return [_make_finding(
            "stw_time_ratio_high", "high",
            f"GC 暂停时间占比 {ratio*100:.1f}%",
            f"GC pause time ratio {ratio*100:.1f}%",
            f"GC 暂停时间占日志时长 {ratio*100:.1f}%，超过 10% 高风险阈值",
            f"GC pauses consume {ratio*100:.1f}% of log duration, exceeding 10% high-risk threshold",
        )]
    if ratio > th["medium"]:
        return [_make_finding(
            "stw_time_ratio_high", "medium",
            f"GC 暂停时间占比 {ratio*100:.1f}%",
            f"GC pause time ratio {ratio*100:.1f}%",
            f"GC 暂停时间占日志时长 {ratio*100:.1f}%，超过 5% 警告阈值",
            f"GC pauses consume {ratio*100:.1f}% of log duration, exceeding 5% warning threshold",
        )]
    return []


def _is_manual_full_gc(event) -> bool:
    """Return True if the Full GC was triggered manually (not by heap pressure).

    Manual triggers include:
    - System.gc() — application code explicitly calling System.gc()
    - Heap Dump Initiated GC — jmap -dump / jcmd GC.heap_dump
    - Heap Inspection — jcmd inspection

    These are NOT heap pressure signals; they should not contribute to
    Full GC frequency metrics (which indicate real heap pressure).
    """
    text = ((event.raw_body or "") + " " + (event.cause or "")).lower()
    return any(marker in text for marker in (
        "system.gc()", "heap dump initiated gc", "heap inspection",
    ))


def _rule_gc_frequency_high(events, collector, stats) -> List[Dict[str, Any]]:
    epm = stats.get("events_per_minute")
    if epm is None:
        return []
    by_cat = stats.get("by_category", {}) or {}
    young_cat = by_cat.get("Young", {}) or {}
    full_cat = by_cat.get("Full", {}) or {}
    duration_sec = stats.get("duration_sec") or 0
    th = RULE_DEFINITIONS["gc_frequency_high"]["thresholds"]

    findings: List[Dict[str, Any]] = []
    young_count = young_cat.get("count", 0)
    full_count = full_cat.get("count", 0)

    # Young GC frequency (per-minute)
    young_per_min = (young_count / duration_sec * 60) if duration_sec > 0 else 0
    if young_per_min >= th["young_per_min"]["high"] and young_count >= 3:
        findings.append(_make_finding(
            "gc_frequency_high", "high",
            f"Young GC 频率过高 ({young_per_min:.0f} 次/分钟)",
            f"Young GC frequency too high ({young_per_min:.0f}/min)",
            f"Young GC {young_per_min:.0f} 次/分钟，{young_count} 次事件，超 60 次/分钟高风险阈值",
            f"{young_count} Young GCs at {young_per_min:.0f}/min, exceeding 60/min high-risk threshold",
        ))
    elif young_per_min >= th["young_per_min"]["medium"] and young_count >= 3:
        findings.append(_make_finding(
            "gc_frequency_high", "medium",
            f"Young GC 频率较高 ({young_per_min:.0f} 次/分钟)",
            f"Young GC frequency elevated ({young_per_min:.0f}/min)",
            f"Young GC {young_per_min:.0f} 次/分钟，超 30 次/分钟警告阈值",
            f"Young GCs at {young_per_min:.0f}/min, exceeding 30/min warning threshold",
        ))

    # Full GC frequency (per-minute) — only count REAL (non-manual) Full GC.
    # Manual Full GC (System.gc() / heap dump / heap inspection) are handled
    # by explicit_gc_called and should not inflate gc_frequency_high.
    #
    # Sample-size-aware severity (statistical confidence):
    # - count < 3: don't fire (single event is noise)
    # - count 3-4 + rate >= threshold: medium (rate high but small sample;
    #   transient burst possible in short logs)
    # - count >= 5 + rate >= threshold: high (confident sustained pressure)
    # This prevents over-alerting on short log windows where 1-3 events look
    # like a high per-minute rate but may be transient.
    real_full_events = [e for e in events
                        if e.category == "Full" and not _is_manual_full_gc(e)]
    real_full_count = len(real_full_events)
    real_full_per_min = (real_full_count / duration_sec * 60) if duration_sec > 0 else 0
    rate_threshold = th["full_per_min"]["high"]

    # Determine severity based on sample size + rate
    severity = None
    if real_full_count >= 5 and real_full_per_min >= rate_threshold:
        severity = "high"
    elif real_full_count >= 3 and real_full_per_min >= rate_threshold:
        severity = "medium"

    if severity:
        # Mention manual count for context
        manual_note = ""
        if full_count > real_full_count:
            manual_n = full_count - real_full_count
            manual_note_zh = f"（另有 {manual_n} 次手动触发）"
            manual_note_en = f" ({manual_n} manual trigger{'s' if manual_n > 1 else ''} excluded)"
        else:
            manual_note_zh = manual_note_en = ""
        # Duration context for short logs (helps user judge transient vs sustained)
        duration_note = ""
        if duration_sec > 0 and duration_sec < 60 and real_full_count < 5:
            duration_note_zh = f"日志仅 {duration_sec:.0f}s, 样本量小, 可能是瞬时高峰"
            duration_note_en = f"log spans only {duration_sec:.0f}s — small sample, may be transient"
        else:
            duration_note_zh = duration_note_en = ""

        findings.append(_make_finding(
            "gc_frequency_high", severity,
            f"Full GC 频率过高 ({real_full_per_min:.2f} 次/分钟)",
            f"Full GC frequency too high ({real_full_per_min:.2f}/min)",
            (f"Full GC {real_full_count} 次，平均 {real_full_per_min:.2f} 次/分钟"
             f"（约 {60/real_full_per_min:.0f} 秒 1 次）{manual_note_zh}。"
             f"{duration_note_zh}"),
            (f"{real_full_count} Full GCs, ~{60/real_full_per_min:.0f}s apart{manual_note_en}. "
             f"{duration_note_en}"),
        ))

    return findings


def _rule_reclaim_low(events, collector, stats) -> List[Dict[str, Any]]:
    by_cat = stats.get("by_category", {}) or {}
    findings: List[Dict[str, Any]] = []
    th = RULE_DEFINITIONS["reclaim_low"]["thresholds"]

    for cat_name in ("Full", "Mixed"):
        cat = by_cat.get(cat_name, {}) or {}
        if cat.get("count", 0) < th["min_events"]:
            continue
        avg_freed = cat.get("avg_freed_mb", 0) or 0
        avg_before = cat.get("avg_pause_ms", 0)  # not used; we use raw events below
        # Compute avg reclaim from raw events for accuracy
        cat_events = [e for e in events
                      if e.category == cat_name
                      and e.heap_before_mb > 0
                      and e.heap_after_mb >= 0]
        if not cat_events:
            continue
        reclaims = [(e.heap_before_mb - e.heap_after_mb) / e.heap_before_mb
                    for e in cat_events
                    if e.heap_before_mb > 0]
        if not reclaims:
            continue
        avg_reclaim = sum(reclaims) / len(reclaims)
        if avg_reclaim >= th["avg_reclaim_ratio"]:
            continue
        # Severity logic:
        #   - Very low (< 2%): always high (heap nearly un-reclaimable = OOM territory)
        #   - Declining trend (slope < -0.002): high (worsening)
        #   - Otherwise: medium (consistently low)
        slope = _linear_slope(reclaims)
        if avg_reclaim <= 0.02:
            severity = "high"
            trend_zh, trend_en = "回收空间几乎为零", "near-zero reclamation"
        elif slope < -0.002:
            severity = "high"
            trend_zh, trend_en = "持续恶化", "worsening"
        else:
            severity = "medium"
            trend_zh, trend_en = "持续偏低", "consistently low"
        findings.append(_make_finding(
            "reclaim_low", severity,
            f"{cat_name} GC 回收率极低",
            f"Low {cat_name} GC reclaim ratio",
            f"{len(reclaims)} 次 {cat_name} GC 平均回收率仅 {avg_reclaim*100:.1f}%，{trend_zh}",
            f"Average reclaim over {len(reclaims)} {cat_name} GCs only {avg_reclaim*100:.1f}%, {trend_en}",
        ))
    return findings


def _rule_explicit_gc_called(events, collector, stats) -> List[Dict[str, Any]]:
    """Detect Full GC triggered by System.gc() (application code call).

    Universal: applies to all collectors.
    Performance category: code smell — production code should consider
    -XX:+DisableExplicitGC to disable explicit GC calls.
    """
    findings: List[Dict[str, Any]] = []
    explicit_count = 0
    for e in events:
        if e.category != "Full":
            continue
        rb = ((e.raw_body or "") + " " + (e.cause or "")).lower()
        # Match exact "System.gc()" or " system.gc() " token; the cause field
        # in JDK9+ logs is exactly "System.gc()", while JDK8 puts it after [GC
        # (System.gc()) ...]. Both contain the literal substring.
        if "system.gc()" in rb:
            explicit_count += 1
    if explicit_count == 0:
        return []
    th = RULE_DEFINITIONS["explicit_gc_called"]["thresholds"]
    severity = "high" if explicit_count >= th["high_count"] else "medium"
    findings.append(_make_finding(
        "explicit_gc_called", severity,
        "应用代码调用了 System.gc()",
        "Application code called System.gc()",
        (f"Full GC 触发原因: System.gc()，共 {explicit_count} 次。"
         f"生产环境应考虑 -XX:+DisableExplicitGC 禁用显式 GC 调用"),
        (f"Full GC triggered by System.gc() ({explicit_count} times). "
         f"Production code should consider -XX:+DisableExplicitGC to disable explicit GC"),
    ))
    return findings


def _rule_single_pause_long(events, collector, stats) -> List[Dict[str, Any]]:
    """Category-aware thresholds; one finding per category that has offenders, capped at 3."""
    thresholds = RULE_DEFINITIONS["single_pause_long"]["thresholds"]
    stw_events = [e for e in events
                  if not e.is_concurrent
                  and e.duration_ms > 0
                  and e.category in thresholds]
    if not stw_events:
        return []
    # Group by category, find max duration per category
    by_cat: Dict[str, List[GCEvent]] = {}
    for e in stw_events:
        by_cat.setdefault(e.category, []).append(e)
    findings: List[Dict[str, Any]] = []
    for cat, evs in by_cat.items():
        evs_sorted = sorted(evs, key=lambda e: -e.duration_ms)
        worst = evs_sorted[0]
        th = thresholds[cat]
        if worst.duration_ms > th["high"]:
            severity = "high"
        elif worst.duration_ms > th["medium"]:
            severity = "medium"
        else:
            continue
        over_count = sum(1 for e in evs if e.duration_ms > th["medium"])
        findings.append(_make_finding(
            "single_pause_long", severity,
            f"{cat} GC 出现 {over_count} 次长暂停",
            f"{over_count} long {cat} pauses detected",
            f"GC#{worst.id} {cat} 停顿 {worst.duration_ms:.0f}ms，超过 {cat} 阈值 "
            f"({th['medium']}ms 中 / {th['high']}ms 高)",
            f"GC#{worst.id} {cat} paused {worst.duration_ms:.0f}ms, exceeds {cat} threshold "
            f"({th['medium']}ms medium / {th['high']}ms high)",
        ))
        if len(findings) >= 3:
            break
    return findings


def _rule_g1_full_gc(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "G1":
        return []
    full_events = [e for e in events if e.category == "Full"]
    if not full_events:
        return []

    n_full = len(full_events)
    severity = "high" if n_full >= RULE_DEFINITIONS["g1_full_gc"]["thresholds"]["n_count_high"] else "medium"

    # Detect extra signals (Evacuation Failure / to-space exhausted)
    extra_signals = []
    # Detect deliberate / manual Full GC triggers that should NOT be
    # misread as heap pressure. JVM emits these causes when the Full GC is
    # the JVM's response to a heap dump / inspection request, or an explicit
    # System.gc() call — the user did this on purpose, the pause is not a
    # heap-health signal.
    manual_dump_count = 0
    manual_inspect_count = 0
    system_gc_count = 0
    metadata_gc_count = 0
    last_ditch_count = 0
    for e in full_events:
        rb = (e.raw_body or "") + " " + (e.cause or "")
        rb_l = rb.lower()
        if "heap dump initiated gc" in rb_l:
            manual_dump_count += 1
        elif "heap inspection" in rb_l:
            # Matches both "Heap Inspection" (G1 modern) and "Heap Inspection
            # Initiated GC" (CMS classic). Both are user-triggered inspection.
            manual_inspect_count += 1
        elif "system.gc()" in rb_l or rb_l.strip() == "system.gc()":
            system_gc_count += 1
        elif "metadata gc threshold" in rb_l:
            metadata_gc_count += 1
        elif "last ditch collection" in rb_l:
            last_ditch_count += 1
        if "to-space exhausted" in rb_l or "to space exhausted" in rb_l:
            extra_signals.append("to-space exhausted")
            break
        if "evacuation failure" in rb_l or "evacuation failed" in rb_l:
            extra_signals.append("evacuation failure")
            break

    # Aggregate: manual triggers (dump + inspect + System.gc()) are not
    # heap pressure. Note: System.gc() is an application code call (not SRE
    # operation like dump/inspect), so the dedicated explicit_gc_called
    # rule will also fire separately with a code-smell recommendation.
    manual_total = manual_dump_count + manual_inspect_count + system_gc_count
    # Non-manual = real heap pressure events
    non_manual = n_full - manual_total

    if manual_total > 0 and manual_total == n_full:
        # All Full GCs in this log were caused by manual triggers
        causes = []
        if manual_dump_count:
            causes.append("heap dump (jmap -dump / jcmd GC.heap_dump)")
        if manual_inspect_count:
            causes.append("heap inspection (jcmd inspection)")
        if system_gc_count:
            causes.append("System.gc() (application code call)")
        cause_text = " + ".join(causes)
        detail_zh = (f"G1 出现 {n_full} 次 Full GC，均由手动操作触发"
                     f"（{cause_text}），属预期操作而非堆压力")
        detail_en = (f"G1 had {n_full} Full GC(s), all triggered by manual "
                     f"{cause_text} — intentional, not heap pressure")
        title_zh = "G1 Full GC 由手动操作触发"
        title_en = "G1 Full GC triggered by manual operation"
        # Manual triggers are not heap pressure signals — cap severity at
        # medium even if count >= 3, so we don't escalate the OOM banner
        # just because someone ran jmap / jcmd or application code called
        # System.gc().
        if n_full >= RULE_DEFINITIONS["g1_full_gc"]["thresholds"]["n_count_high"]:
            severity = "medium"
    elif manual_total > 0 and non_manual > 0:
        # Mixed: some manual, some real heap pressure. Break down both counts
        # so the user understands the diagnosis correctly.
        manual_parts = []
        if manual_dump_count:
            manual_parts.append(f"{manual_dump_count} dump")
        if manual_inspect_count:
            manual_parts.append(f"{manual_inspect_count} inspect")
        if system_gc_count:
            manual_parts.append(f"{system_gc_count} System.gc()")
        manual_text = " + ".join(manual_parts)
        detail_zh = (f"G1 出现 {n_full} 次 Full GC：{manual_total} 次手动"
                     f"（{manual_text}），{non_manual} 次真实堆压力 (G1 Compaction Pause 等)。"
                     f"手动部分非堆压力, 真实压力部分需关注")
        detail_en = (f"G1 had {n_full} Full GC(s): {manual_total} manual "
                     f"({manual_text}) and {non_manual} from real heap pressure "
                     f"(e.g. G1 Compaction Pause). Manual triggers are not "
                     f"heap pressure; the {non_manual} real-pressure events need attention")
        title_zh = "G1 Full GC 混合触发"
        title_en = "G1 Full GC: mixed manual + heap pressure"
        if n_full >= RULE_DEFINITIONS["g1_full_gc"]["thresholds"]["n_count_high"]:
            severity = "high"  # real pressure events present
    elif extra_signals:
        detail_zh = (f"G1 出现 {n_full} 次 Full GC，其中检测到 {extra_signals[0]}，"
                     f"通常为堆容量不足或 Humongous 分配过多")
        detail_en = (f"G1 had {n_full} Full GC(s), detected {extra_signals[0]}; "
                     f"usually indicates insufficient heap or excessive Humongous allocation")
        title_zh = f"G1 发生 Full GC（含 {extra_signals[0]}）"
        title_en = f"G1 experienced Full GC (incl. {extra_signals[0]})"
    else:
        detail_zh = f"G1 出现 {n_full} 次 Full GC，这通常是堆配置不足或 Humongous 分配过多的信号"
        detail_en = f"G1 had {n_full} Full GC(s), which usually indicates insufficient heap or excessive Humongous allocation"
        title_zh = "G1 发生 Full GC"
        title_en = "G1 experienced Full GC"

    return [_make_finding("g1_full_gc", severity, title_zh, title_en, detail_zh, detail_en)]


def _rule_g1_compaction_pause(events, collector, stats) -> List[Dict[str, Any]]:
    """Detect G1 Compaction Pause (G1主动全堆压缩, 真堆压力信号)。

    G1 正常通过 Mixed GC 回收老年代。当 Mixed GC 无法跟上晋升速率,
    G1 会切换到全堆压缩 (compaction) 模式, 触发 Pause Full (G1 Compaction Pause)。
    这是真实的内存压力信号, 表示堆空间被占满且无法正常回收。

    Performance category: 1-2 是 medium, ≥ 3 是 high。
    """
    if collector != "G1":
        return []
    compaction_count = sum(
        1 for e in events
        if e.category == "Full"
        and "g1 compaction pause" in ((e.raw_body or "") + " " + (e.cause or "")).lower()
    )
    th = RULE_DEFINITIONS["g1_compaction_pause"]["thresholds"]
    if compaction_count < th["min_count"]:
        return []
    severity = "high" if compaction_count >= th["high_count"] else "medium"
    return [_make_finding(
        "g1_compaction_pause", severity,
        "G1 Compaction Pause (全堆压缩)",
        "G1 Compaction Pause (full heap compaction)",
        f"G1 主动全堆压缩 {compaction_count} 次, 表示 Mixed GC 已无法跟上晋升速率, 堆空间被占满",
        f"G1 triggered full heap compaction {compaction_count} time(s) — "
        f"Mixed GC cannot keep up with promotion, heap is full",
    )]


def _rule_evacuation_failure(events, collector, stats) -> List[Dict[str, Any]]:
    """Detect frequent Evacuation Failure during Young/Mixed GC.

    Evacuation Failure = G1 在年轻代/混合回收时, 找不到 Survivor 空间
    容纳晋升对象。这是老年代空间不足的早期信号, 通常先于 Full GC。
    """
    failure_count = sum(
        1 for e in events
        if e.category in ("Young", "Mixed")
        and ("evacuation failure" in ((e.raw_body or "") + " " + (e.cause or "")).lower()
             or "evacuation failed" in ((e.raw_body or "") + " " + (e.cause or "")).lower())
    )
    th = RULE_DEFINITIONS["evacuation_failure"]["thresholds"]
    if failure_count < th["min_count"]:
        return []
    severity = "high" if failure_count >= th["high_count"] else "medium"
    return [_make_finding(
        "evacuation_failure", severity,
        "Evacuation Failure 频繁",
        "Frequent Evacuation Failure",
        f"G1 Young/Mixed GC 阶段找不到 Survivor 空间容纳晋升对象 {failure_count} 次, "
        f"通常意味着老年代已满, 是 Full GC 的前兆",
        f"G1 Young/Mixed GC cannot find Survivor space for promoted objects "
        f"({failure_count} times) — usually indicates Old Gen is full, "
        f"a precursor to Full GC",
    )]


def _rule_g1_humongous_allocation(events, collector, stats) -> List[Dict[str, Any]]:
    """Detect frequent G1 Humongous Allocation.

    Humongous objects (≥ region size / 2) 在 G1 中特殊处理, 直接分配到老年代。
    频繁的 humongous allocation 暗示业务层有大量大对象, 会导致:
    - 老年代碎片化 (humongous regions 不可被普通 Mixed GC 回收)
    - 频繁的 Full GC
    """
    if collector != "G1":
        return []
    # Humongous Allocation appears in both Young GC and Mixed GC body. G1
    # Mixed GC handles humongous regions in old gen — when humongous is in
    # the body of a Mixed event, it's still humongous allocation pressure.
    # Counting only Young misses the Mixed case (production report 6dc3798718:
    # 91 humongous events, 43 in Young + 48 in Mixed → old rule only saw 43).
    humongous_count = sum(
        1 for e in events
        if e.category in ("Young", "Mixed")
        and "g1 humongous allocation" in ((e.raw_body or "") + " " + (e.cause or "")).lower()
    )
    th = RULE_DEFINITIONS["g1_humongous_allocation"]["thresholds"]
    if humongous_count < th["min_count"]:
        return []
    severity = "high" if humongous_count >= th["high_count"] else "medium"
    return [_make_finding(
        "g1_humongous_allocation", severity,
        "G1 Humongous Allocation 频繁",
        "Frequent G1 Humongous Allocation",
        f"频繁的大对象分配 {humongous_count} 次, G1 需为每个 humongous object 分配完整 region, "
        f"可能造成老年代碎片化和 Full GC",
        f"Frequent large-object allocation ({humongous_count} times) — "
        f"G1 allocates a full region per humongous object, "
        f"can fragment Old Gen and trigger Full GC",
    )]


def _rule_g1_mixed_ineffective(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "G1":
        return []
    th = RULE_DEFINITIONS["g1_mixed_ineffective"]["thresholds"]
    mixed = [e for e in events if e.category == "Mixed" and e.heap_before_mb > 0]
    if len(mixed) < th["min_events"]:
        return []
    before_mbs = [e.heap_before_mb for e in mixed]
    slope = _linear_slope(before_mbs)
    if slope <= th["slope_mb_per_event"]:
        return []
    return [_make_finding(
        "g1_mixed_ineffective", "medium",
        "G1 Mixed GC 回收跟不上晋升速率",
        "G1 Mixed GC cannot keep up with promotion rate",
        f"连续 {len(mixed)} 次 Mixed GC 前堆内存持续上升（{slope:.2f} MB/事件），"
        f"增量回收不足以控制老年代增长",
        f"Heap before Mixed GC keeps rising over {len(mixed)} collections ({slope:.2f} MB/event), "
        f"incremental reclamation insufficient",
    )]


# =============================================================================
# CMS-specific rules
# =============================================================================


def _rule_cms_concurrent_mode_failure(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "CMS":
        return []
    findings = []
    for e in events:
        if e.category != "Full":
            continue
        text = ((e.cause or "") + " " + (e.raw_body or "")).lower()
        if "concurrent mode failure" in text:
            findings.append(_make_finding(
                "cms_concurrent_mode_failure", "high",
                "CMS 出现 Concurrent Mode Failure",
                "CMS concurrent mode failure",
                f"Full GC 触发原因: {e.cause or 'concurrent mode failure'}, CMS 并发回收失败 fallback 到 Full GC, 堆空间不足",
                f"Full GC triggered by CMS concurrent mode failure — concurrent cycle could not complete, heap exhausted",
            ))
            break
    return findings


def _rule_cms_promotion_failed(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "CMS":
        return []
    findings = []
    for e in events:
        if e.category != "Full":
            continue
        text = ((e.cause or "") + " " + (e.raw_body or "")).lower()
        if "promotion failed" in text or "promotion failure" in text:
            findings.append(_make_finding(
                "cms_promotion_failed", "high",
                "CMS Promotion Failed",
                "CMS promotion failed",
                f"Full GC 触发原因: {e.cause or 'promotion failed'}, 年轻代对象无法晋升到老年代",
                f"Full GC triggered by CMS promotion failure — Young Gen objects cannot fit into Old Gen",
            ))
            break
    return findings


def _rule_cms_remark_too_long(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "CMS":
        return []
    by_cat = stats.get("by_category", {}) or {}
    remark_cat = by_cat.get("Remark", {}) or {}
    if remark_cat.get("count", 0) < 1:
        return []
    p95 = remark_cat.get("p95_pause_ms", 0) or 0
    th = RULE_DEFINITIONS["cms_remark_too_long"]["thresholds"]
    if p95 <= th["p95_ms_medium"]:
        return []
    if p95 > th["p95_ms_high"]:
        severity = "high"
        detail_zh = f"Remark 阶段 p95 = {p95:.0f}ms (> {th['p95_ms_high']}ms 阈值)"
        detail_en = f"Remark p95 = {p95:.0f}ms (> {th['p95_ms_high']}ms threshold)"
    else:
        severity = "medium"
        detail_zh = f"Remark 阶段 p95 = {p95:.0f}ms (> {th['p95_ms_medium']}ms 阈值)"
        detail_en = f"Remark p95 = {p95:.0f}ms (> {th['p95_ms_medium']}ms threshold)"
    return [_make_finding(
        "cms_remark_too_long", severity,
        "CMS Remark 阶段过长",
        "CMS remark phase too long",
        detail_zh, detail_en,
    )]


def _rule_cms_fragmentation(events, collector, stats) -> List[Dict[str, Any]]:
    """CMS 老年代碎片化: Full GC + post-GC 仍 < 70% (堆不挤但仍 Full GC = 碎片化)。

    区别于 reclaim_low: reclaim_low 是堆挤腾不出 (post-GC >= 70%, 实际回收少);
    cms_fragmentation 是堆空但无法分配连续块 (post-GC < 70%, Full GC 仍触发)。
    """
    if collector != "CMS":
        return []
    full_events = [e for e in events if e.category == "Full" and e.heap_total_mb > 0]
    if len(full_events) < 3:
        return []
    post_pcts = [(e.heap_after_mb / e.heap_total_mb) for e in full_events]
    avg_post = sum(post_pcts) / len(post_pcts)
    threshold = RULE_DEFINITIONS["cms_fragmentation"]["thresholds"]["post_gc_pct_threshold"]
    if avg_post >= threshold:
        return []
    return [_make_finding(
        "cms_fragmentation", "medium",
        "CMS 堆碎片化",
        "CMS heap fragmentation",
        f"Full GC 后堆仅占用 {avg_post*100:.0f}%, 大量空闲却被碎片化阻塞",
        f"Full GC leaves heap at {avg_post*100:.0f}% occupied — fragmented free space cannot satisfy allocations",
    )]


# =============================================================================
# ZGC-specific rules
# =============================================================================


def _has_nonzero_allocation_stall(text: str) -> bool:
    """Check if any 'Allocation Stall(s)' line in text has a non-zero value.

    ZGC logs report allocation stalls in three distinct forms:
      Pattern A: per-phase tracking, e.g. "Y: Allocation Stalls: 0 19 0 0"
                 (4 numbers: Mark Start / Mark End / Relocate Start / Relocate End)
      Pattern B: stats summary, e.g. "Critical: Allocation Stall 0.000 / 0.000"
                 (slash-separated totals per phase)
      Pattern C: event cause, e.g. cause="Allocation Stall" (sync mode event)

    Plain substring "allocation stall" matching fires on Pattern A / B header
    text even when all values are zero, producing false positives on every
    ZGC log (production report gc-jdk25-zgc-finagle-http.log: 63 cycles,
    all zero, yet old rule fired). This helper requires at least one non-zero
    digit before declaring a stall.
    """
    # Pattern A: "Allocation Stalls: 0 19 0 0" — capture numbers after colon
    for nums_str in re.findall(r"allocation stalls?:\s*([\d\s]+?)(?:\n|$)", text):
        nums = [int(x) for x in nums_str.split() if x.isdigit()]
        if any(n > 0 for n in nums):
            return True
    # Pattern B: "Critical: Allocation Stall X.000 / Y.000 ..." — capture total
    for m in re.finditer(
        r"critical:\s*allocation stall[^\n]*?(\d+(?:\.\d+)?)\s*/\s*\d+(?:\.\d+)?",
        text,
    ):
        if float(m.group(1)) > 0:
            return True
    # Pattern C: singular "Allocation Stall" without trailing 's' or ':' — sync mode.
    # Exclude when preceded by "critical:" (already handled by Pattern B) and
    # when preceded by "y:" or "o:" prefix on per-phase tracking lines
    # (e.g. "Y: Allocation Stalls: 0 0 0 0" — plural form, handled by Pattern A).
    if re.search(r"(?<!critical:\s)\b(?:y:|o:)?\s*\ballocation stall\b(?!s|:)", text):
        return True
    return False


def _rule_zgc_allocation_stall(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "Z":
        return []
    findings = []
    for e in events:
        text = ((e.cause or "") + " " + (e.raw_body or "")).lower()
        if _has_nonzero_allocation_stall(text):
            finding = _make_finding(
                "zgc_allocation_stall", "high",
                "ZGC 出现 Allocation Stall",
                "ZGC allocation stall",
                f"ZGC 分配被 GC 阻塞 (Allocation Stall), 堆空间不足无法满足分配请求",
                f"ZGC allocation stall — heap cannot satisfy allocation request, ZGC sync mode engaged",
            )
            # Surface the specific triggering event so the frontend can show
            # "GC(N) at uptime Xs" instead of just the aggregate finding.
            # Critical for incident triage: user needs to know WHICH GC stalled,
            # not just that one did.
            finding["event"] = {
                "id": e.id,
                "uptime_sec": e.uptime_sec,
                "absolute_epoch_ms": e.absolute_epoch_ms,
                "category": e.category,
                "cause": e.cause or "",
                "duration_ms": e.duration_ms,
            }
            findings.append(finding)
            break
    return findings


def _rule_zgc_pause_exceeds_target(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "Z":
        return []
    by_cat = stats.get("by_category", {}) or {}
    zgc_cat = by_cat.get("ZGC", {}) or {}
    if zgc_cat.get("count", 0) < 1:
        return []
    p99 = zgc_cat.get("p99_pause_ms", 0) or 0
    th = RULE_DEFINITIONS["zgc_pause_exceeds_target"]["thresholds"]
    if p99 <= th["p99_ms_medium"]:
        return []
    if p99 > th["p99_ms_high"]:
        severity = "high"
        detail_zh = f"ZGC p99 = {p99:.1f}ms (> {th['p99_ms_high']}ms 阈值)"
        detail_en = f"ZGC p99 = {p99:.1f}ms (> {th['p99_ms_high']}ms threshold)"
    else:
        severity = "medium"
        detail_zh = f"ZGC p99 = {p99:.1f}ms (> {th['p99_ms_medium']}ms 阈值)"
        detail_en = f"ZGC p99 = {p99:.1f}ms (> {th['p99_ms_medium']}ms threshold)"
    return [_make_finding(
        "zgc_pause_exceeds_target", severity,
        "ZGC 暂停时间超过目标",
        "ZGC pause exceeds target",
        detail_zh, detail_en,
    )]


def _rule_zgc_concurrent_cycle_failure(events, collector, stats) -> List[Dict[str, Any]]:
    if collector != "Z":
        return []
    findings = []
    for e in events:
        text = ((e.cause or "") + " " + (e.raw_body or "")).lower()
        if "concurrent cycle failure" in text or "concurrent failure" in text:
            findings.append(_make_finding(
                "zgc_concurrent_cycle_failure", "high",
                "ZGC 并发循环失败",
                "ZGC concurrent cycle failure",
                "ZGC 并发回收循环失败, 通常因堆不足或分配速率过高",
                "ZGC concurrent cycle failed — typically heap insufficient or allocation rate too high",
            ))
            break
    return findings


# Rule registry: (collector_filter_or_None, fn)
RULES: List[Tuple[Optional[str], Callable]] = [
    ("G1", _rule_g1_full_gc),
    ("G1", _rule_g1_mixed_ineffective),
    ("G1", _rule_g1_compaction_pause),
    (None, _rule_evacuation_failure),
    ("G1", _rule_g1_humongous_allocation),
    (None, _rule_throughput_low),
    (None, _rule_stw_time_ratio_high),
    (None, _rule_gc_frequency_high),
    (None, _rule_reclaim_low),
    (None, _rule_explicit_gc_called),
    (None, _rule_single_pause_long),
    # CMS-specific
    ("CMS", _rule_cms_concurrent_mode_failure),
    ("CMS", _rule_cms_promotion_failed),
    ("CMS", _rule_cms_remark_too_long),
    ("CMS", _rule_cms_fragmentation),
    # ZGC-specific
    ("Z", _rule_zgc_allocation_stall),
    ("Z", _rule_zgc_pause_exceeds_target),
    ("Z", _rule_zgc_concurrent_cycle_failure),
]


# Risk rollup sets. leak_risk and oom_risk are MUTUALLY EXCLUSIVE states:
# - leak_risk = high means heap is leaking/trending bad but not yet at OOM.
# - oom_risk  = high means heap is at OOM territory (terminal state).
# When both could fire, OOM takes precedence (see _rollup_risks).
# Per user principle "OOM = Full GC + cannot reclaim memory":
# - reclaim_low (Full GC + low reclaim) → oom
# - alloc_failure_full (Full GC + alloc failed) → oom
# - cms_concurrent_mode_failure (CMS Full GC fallback) → oom
# - cms_promotion_failed (Full GC + Old Gen full) → oom
# - zgc_allocation_stall (ZGC sync mode) → oom
# - zgc_concurrent_cycle_failure (ZGC fallback) → oom
# - g1_full_gc alone → performance (not oom by itself, only combined with reclaim_low)
# - reclaim_low alone → leak (heap can't free, but no Full GC explicitly)
# - cms_fragmentation (Full GC + heap has space) → leak
_LEAK_RULES = {"heap_floor_rising", "reclaim_declining", "g1_mixed_ineffective", "post_gc_high_usage",
              "cms_fragmentation", "reclaim_low"}
# Note: g1_compaction_pause and evacuation_failure are NOT in _LEAK_RULES.
# Both indicate heap pressure (which could be transient bursts, cache warmup,
# or allocation spikes), but not necessarily a leak. leak_risk is therefore
# determined only by g1_mixed_ineffective + reclaim_low — the conservative
# signal pattern. To avoid false-positive leak alerts, we keep
# compaction/evacuation out of _LEAK_RULES and let them surface as
# performance findings only.
# Note: zgc_allocation_stall and zgc_concurrent_cycle_failure are NOT in _OOM_RULES.
# Per user principle "OOM = Full GC + cannot reclaim memory":
# - zgc_allocation_stall alone indicates heap pressure (ZGC may still be reclaiming
#   effectively; issue is allocation rate outpacing ZGC's collection speed).
#   Only when paired with reclaim_low (actual failure to reclaim) does it escalate
#   to OOM (handled by cross-rule escalation below).
# - zgc_concurrent_cycle_failure similarly — performance signal unless paired with
#   reclaim_low. Keeping these out of _OOM_RULES avoids false-positive OOM alerts
#   when ZGC is operating normally under high allocation rate.
_OOM_RULES = {"oom_critical", "alloc_failure_full", "reclaim_low"}


def _rollup_risks(findings: List[Dict[str, Any]]) -> Dict[str, str]:
    leak_high = [f for f in findings if f["severity"] == "high" and f["rule"] in _LEAK_RULES]
    leak_med = [f for f in findings if f["severity"] == "medium" and f["rule"] in _LEAK_RULES]
    oom_high = [f for f in findings if f["severity"] == "high" and f["rule"] in _OOM_RULES]
    oom_med = [f for f in findings if f["severity"] == "medium" and f["rule"] in _OOM_RULES]

    if leak_high:
        leak_risk = "high"
    elif len(leak_med) >= 1:
        # Even a single medium leak finding (e.g., cms_fragmentation, g1_mixed_ineffective)
        # indicates an active leak/perf-degradation signal worth medium-level alert
        leak_risk = "medium"
    elif leak_med:
        leak_risk = "low"
    else:
        leak_risk = "none"

    if oom_high:
        oom_risk = "high"
    elif oom_med:
        oom_risk = "medium"
    else:
        oom_risk = "none"

    # Cross-rule escalation (user principle: OOM = Full GC + cannot reclaim):
    # If g1_full_gc is high (≥3 sustained) AND reclaim_low fires, bump oom_risk to high.
    has_g1_full_high = any(f["rule"] == "g1_full_gc" and f["severity"] == "high" for f in findings)
    has_reclaim_any = any(f["rule"] == "reclaim_low" for f in findings)
    if has_g1_full_high and has_reclaim_any and oom_risk != "high":
        oom_risk = "high"

    # ZGC analog: if zgc_allocation_stall or zgc_concurrent_cycle_failure is high
    # AND reclaim_low fires (heap can't actually reclaim memory), bump oom_risk to high.
    has_zgc_oom_signal = any(
        f["rule"] in ("zgc_allocation_stall", "zgc_concurrent_cycle_failure")
        and f["severity"] == "high"
        for f in findings
    )
    if has_zgc_oom_signal and has_reclaim_any and oom_risk != "high":
        oom_risk = "high"

    # Mutual exclusion: leak_risk and oom_risk describe sequential states
    # (OOM is the terminal state, leak is the warning). If both could be
    # reported, OOM wins and leak collapses to "none" — there's no point
    # warning about "leak" once the heap is already at OOM territory.
    _SEV_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
    if _SEV_RANK[oom_risk] >= _SEV_RANK[leak_risk] and oom_risk != "none":
        leak_risk = "none"

    return {"leak_risk": leak_risk, "oom_risk": oom_risk}


# =============================================================================
# Root cause detection + evidence/symptom classification + tiered recommendations
# =============================================================================

# Findings that *directly* support each root cause category. A finding not in
# the active category's set becomes a "symptom" (downstream effect of the root).
# Per user principle "OOM = Full GC + cannot reclaim":
# - oom: g1_full_gc and reclaim_low are NOT in oom individually (only together)
#   g1_full_gc alone = performance (Full GC happened but heap may have recovered)
# - reclaim_low alone = leak (heap can't reclaim, but no explicit Full GC)
# - reclaim_low is now in BOTH leak and oom (mutual exclusion decides which)
# - CMS rules → performance (CMS designed to allow Full GC fallback)
# - ZGC allocation_stall + cycle_failure → performance alone (heap pressure)
#   OOM only via cross-rule escalation when paired with reclaim_low
# - cms_fragmentation → leak (Full GC + heap has space = resource fragmentation)
_EVIDENCE_RULES = {
    "oom": {
        "reclaim_low", "alloc_failure_full",
    },
    "leak": {
        "g1_mixed_ineffective", "reclaim_low", "cms_fragmentation",
    },
    "performance": {
        "throughput_low", "gc_frequency_high", "stw_time_ratio_high", "single_pause_long",
        "g1_full_gc", "cms_remark_too_long",
        "cms_concurrent_mode_failure", "cms_promotion_failed",
        # Heap pressure signals (NOT leak — could be transient bursts)
        "g1_compaction_pause", "evacuation_failure",
        # ZGC hard fallback signals: alone = performance, paired with reclaim_low
        # is escalated to oom via cross-rule logic in _rollup_risks.
        "zgc_allocation_stall", "zgc_concurrent_cycle_failure",
    },
}

_ROOT_CAUSE_SUMMARY = {
    "oom": {
        "label_zh": "OOM 即将发生",
        "label_en": "OOM imminent",
        "summary_zh": "堆已无法回收有效空间 (G1 退化到 Full GC) 或回收率过低",
        "summary_en": "Heap can no longer reclaim space (G1 fallback to Full GC or low reclaim ratio)",
        # ZGC-specific OOM (when zgc_allocation_stall + reclaim_low co-occur)
        "summary_zh_zgc": "ZGC 进入 sync mode (Allocation Stall) 且回收率过低, 堆无法回收也无法满足分配",
        "summary_en_zgc": "ZGC entered sync mode (allocation stall) AND reclaim ratio too low, heap cannot reclaim or satisfy allocation",
    },
    "leak": {
        "label_zh": "内存泄漏",
        "label_en": "Memory leak",
        "summary_zh": "老年代持续增长, Mixed GC 跟不上晋升速率, 堆即将耗尽",
        "summary_en": "Old generation keeps growing, Mixed GC cannot keep up with promotion, heap nearly exhausted",
    },
    "performance": {
        "label_zh": "性能问题",
        "label_en": "Performance issue",
        "summary_zh": "GC 暂停时间/频率过高, 应用可用时间被吞噬",
        "summary_en": "GC pause time / frequency too high, eating into application availability",
    },
    "healthy": {
        "label_zh": "无明显问题",
        "label_en": "No significant issues",
        "summary_zh": "本报告未触发任何诊断规则",
        "summary_en": "No diagnostic rules triggered for this report",
    },
}


def _compute_root_cause(risks: Dict[str, str], findings: List[Dict[str, Any]],
                        collector: str = "") -> Dict[str, str]:
    """Determine the primary diagnosis (oom / leak / performance / healthy).

    Uses the existing risk rollup as ground truth:
      - oom_risk high/medium → OOM
      - leak_risk high/medium → Leak  (already mutually exclusive with oom)
      - any other finding present → Performance
      - nothing → Healthy

    Summary text uses a collector-specific variant for OOM when available
    (e.g., ZGC Allocation Stall + reclaim_low).
    """
    if risks.get("oom_risk") in ("high", "medium"):
        category = "oom"
    elif risks.get("leak_risk") in ("high", "medium"):
        category = "leak"
    elif findings:
        category = "performance"
    else:
        category = "healthy"
    summary = _ROOT_CAUSE_SUMMARY[category]
    # Use collector-specific summary if available
    summary_key = f"summary_{'en' if True else 'zh'}_{collector.lower()}" \
        if collector and f"summary_en_{collector.lower()}" in summary else None
    summary_zh = summary["summary_zh"]
    summary_en = summary["summary_en"]
    if collector and f"summary_en_{collector.lower()}" in summary:
        summary_zh = summary.get(f"summary_zh_{collector.lower()}", summary_zh)
        summary_en = summary[f"summary_en_{collector.lower()}"]
    return {
        "category": category,
        "label_zh": summary["label_zh"],
        "label_en": summary["label_en"],
        "summary_zh": summary_zh,
        "summary_en": summary_en,
    }


def _categorize_findings(findings: List[Dict[str, Any]], category: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split findings into (evidence, symptoms) based on the active root cause."""
    evidence_rules = _EVIDENCE_RULES.get(category, set())
    evidence: List[Dict[str, Any]] = []
    symptoms: List[Dict[str, Any]] = []
    for f in findings:
        if f["rule"] in evidence_rules:
            evidence.append(f)
        else:
            symptoms.append(f)
    return evidence, symptoms


def _generate_recommendations(
    category: str, collector: str, fired_rules: set,
    findings: List[Dict[str, Any]],
    stats: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build tiered recommendations: immediate / short_term / tuning / profiling.

    Each entry: { tier, action_zh, action_en, triggered_by: [rule_ids] }
    Tier order enforced: immediate → short_term → tuning → profiling.
    """
    recs: List[Dict[str, Any]] = []
    fired_evidence = {f["rule"] for f in findings}  # alias for internal use

    def _add(tier: str, action_zh: str, action_en: str, triggered_by: list) -> None:
        # Deduplicate by action text (avoid two near-identical recs)
        for r in recs:
            if r["action_zh"] == action_zh or r["action_en"] == action_en:
                r["triggered_by"] = sorted(set(r["triggered_by"]) | set(triggered_by))
                return
        recs.append({
            "tier": tier,
            "action_zh": action_zh,
            "action_en": action_en,
            "triggered_by": list(triggered_by),
        })

    if category == "oom":
        if "reclaim_low" in fired_rules or "g1_full_gc" in fired_rules \
                or "cms_concurrent_mode_failure" in fired_rules \
                or "cms_promotion_failed" in fired_rules \
                or "zgc_allocation_stall" in fired_rules \
                or "zgc_concurrent_cycle_failure" in fired_rules:
            triggers = sorted({r for r in fired_rules if r in {
                "reclaim_low", "g1_full_gc", "cms_concurrent_mode_failure",
                "cms_promotion_failed", "zgc_allocation_stall",
                "zgc_concurrent_cycle_failure"}})
            _add("immediate",
                 "立即执行 jmap -dump:live,format=b,file=heap.hprof <pid>，用 MAT 分析大对象持有链",
                 "Run jmap -dump:live,format=b,file=heap.hprof <pid> immediately and analyze with MAT for holder chain",
                 triggers)
            _add("immediate",
                 "检查代码中是否存在持续增长的静态集合、缓存未设上限、ThreadLocal 未清理",
                 "Check code for unbounded static collections, caches without size limit, unclosed ThreadLocal",
                 triggers)
        if "g1_full_gc" in fired_rules and collector == "G1":
            _add("short_term",
                 "检查是否有 Humongous 短命大对象 (G1 退化常见原因)",
                 "Check for short-lived Humongous objects (common cause of G1 degradation)",
                 ["g1_full_gc"])
        if "alloc_failure_full" in fired_rules:
            if collector == "G1":
                _add("short_term",
                     "通过 async-profiler 分析分配热点, 检查 Humongous 短命大对象",
                     "Profile allocation hotspots with async-profiler, check for short-lived Humongous objects",
                     ["alloc_failure_full"])
            else:
                _add("short_term",
                     "检查是否存在大对象频繁分配或年轻代配置过小",
                     "Check for frequent large object allocation or undersized Young Gen",
                     ["alloc_failure_full"])
        if "cms_concurrent_mode_failure" in fired_rules or "cms_promotion_failed" in fired_rules:
            _add("tuning",
                 "CMS 触发 Concurrent Mode Failure / Promotion Failed. 增大 -Xmx 或调整 -XX:CMSInitiatingOccupancyFraction, 长期考虑迁移到 G1",
                 "CMS concurrent mode failure / promotion failed. Increase -Xmx or tune -XX:CMSInitiatingOccupancyFraction; consider migrating to G1",
                 sorted([r for r in ["cms_concurrent_mode_failure", "cms_promotion_failed"] if r in fired_rules]))
        if "zgc_allocation_stall" in fired_rules or "zgc_concurrent_cycle_failure" in fired_rules:
            _add("tuning",
                 "ZGC 出现 Allocation Stall / Concurrent Cycle Failure. 增大 -Xmx 给 ZGC 预留 relocation headroom, 降低分配速率",
                 "ZGC allocation stall / cycle failure. Increase -Xmx for ZGC relocation headroom; reduce allocation rate",
                 sorted([r for r in ["zgc_allocation_stall", "zgc_concurrent_cycle_failure"] if r in fired_rules]))
        if collector == "G1":
            _add("tuning",
                 "考虑增大 -Xmx 或降低 -XX:InitiatingHeapOccupancyPercent 以提前触发 Mixed GC",
                 "Consider increasing -Xmx or lowering -XX:InitiatingHeapOccupancyPercent to trigger Mixed GC earlier",
                 ["g1_full_gc", "reclaim_low"])
        elif "reclaim_low" in fired_rules:
            # Only show this for non-G1 collectors when reclaim_low actually fired.
            # Otherwise the ZGC/CMS-specific recommendation above already covers
            # the situation without falsely attributing to a leak signal.
            _add("tuning",
                 "考虑增大 -Xmx 堆容量, 并排查是否存在内存泄漏导致堆压力",
                 "Consider increasing -Xmx and investigate potential memory leaks causing heap pressure",
                 ["reclaim_low"])

    elif category == "leak":
        if "reclaim_low" in fired_rules or "g1_mixed_ineffective" in fired_rules \
                or "cms_fragmentation" in fired_rules:
            triggers = sorted([r for r in ["reclaim_low", "g1_mixed_ineffective", "cms_fragmentation"]
                              if r in fired_rules])
            _add("immediate",
                 "立即执行 jmap -dump:live,format=b,file=heap.hprof <pid>, 用 MAT 分析持有链",
                 "Run jmap -dump:live,format=b,file=heap.hprof <pid> immediately and analyze with MAT for holder chain",
                 triggers)
            _add("short_term",
                 "检查代码中是否存在持续增长的静态集合、缓存未设上限、ThreadLocal 未清理",
                 "Check code for unbounded static collections, caches without size limit, unclosed ThreadLocal",
                 triggers)
        if "cms_fragmentation" in fired_rules:
            _add("tuning",
                 "CMS 堆碎片化. 启用 -XX:+UseCMSCompactAtFullCollection 或调整 -XX:CMSFullGCsBeforeCompaction. 长期考虑迁移到 G1 (Region-based, 无碎片化)",
                 "CMS heap fragmentation. Enable -XX:+UseCMSCompactAtFullCollection or tune -XX:CMSFullGCsBeforeCompaction; consider migrating to G1 (region-based, less fragmentation)",
                 ["cms_fragmentation"])

    elif category == "performance":
        # DisableExplicitGC: when explicit_gc_called fires, recommend the
        # collector-agnostic JVM flag. This is the most actionable advice
        # for manual Full GC triggers (more useful than collector tuning
        # which doesn't address the root cause: application code calling
        # System.gc()). Applies to all collectors.
        if "explicit_gc_called" in fired_rules:
            _add("tuning",
                 "应用代码中调用了 System.gc(). 生产环境应启用 -XX:+DisableExplicitGC 禁用显式 GC 调用 (除非 RMI/JMX 等场景需要). 同步建议查找代码中 System.gc() 的调用位置并评估是否必要",
                 "Application code called System.gc(). Production should enable -XX:+DisableExplicitGC to disable explicit GC (unless RMI/JMX requires it). Find and review every System.gc() call in the codebase",
                 ["explicit_gc_called"])

        if "gc_frequency_high" in fired_rules:
            if collector == "G1":
                _add("tuning",
                     "G1 是自适应收集器: 调整 -XX:MaxGCPauseMillis (放宽暂停目标) 或 -XX:G1NewSizePercent / -XX:G1MaxNewSizePercent",
                     "G1 is adaptive: tune -XX:MaxGCPauseMillis or -XX:G1NewSizePercent / -XX:G1MaxNewSizePercent",
                     ["gc_frequency_high"])
            elif collector == "Z":
                _add("tuning",
                     "ZGC 自适应收集器: 调整 -XX:ZAllocationSpareTolerance 或 -XX:SoftRefLRUPolicyMSPerMB 降低暂停",
                     "ZGC adaptive: tune -XX:ZAllocationSpareTolerance or -XX:SoftRefLRUPolicyMSPerMB",
                     ["gc_frequency_high"])
            else:
                _add("tuning",
                     "考虑增大 -Xmn 年轻代大小或降低分配速率",
                     "Consider larger Young Gen (-Xmn) or reducing allocation rate",
                     ["gc_frequency_high"])
        if "zgc_pause_exceeds_target" in fired_rules:
            _add("tuning",
                 "ZGC 暂停超过 1ms 目标. 调整 -XX:ZAllocationSpareTolerance (降低 GC 触发敏感度) 或 -XX:SoftRefLRUPolicyMSPerMB",
                 "ZGC pause exceeds 1ms target. Tune -XX:ZAllocationSpareTolerance or -XX:SoftRefLRUPolicyMSPerMB",
                 ["zgc_pause_exceeds_target"])
        if "cms_remark_too_long" in fired_rules:
            _add("tuning",
                 "CMS Remark 阶段过长. 调整 -XX:CMSMarkStackSize 或 -XX:ParallelGCThreads, 或减少 Old Gen 碎片化 (考虑迁移到 G1)",
                 "CMS remark too long. Tune -XX:CMSMarkStackSize or -XX:ParallelGCThreads; reduce Old Gen fragmentation (consider G1)",
                 ["cms_remark_too_long"])
        if any(f["rule"] == "throughput_low" and f["severity"] == "high" for f in findings):
            _add("profiling",
                 "应用吞吐率 < 90%, 暂停时间占比过高, 建议分析分配热点 (async-profiler / JFR) 并减少短期对象分配",
                 "Throughput < 90%, pause time dominates — profile allocation hotspots (async-profiler / JFR) and reduce short-lived object allocation",
                 ["throughput_low"])

    # Sort: immediate → short_term → tuning → profiling; within tier, more
    # triggered_by first (broader rule coverage wins).
    _TIER_RANK = {"immediate": 0, "short_term": 1, "tuning": 2, "profiling": 3}
    recs.sort(key=lambda r: (_TIER_RANK.get(r["tier"], 99), -len(r["triggered_by"])))
    return recs


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
    """Legacy helper kept for backward compat."""
    if collector == "Parallel":
        return [e for e in events if e.category == "Full" and e.heap_before_mb > 0 and e.heap_total_mb > 0]
    elif collector == "G1":
        return [e for e in events if e.category in ("Full", "Mixed") and e.heap_before_mb > 0 and e.heap_total_mb > 0]
    return []


# =============================================================================
# OOM candidates: identify specific event timestamps most likely to precede OOM
# =============================================================================

_OOM_CANDIDATE_BURST_WINDOW_SEC = 60.0  # 3+ Full GCs within this window → cascade
_OOM_CANDIDATE_HIGH_POST_GC_PCT = 0.85  # post-GC heap above this → likely OOM territory
_OOM_CANDIDATE_VERY_HIGH_POST_GC_PCT = 0.95


def _oom_reason_for_event(event) -> Optional[Tuple[str, str]]:
    """Return (reason_zh, reason_en) if event is an OOM candidate, else None.

    Patterns detected:
      - Full GC with "allocation failure" cause + post-GC heap still high
      - G1 Full GC (G1 should not normally produce Full GC)
      - Full GC where post-GC heap is > 95% (heap can't be freed)
    """
    if event.category != "Full":
        return None
    cause_l = (event.cause or "").lower()
    post_pct = (event.heap_after_mb / event.heap_total_mb) if event.heap_total_mb > 0 else 0
    pre_pct = (event.heap_before_mb / event.heap_total_mb) if event.heap_total_mb > 0 else 0

    if "allocation failure" in cause_l and post_pct >= _OOM_CANDIDATE_HIGH_POST_GC_PCT:
        return (
            f"Allocation Failure 触发 Full GC，回收后堆占用仍达 {post_pct*100:.0f}%，OOM 风险高",
            f"Full GC triggered by allocation failure, post-GC heap at {post_pct*100:.0f}%, OOM imminent",
        )
    if post_pct >= _OOM_CANDIDATE_VERY_HIGH_POST_GC_PCT:
        return (
            f"Full GC 后堆占用 {post_pct*100:.0f}%（{pre_pct*100:.0f}% → {post_pct*100:.0f}%），几乎无法回收",
            f"Post-GC heap at {post_pct*100:.0f}% ({pre_pct*100:.0f}% → {post_pct*100:.0f}%), nearly no reclamation",
        )
    # G1 Full GC is itself anomalous
    rb_l = (event.raw_body or "").lower() + " " + cause_l
    if "g1 evacuation pause" in rb_l and "full" in rb_l:
        return (
            "G1 出现 Full GC (并发回收失效)，可能是 OOM 前兆",
            "G1 Full GC (concurrent reclamation failure), possible OOM precursor",
        )
    return None


def _detect_oom_candidates(events) -> List[Dict[str, Any]]:
    """Identify the earliest event most likely to precede OOM.

    Returns at most ONE candidate (the earliest qualifying event) — the user
    only needs the first trigger point for banner display; storing N candidates
    (e.g. 99 in a severe cascade log) wastes payload without adding insight.

    Returns an empty list if no qualifying event found.
    Returns a list of one dict with: id, uptime_sec, absolute_epoch_ms,
        category, cause, post_gc_pct, reason_zh, reason_en
    """
    full_events = [e for e in events if e.category == "Full"]

    # 1) Direct OOM signals (high post-GC, allocation failure, G1 Full GC)
    direct = None
    for e in full_events:
        reason = _oom_reason_for_event(e)
        if not reason:
            continue
        post_pct = (e.heap_after_mb / e.heap_total_mb) if e.heap_total_mb > 0 else 0
        candidate = {
            "id": e.id,
            "uptime_sec": e.uptime_sec,
            "absolute_epoch_ms": e.absolute_epoch_ms,
            "category": e.category,
            "cause": e.cause or "",
            "post_gc_pct": round(post_pct * 100, 1),
            "reason_zh": reason[0],
            "reason_en": reason[1],
        }
        if direct is None or (candidate["uptime_sec"] or 0) < (direct["uptime_sec"] or 0):
            direct = candidate
    if direct:
        return [direct]

    # 2) Burst detection: 3+ Full GCs within a short window (cascade pattern).
    #    Only returned if no direct signal was found.
    burst_events = [
        e for e in full_events
        if e.uptime_sec is not None
        and e.heap_total_mb > 0
        and (e.heap_after_mb / e.heap_total_mb) >= _OOM_CANDIDATE_HIGH_POST_GC_PCT
    ]
    burst_events.sort(key=lambda e: e.uptime_sec)
    for i in range(len(burst_events) - 2):
        a, b, c = burst_events[i], burst_events[i + 1], burst_events[i + 2]
        if (c.uptime_sec - a.uptime_sec) <= _OOM_CANDIDATE_BURST_WINDOW_SEC:
            # Return the FIRST event in the cascade
            ev = a
            post_pct = (ev.heap_after_mb / ev.heap_total_mb) if ev.heap_total_mb > 0 else 0
            return [{
                "id": ev.id,
                "uptime_sec": ev.uptime_sec,
                "absolute_epoch_ms": ev.absolute_epoch_ms,
                "category": ev.category,
                "cause": ev.cause or "",
                "post_gc_pct": round(post_pct * 100, 1),
                "reason_zh": f"60 秒内连续 {3}+ 次 Full GC，OOM 级联",
                "reason_en": f"3+ Full GCs within 60s, OOM cascade pattern",
            }]

    return []


def _diagnose_memory(events, collector, *args, **kwargs):
    """Dispatch each rule function and aggregate results.

    New output structure (root-cause oriented):
      root_cause:    { category, label_zh/en, summary_zh/en }
      evidence:      findings directly supporting the root cause
      symptoms:      downstream effects
      recommendations: tiered list (immediate / short_term / tuning / profiling)
      rule_definitions, oom_candidates: unchanged

    Supports two call styles:
      - New:    _diagnose_memory(events, collector, stats)
      - Legacy: _diagnose_memory(events, collector, heap_max_mb=..., ...)
    """
    if args and isinstance(args[0], dict):
        stats = args[0]
    else:
        stats = {
            "heap_max_mb": kwargs.get("heap_max_mb"),
            "max_heap_usage_pct": kwargs.get("max_heap_usage_pct"),
            "avg_heap_usage_pct": kwargs.get("avg_heap_usage_pct"),
            "by_category": kwargs.get("by_category", {}),
            "throughput": kwargs.get("throughput"),
            "events_per_minute": kwargs.get("events_per_minute"),
            "duration_sec": kwargs.get("duration_sec"),
            "total_pause_ms": kwargs.get("total_pause_ms"),
            "events_total": kwargs.get("events_total"),
        }

    findings: List[Dict[str, Any]] = []
    for scope, fn in RULES:
        if scope is None or scope == collector:
            findings.extend(fn(events, collector, stats))

    risks = _rollup_risks(findings)
    root_cause = _compute_root_cause(risks, findings, collector=collector)
    evidence, symptoms = _categorize_findings(findings, root_cause["category"])
    recommendations = _generate_recommendations(
        root_cause["category"], collector, {f["rule"] for f in findings},
        findings, stats,
    )

    return {
        **risks,
        "collector": collector,
        "root_cause": root_cause,
        "evidence": evidence,
        "symptoms": symptoms,
        "recommendations": recommendations,
        "rule_definitions": RULE_DEFINITIONS,
        "oom_candidates": _detect_oom_candidates(events),
    }

    return {
        **risks,
        "collector": collector,
        "root_cause": root_cause,
        "evidence": evidence,
        "symptoms": symptoms,
        "recommendations": recommendations,
        "rule_definitions": RULE_DEFINITIONS,
        "oom_candidates": _detect_oom_candidates(events),
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
    result["diagnosis"] = _diagnose_memory(events, result["collector"], result)
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
        rc = diagnosis.get("root_cause") or {}
        dlines.append(f"  Root Cause: {rc.get('category', '?')} — {rc.get('label_en', '')}")
        if rc.get("summary_en"):
            dlines.append(f"    {rc['summary_en']}")
        dlines.append(f"  Leak Risk: {diagnosis.get('leak_risk', 'none')}")
        dlines.append(f"  OOM Risk: {diagnosis.get('oom_risk', 'none')}")
        evidence = diagnosis.get("evidence", [])
        symptoms = diagnosis.get("symptoms", [])
        if evidence:
            dlines.append("  Evidence (supporting root cause):")
            for f in evidence:
                title = f.get("title_en") or f.get("title_zh", "")
                detail = f.get("detail_en") or f.get("detail_zh", "")
                dlines.append(f"    - [{f['severity']}] {title}: {detail}")
        if symptoms:
            dlines.append("  Symptoms (downstream effects):")
            for f in symptoms:
                title = f.get("title_en") or f.get("title_zh", "")
                detail = f.get("detail_en") or f.get("detail_zh", "")
                dlines.append(f"    - [{f['severity']}] {title}: {detail}")
        recs = diagnosis.get("recommendations", [])
        if recs:
            dlines.append("  Recommendations (tiered):")
            for r in recs:
                triggered = ", ".join(r.get("triggered_by") or [])
                dlines.append(f"    - [{r['tier']}] {r.get('action_en', '')} (triggered_by: {triggered})")
        lines.extend(dlines)

    text = "\n".join(lines)
    if len(text) > max_chars:
        suffix = "\n...(truncated)"
        text = text[:max_chars - len(suffix)] + suffix
    return text
