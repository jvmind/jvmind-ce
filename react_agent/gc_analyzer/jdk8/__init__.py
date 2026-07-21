"""JDK8 legacy PrintGCDetails format parser.

Dispatches collector-specific processing and aggregates the result.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..base import GCEvent, _to_mb, _iso_to_epoch_ms
from .base_parser import (
    _RE_TS, _RE_TS_DATE_ONLY, _RE_GEN, _RE_GC_CAUSE, _RE_CONCURRENT, _RE_REMARK, _RE_CLEANUP, _RE_G1_PAUSE, _RE_HEAP,
    _preprocess_lines, _detect_collector, _classify_concurrent, _extract_heap, _extract_duration_secs
)
from .g1 import parse_g1_pause
from .generational import parse_generational_gc


def _extract_jvm_args(lines: List[str]) -> Optional[List[str]]:
    """Extract JVM startup flags from a JDK8 'CommandLine flags:' header line.

    Returns a list of individual flag tokens, or None if not present.
    """
    for line in lines:
        idx = line.find("CommandLine flags:")
        if idx == -1:
            continue
        rest = line[idx + len("CommandLine flags:"):].strip()
        if not rest:
            return None
        args = [tok for tok in rest.split() if tok]
        return args or None
    return None


def parse_gc_log_jdk8(text: str) -> Dict:
    """Parse JDK8 legacy GC log format."""
    events: List[GCEvent] = []
    collector: Optional[str] = None
    all_gen_names: set = set()
    heap_max_mb: Optional[float] = None
    first_uptime: Optional[float] = None
    last_uptime: Optional[float] = None
    parsed = 0
    total = 0
    event_id = 0

    lines = _preprocess_lines(text)

    jvm_args = _extract_jvm_args(lines)

    i = 0
    while i < len(lines):
        line = lines[i]
        total += 1

        # Extract ISO absolute timestamp from line prefix
        abs_epoch_ms = None
        iso_m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4}):\s+", line)
        if iso_m:
            abs_epoch_ms = _iso_to_epoch_ms(iso_m.group(1))

        m_ts = _RE_TS.match(line)
        if not m_ts:
            m_ts = _RE_TS_DATE_ONLY.match(line)
        uptime = None
        body = ""
        if m_ts:
            uptime_val = m_ts.group(1)
            body = m_ts.group(m_ts.lastindex) or ""
            try:
                uptime = float(uptime_val)
            except (ValueError, TypeError):
                uptime = None
        else:
            # No timestamp format (PrintGCTimeStamps not enabled): starts directly with [GC or [Full GC
            if line.startswith("[GC ") or line.startswith("[Full GC "):
                body = line
            else:
                i += 1
                continue

        if uptime is not None:
            if first_uptime is None:
                first_uptime = uptime
            last_uptime = uptime

        if not re.search(r"\[(?:Full )?GC\s", body):
            i += 1
            continue

        # Collect generation names for collector detection
        for m_gen in _RE_GEN.finditer(body):
            all_gen_names.add(m_gen.group(1))

        ev: Optional[GCEvent] = None

        # 0) G1 Full GC — 必须在 _RE_CONCURRENT 之前
        # G1 Full GC 触发 marking cycle 时会跨多行输出：
        #   [Full GC (cause) <ts>: [GC concurrent-root-region-scan-start]
        #   <ts>: [GC concurrent-root-region-scan-end, X secs]
        #   <ts>: [GC concurrent-mark-start]
        #    NNNM->NNNM(NNNNM), X secs]   <- 关闭行（含堆变化）
        #      [Eden: ...]
        #    [Times: ...]
        # 必须优先匹配，否则嵌入的 [GC concurrent-...] 会被错判为 Concurrent。
        if re.search(r"^\s*\[Full\s+GC", body):
            m_cause = _RE_GC_CAUSE.search(body)
            cause = m_cause.group(1) if m_cause else ""
            heap = _extract_heap(body)
            closing_dur_secs: Optional[float] = None

            # 前向扫描查找关闭行 "NNNM->NNNM(NNNNM), X.XXXX secs]"
            consumed = 1
            if not heap:
                for j in range(i + 1, min(i + 8, len(lines))):
                    next_line = lines[j].strip()
                    m_close = re.search(
                        r"(\d+(?:\.\d+)?)([BKMG])\s*->\s*"
                        r"(\d+(?:\.\d+)?)([BKMG])\s*"
                        r"\((\d+(?:\.\d+)?)([BKMG])\).*?secs\]",
                        next_line,
                    )
                    if m_close:
                        heap = m_close.groups()
                        consumed = j - i + 1
                        m_dur = re.search(r",\s+([\d.]+)\s+secs\]", next_line)
                        if m_dur:
                            closing_dur_secs = float(m_dur.group(1))
                        break

            if heap:
                hb, hbu, ha, hau, ht, htu = heap
                # 优先用关闭行的时长（多行 Full GC 的时长在关闭行）
                dur_secs = closing_dur_secs if closing_dur_secs is not None else _extract_duration_secs(body)
                # raw_body 合并所有消耗的行（包含嵌入的并发子事件），
                # 否则 Top 10 Slowest Events 只显示第一行。
                raw_body = "\n".join(lines[i:i + consumed]) if consumed > 1 else body.strip()
                ev = GCEvent(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Full",
                    cause=cause,
                    heap_before_mb=_to_mb(float(hb), hbu),
                    heap_after_mb=_to_mb(float(ha), hau),
                    heap_total_mb=_to_mb(float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    raw_body=raw_body,
                    absolute_epoch_ms=abs_epoch_ms,
                )
                i += consumed
                event_id += 1
                events.append(ev)
                parsed += 1
                heap_mb = _to_mb(float(ht), htu)
                if heap_mb > 0 and (heap_max_mb is None or heap_mb > heap_max_mb):
                    heap_max_mb = heap_mb
            else:
                i += 1
            continue

        # 1) Concurrent phases
        m_cc = _RE_CONCURRENT.search(body)
        if m_cc and not _extract_heap(body):
            # Only concurrent if no heap change - avoid miscleanup matching
            phase = m_cc.group(1)
            dur_str = m_cc.group(2)
            dur_ms = float(dur_str) * 1000 if dur_str else 0.0
            ev = GCEvent(
                id=event_id,
                uptime_sec=uptime,
                category=_classify_concurrent(phase),
                cause=phase,
                duration_ms=dur_ms,
                raw_type=phase,
                is_concurrent=True,
                absolute_epoch_ms=abs_epoch_ms,
            )
            i += 1
            event_id += 1
            events.append(ev)
            parsed += 1
            continue

        # 1b) 独立的 [GC concurrent-mark-start] + 堆 delta — Mixed GC
        # 仅当不在 [Full GC ...] 内时触发（已被分支 0 拦截）。
        # CMS 用 `CMS-concurrent-mark-start` 走不同格式，不命中此处。
        m_cc_start = re.search(r"\[GC\s+concurrent-mark-start\]", body)
        if m_cc_start:
            heap = _extract_heap(body)
            dur_secs = _extract_duration_secs(body)
            if collector is None:
                collector = "G1"
            if heap:
                hb, hbu, ha, hau, ht, htu = heap
                ev = GCEvent(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Mixed",
                    cause="concurrent-mark-start",
                    heap_before_mb=_to_mb(float(hb), hbu),
                    heap_after_mb=_to_mb(float(ha), hau),
                    heap_total_mb=_to_mb(float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    absolute_epoch_ms=abs_epoch_ms,
                )
                i += 1
                event_id += 1
                events.append(ev)
                parsed += 1
                heap_mb = _to_mb(float(ht), htu)
                if heap_mb > 0 and (heap_max_mb is None or heap_mb > heap_max_mb):
                    heap_max_mb = heap_mb
                continue
            # 没有堆细节时退回到上面的 _RE_CONCURRENT 处理（已被守卫跳过）。
            # 这里不应该到达 — 有 m_cc_start 必有 m_cc。
            i += 1
            continue

        # 2) G1 remark (no heap change)
        m_rm = _RE_REMARK.search(body)
        if m_rm and not _extract_heap(body):
            dur_ms = float(m_rm.group(1)) * 1000
            ev = GCEvent(
                id=event_id,
                uptime_sec=uptime,
                category="Remark",
                cause="remark",
                duration_ms=dur_ms,
                raw_type="remark",
                absolute_epoch_ms=abs_epoch_ms,
            )
            i += 1
            event_id += 1
            events.append(ev)
            parsed += 1
            continue

        # 3) G1 pause
        if re.search(_RE_G1_PAUSE, body):
            ev = g1.parse_g1_pause(i, lines, body, uptime, abs_epoch_ms)
            if collector is None:
                collector = "G1"
            if ev:
                ev.id = event_id
                event_id += 1
                events.append(ev)
                parsed += 1
                # Update max heap
                heap_mb = ev.heap_total_mb
                if heap_mb > 0 and (heap_max_mb is None or heap_mb > heap_max_mb):
                    heap_max_mb = heap_mb
            i += 1
            continue

        # 4) G1 cleanup
        elif re.search(_RE_CLEANUP, body):
            m_cl = _RE_CLEANUP.search(body)
            if m_cl:
                hb, hbu, ha, hau, ht, htu = m_cl.group("hb"), m_cl.group("hbu"), m_cl.group("ha"), m_cl.group("hau"), m_cl.group("ht"), m_cl.group("htu")
                dur_secs = _extract_duration_secs(body)
                ev = GCEvent(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Cleanup",
                    cause="cleanup",
                    heap_before_mb=_to_mb(float(hb), hbu),
                    heap_after_mb=_to_mb(float(ha), hau),
                    heap_total_mb=_to_mb(float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    absolute_epoch_ms=abs_epoch_ms,
                )
                i += 1
                event_id += 1
                events.append(ev)
                parsed += 1
                # Update max heap
                heap_mb = ev.heap_total_mb
                if heap_mb > 0 and (heap_max_mb is None or heap_mb > heap_max_mb):
                    heap_max_mb = heap_mb
            else:
                i += 1
            continue

        # 5) Full GC / Young GC (generational collectors: Parallel/Serial/CMS)
        elif all_gen_names:
            is_full = bool(re.search(r"\[Full\s+GC", body))
            m_cause = _RE_GC_CAUSE.search(body)
            cause = m_cause.group(1) if m_cause else ""
            ev = generational.parse_generational_gc(body, is_full, cause, uptime, abs_epoch_ms)
            if collector is None:
                collector = generational.detect_collector(all_gen_names)
            if ev:
                ev.id = event_id
                event_id += 1
                events.append(ev)
                parsed += 1
                # Update max heap
                heap_mb = ev.heap_total_mb
                if heap_mb > 0 and (heap_max_mb is None or heap_mb > heap_max_mb):
                    heap_max_mb = heap_mb
            i += 1
            continue
        else:
            # nothing matched, just consume this line
            i += 1
            continue

    # Collector inference
    # Check command line flags first - they override generation-based detection
    for line in lines:
        if "UseConcMarkSweepGC" in line:
            collector = "CMS"
            break
    if collector is None:
        for line in lines:
            if "UseG1GC" in line:
                collector = "G1"
                break
    # If still not detected, infer from generation names
    if collector is None:
        collector = _detect_collector(all_gen_names)
    if collector is None and events:
        # Infer from raw_type
        for e in events:
            if "G1 Evacuation" in e.raw_type or "G1 Humongous" in e.raw_type or "G1" in e.raw_type:
                collector = "G1"
                break
    if collector is None and events:
        collector = "Unknown"

    return {
        "collector": collector,
        "heap_max_mb": round(heap_max_mb, 2) if heap_max_mb else None,
        "events": events,
        "first_uptime": first_uptime,
        "last_uptime": last_uptime,
        "parsed_lines": parsed,
        "total_lines": total,
        "jdk_version": "8",
        "jvm_args": jvm_args,
    }
