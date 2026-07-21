"""Main GC analysis entry point - maintains backward compatibility with old API.

This package is organized by:
  1. JDK version: jdk9/ and jdk8/
  2. Within each JDK: collector-specific parsing is split into individual modules
  3. Shared infrastructure: base.py (types/utilities) and compute_stats.py (statistics)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .base import GCEvent, _to_mb, _iso_to_epoch_ms, _UNIT_MB
from .compute_stats import compute_stats, summary_for_llm
from .jdk9 import parse_gc_log_jdk9
from .jdk8 import parse_gc_log_jdk8


def parse_gc_log(text: str) -> Dict:
    """Parse GC log text, automatically detect JDK version format.
    
    JDK8 format detection rules:
    - Line doesn't start with [ and contains [GC / [Full GC → JDK8
    - Starts directly with [GC or [Full GC (not Worker/Thread) → JDK8
    Otherwise use JDK9+ unified parsing.
    """
    # Auto-detect format by checking first 10 non-empty lines
    _count = 0
    for _line in text.splitlines():
        _l = _line.strip()
        if not _l:
            continue
        _count += 1
        if _count > 10:
            break
        # JDK8 detection
        if not _l.startswith("[") and re.search(r"\[(?:Full )?GC\s", _l) and re.match(r"^[^[\s]", _l):
            return parse_gc_log_jdk8(text)
        if (_l.startswith("[GC ") or _l.startswith("[Full GC ")) and not re.match(
            r"\[(?:Full )?GC\s+(?:Worker|Thread)", _l
        ):
            return parse_gc_log_jdk8(text)

    # Otherwise use JDK9+
    return parse_gc_log_jdk9(text)


def analyze(text: str) -> Dict:
    """Main entry point: parse + compute statistics.

    Same API as before for backward compatibility.
    """
    parsed = parse_gc_log(text)
    stats = compute_stats(parsed)
    return stats


# Keep the old name for backward compatibility
summary_for_llm = summary_for_llm


def read_gc_report_tool(memory, session_id: str, arg: str) -> str:
    """Tool for Agent: read existing GC analysis reports in the current session.

    arg="list" (or empty) returns report list; arg=<report_id> returns detailed stats.
    """
    arg = arg.strip()
    if not arg or arg == "list":
        reports = memory.list_gc_reports(session_id)
        if not reports:
            return ("No GC reports in current session. Please upload a GC log file first.\n"
                    "当前会话没有 GC 报告。请先上传 GC 日志文件。")
        lines = ["GC Reports in current session / 当前会话的 GC 报告列表："]
        for r in reports:
            ai_tag = " [AI diagnosis / 有 AI 诊断]" if r.get("has_ai") else ""
            lines.append(
                f"  - [{r['id']}] {r['filename']} ({r['created_at']}) "
                f"- Collector={r.get('collector','?')}, "
                f"Events={r.get('events_total','?')}, "
                f"Total Pause={r.get('total_pause_ms','?')}ms{ai_tag}"
            )
        lines.append(f"\nTotal {len(reports)} reports. Use read_gc_report(<report_id>) for details.")
        return "\n".join(lines)

    report = memory.get_gc_report(session_id, arg)
    if not report:
        return (f"Report '{arg}' not found. Use read_gc_report(list) to list available reports.\n"
                f"未找到 ID 为 '{arg}' 的 GC 报告。使用 read_gc_report(list) 查看可用报告列表。")

    stats = report.get("stats", {})
    lines = [
        "=== GC Report / GC 报告 ===",
        f"Filename / 文件名: {report.get('filename', '?')}",
        f"Analyzed at / 分析时间: {report.get('created_at', '?')}",
        "",
        f"Collector / 收集器: {stats.get('collector', '?')}",
        f"Heap Capacity / 堆容量: {stats.get('heap_max_mb', '?')} MB",
        f"Log Duration / 日志覆盖时长: {stats.get('duration_sec', '?')}s",
        f"Total GC Events / GC 事件总数: {stats.get('events_total', '?')}",
        f"Total Pause Time / 总停顿时间: {stats.get('total_pause_ms', '?')}ms",
    ]
    if stats.get("throughput") is not None:
        lines.append(f"Application Throughput / 应用吞吐率: {stats['throughput']*100:.3f}%")
    if stats.get("avg_alloc_rate_mb_s") is not None:
        lines.append(f"Average Allocation Rate / 平均分配速率: {stats['avg_alloc_rate_mb_s']} MB/s")

    jvm_args = stats.get("jvm_args")
    if jvm_args:
        lines.append(f"JVM Args / JVM 启动参数: {' '.join(jvm_args)}")

    by_cat = stats.get("by_category", {})
    if by_cat:
        lines.append(f"\nBy Category / 按类型统计:")
        for cat, s in by_cat.items():
            lines.append(
                f"  - {cat}: count={s['count']}, total_pause={s['total_pause_ms']}ms, "
                f"avg={s['avg_pause_ms']}ms, max={s['max_pause_ms']}ms, "
                f"p95={s['p95_pause_ms']}ms, p99={s['p99_pause_ms']}ms, "
                f"avg_freed={s['avg_freed_mb']}MB"
            )

    slowest = stats.get("slowest", [])
    if slowest:
        lines.append(f"\nTop 5 Slowest Events / Top 5 最慢事件:")
        for e in slowest[:5]:
            lines.append(
                f"  - GC#{e['id']} @{e['t']}s [{e['cat']}] "
                f"{e['before']}MB->{e['after']}MB dur={e['dur']}ms (cause={e['cause']})"
            )

    ai_conc = report.get("ai_conclusion")
    if ai_conc:
        lines.append(f"\nAI Diagnosis / AI 诊断结论:\n{ai_conc[:2000]}")
        if len(ai_conc) > 2000:
            lines.append("...(truncated)")

    return "\n".join(lines)


def query_events(
    memory,
    session_id: str,
    *,
    report_id: str,
    category: Optional[str] = None,
    cause: Optional[str] = None,
    time_start: Optional[float] = None,
    time_end: Optional[float] = None,
    duration_min: Optional[float] = None,
    gc_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """按过滤器返回已落库报告内的事件列表（紧凑文本格式）。

    Returns: see spec Output format. Max length 2500 chars.
    """
    if not report_id or not str(report_id).strip():
        return ("report_id is required.\n"
                "需要传 report_id 参数。")

    report = memory.get_gc_report(session_id, report_id) if memory else None
    if not report:
        return (f"Report '{report_id}' not found. "
                f"Use read_gc_report(list) to see available reports.\n"
                f"未找到 ID 为 '{report_id}' 的 GC 报告。"
                f"使用 read_gc_report(list) 查看可用报告列表。")

    stats = report.get("stats") or {}
    events = stats.get("events")
    if events is None:
        return (f"This report was persisted before query_gc_events was available. "
                f"Please re-upload the GC log to enable event-level queries.\n"
                f"该报告未持久化完整事件。请重新上传 GC 日志以启用事件级查询。")

    # Clamp limits
    if limit is None or limit <= 0:
        limit = 20
    if limit > 100:
        limit = 100
    if offset is None or offset < 0:
        offset = 0
    if duration_min is not None and duration_min < 0:
        duration_min = 0

    matched = events
    if gc_id is not None:
        matched = [e for e in matched if e.get("id") == gc_id]
    if category:
        matched = [e for e in matched if e.get("cat") == category]
    if cause:
        c = cause.lower()
        matched = [e for e in matched if c in (e.get("cause") or "").lower()]
    if time_start is not None:
        matched = [e for e in matched if (e.get("t") or 0) >= time_start]
    if time_end is not None:
        matched = [e for e in matched if (e.get("t") or 0) <= time_end]
    if duration_min is not None and duration_min > 0:
        matched = [e for e in matched if (e.get("dur") or 0) >= duration_min]

    total = len(matched)
    sliced = matched[offset:offset + limit]
    return _format_events_for_llm(
        report=report, sliced=sliced, total=total,
        offset=offset, limit=limit,
        filters={
            "gc_id": gc_id, "category": category, "cause": cause,
            "time_start": time_start, "time_end": time_end,
            "duration_min": duration_min,
        },
    )


def _format_events_for_llm(*, report, sliced, total, offset, limit, filters,
                            max_chars: int = 2500) -> str:
    """Format the query result as a compact multi-line text block."""
    stats = report.get("stats") or {}
    header = [
        "GC Events Query Result",
        f"Report: {report.get('id', '?')} ({report.get('filename', '?')})",
        f"Collector: {stats.get('collector', '?')}  "
        f"Heap: {stats.get('heap_max_mb', '?')}MB  "
        f"Duration: {stats.get('duration_sec', '?')}s",
    ]
    filter_parts = []
    for k, v in filters.items():
        if v is not None and v != "":
            filter_parts.append(f"{k}={v}")
    header.append("Filter: " + "  ".join(filter_parts) if filter_parts else "Filter: (none)")

    truncated = total > offset + len(sliced)
    header.append(
        f"Matched: {total}  Returned: {len(sliced)}  "
        f"Offset: {offset}  Limit: {limit}"
        + ("  [truncated]" if truncated else "")
    )

    body = []
    for e in sliced:
        eid = e.get("id", "?")
        t = e.get("t") or 0.0
        cat = e.get("cat", "?")
        before = e.get("before", 0)
        after = e.get("after", 0)
        dur = e.get("dur", 0)
        cause = e.get("cause", "")
        body.append(
            f"  - GC#{eid} @{t:.2f}s [{cat}] {before:.0f}MB->{after:.0f}MB "
            f"dur={dur:.1f}ms (cause={cause})"
        )
        raw = e.get("raw") or ""
        if raw:
            snippet = raw.replace("\n", " ")[:120]
            body.append(f"    raw: {snippet}{'...' if len(raw) > 120 else ''}")

    text = "\n".join(header + body)
    if len(text) > max_chars:
        suffix = "\n...(truncated output; refine filter to narrow results)"
        text = text[:max_chars - len(suffix)] + suffix
    return text
