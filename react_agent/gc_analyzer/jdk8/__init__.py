"""JDK8 legacy PrintGCDetails format parser.

Dispatches collector-specific processing and aggregates the result.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..base import GCEvent, _to_mb, _iso_to_epoch_ms
from .base_parser import (
    _RE_TS, _RE_TS_DATE_ONLY, _RE_GEN, _RE_GC_CAUSE, _RE_CONCURRENT, _RE_CONCURRENT_DEDUP, _RE_CMS_CONCURRENT, _RE_REMARK, _RE_CLEANUP, _RE_G1_PAUSE, _RE_HEAP,
    _preprocess_lines, _detect_collector, _classify_concurrent, _extract_heap, _extract_duration_secs, _extract_metaspace, _extract_jvm_args,
    _safe_float,
)
from .g1 import parse_g1_pause
from .generational import parse_generational_gc
from .preprocess_state import RawGCEvent, parse_to_raw_events


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

    # Use the state machine to get structured events with raw_lines.
    # Each RawGCEvent has:
    #   - raw_lines: list of original log lines (preserves line structure)
    #   - has_heap_before / has_heap_after: flags
    # For backward compat with metric extractors, we also build merged lines
    # (joined with spaces) — same as the legacy _preprocess_lines output.
    raw_events = parse_to_raw_events(text)
    lines = _preprocess_lines(text)  # For jvm_args and collector detection

    jvm_args = _extract_jvm_args(lines)

    # Map raw_events (ordered) to merged lines. raw_events are emitted in the
    # same order as the merged lines (with standalone non-event lines included).
    # Index mapping: each RawGCEvent in raw_events corresponds to ONE merged line
    # (after flattening) because the state machine produces one RawGCEvent per
    # logical event (with raw_lines containing the original multi-line content).
    raw_event_iter = iter(raw_events)
    raw_event_by_merged_idx: List[Optional[RawGCEvent]] = []
    raw_event_idx = 0
    for line in lines:
        if raw_event_idx < len(raw_events):
            ev = raw_events[raw_event_idx]
            # Match: a RawGCEvent corresponds to a merged line if the merged
            # line is " ".join(ev.raw_lines) (for multi-line events) or
            # ev.raw_lines[0] (for single-line events).
            if len(ev.raw_lines) == 1 and ev.raw_lines[0] == line:
                raw_event_by_merged_idx.append(ev)
                raw_event_idx += 1
                continue
            if len(ev.raw_lines) > 1 and " ".join(ev.raw_lines) == line:
                raw_event_by_merged_idx.append(ev)
                raw_event_idx += 1
                continue
        raw_event_by_merged_idx.append(None)

    i = 0
    while i < len(lines):
        line = lines[i]
        total += 1
        # The corresponding raw_event (if any) gives us the original raw_lines
        ev_raw_lines = (raw_event_by_merged_idx[i].raw_lines
                        if i < len(raw_event_by_merged_idx)
                        and raw_event_by_merged_idx[i] is not None
                        else None)

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
                uptime = _safe_float(uptime_val)
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

        # Early gate: line must be a GC event. Accepts:
        #   - [GC ... / [Full GC ...  (standard JDK8 event)
        #   - [CMS-concurrent-...     (CMS concurrent phase, no `[GC ` prefix)
        if not re.search(r"\[(?:Full )?GC\s|\[CMS-concurrent-", body):
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
                            closing_dur_secs = _safe_float(m_dur.group(1))
                        break

            if heap:
                hb, hbu, ha, hau, ht, htu = heap
                # 优先用关闭行的时长（多行 Full GC 的时长在关闭行）
                dur_secs = closing_dur_secs if closing_dur_secs is not None else _extract_duration_secs(body)
                metaspace = _extract_metaspace(body)
                ev_kwargs = dict(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Full",
                    cause=cause,
                    heap_before_mb=_to_mb(_safe_float(hb), hbu),
                    heap_after_mb=_to_mb(_safe_float(ha), hau),
                    heap_total_mb=_to_mb(_safe_float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    absolute_epoch_ms=abs_epoch_ms,
                )
                if metaspace is not None:
                    ev_kwargs["metaspace_before_mb"] = metaspace[0]
                    ev_kwargs["metaspace_after_mb"] = metaspace[1]
                    ev_kwargs["metaspace_total_mb"] = metaspace[2]
                # Prefer original raw_lines (preserves log structure) over
                # merged-string split. The state machine already grouped the
                # multi-line event into raw_lines; we don't need to slice.
                if ev_raw_lines is not None:
                    ev_kwargs["raw_lines"] = list(ev_raw_lines)
                elif consumed > 1:
                    ev_kwargs["raw_lines"] = lines[i:i + consumed]
                # raw_body is auto-derived from raw_lines by __post_init__.
                ev = GCEvent(**ev_kwargs)
                i += consumed
                event_id += 1
                events.append(ev)
                parsed += 1
                heap_mb = _to_mb(_safe_float(ht), htu)
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
            dur_ms = _safe_float(dur_str) * 1000 if dur_str else 0.0
            ev_kwargs = dict(
                id=event_id,
                uptime_sec=uptime,
                category=_classify_concurrent(phase),
                cause=phase,
                duration_ms=dur_ms,
                raw_type=phase,
                is_concurrent=True,
                absolute_epoch_ms=abs_epoch_ms,
            )
            if ev_raw_lines is not None:
                ev_kwargs["raw_lines"] = list(ev_raw_lines)
            ev = GCEvent(**ev_kwargs)
            i += 1
            event_id += 1
            events.append(ev)
            parsed += 1
            continue

        # 1c) G1-specific concurrent phases with heap-delta in body
        # (e.g. [GC concurrent-string-deduplication, K->K(K), avg %, secs]).
        # The K→MB heap data is the String dedup region size, NOT the main
        # Java heap. We extract the duration but NOT the heap stats.
        m_cc_dedup = _RE_CONCURRENT_DEDUP.search(body)
        if m_cc_dedup:
            phase = m_cc_dedup.group(1)
            dur_ms = _extract_duration_secs(body) * 1000
            ev_kwargs = dict(
                id=event_id,
                uptime_sec=uptime,
                category=_classify_concurrent(phase),
                cause=phase,
                duration_ms=dur_ms,
                raw_type=phase,
                is_concurrent=True,
                absolute_epoch_ms=abs_epoch_ms,
            )
            if ev_raw_lines is not None:
                ev_kwargs["raw_lines"] = list(ev_raw_lines)
            ev = GCEvent(**ev_kwargs)
            i += 1
            event_id += 1
            events.append(ev)
            parsed += 1
            continue

        # 1d) CMS concurrent phases: [CMS-concurrent-mark-start] /
        #     [CMS-concurrent-mark: 0.004/0.004 secs] (no `[GC ` prefix).
        #     Duration = SECOND number (Y in X/Y) — empirically matches the
        #     wall-clock (abortable-preclean 0.675/1.780 ran 10.220→11.999 =
        #     1.779s ≈ 1.780; [Times: real=1.78 secs] confirms). The first
        #     number is an internal timer, not the wall duration. 0 for
        #     -start markers.
        m_cc_cms = _RE_CMS_CONCURRENT.search(body)
        if m_cc_cms:
            phase = m_cc_cms.group(1)
            dur_str = m_cc_cms.group(3) or m_cc_cms.group(2) or ""
            dur_ms = _safe_float(dur_str) * 1000 if dur_str else 0.0
            cause = f"CMS-concurrent-{phase}"
            ev_kwargs = dict(
                id=event_id,
                uptime_sec=uptime,
                category="Concurrent",
                cause=cause,
                duration_ms=dur_ms,
                raw_type=cause,
                is_concurrent=True,
                absolute_epoch_ms=abs_epoch_ms,
            )
            if ev_raw_lines is not None:
                ev_kwargs["raw_lines"] = list(ev_raw_lines)
            ev = GCEvent(**ev_kwargs)
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
                metaspace = _extract_metaspace(body)
                ev_kwargs = dict(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Mixed",
                    cause="concurrent-mark-start",
                    heap_before_mb=_to_mb(_safe_float(hb), hbu),
                    heap_after_mb=_to_mb(_safe_float(ha), hau),
                    heap_total_mb=_to_mb(_safe_float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    absolute_epoch_ms=abs_epoch_ms,
                )
                if metaspace is not None:
                    ev_kwargs["metaspace_before_mb"] = metaspace[0]
                    ev_kwargs["metaspace_after_mb"] = metaspace[1]
                    ev_kwargs["metaspace_total_mb"] = metaspace[2]
                if ev_raw_lines is not None:
                    ev_kwargs["raw_lines"] = list(ev_raw_lines)
                ev = GCEvent(**ev_kwargs)
                i += 1
                event_id += 1
                events.append(ev)
                parsed += 1
                heap_mb = _to_mb(_safe_float(ht), htu)
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
            dur_ms = _safe_float(m_rm.group(1)) * 1000
            ev_kwargs = dict(
                id=event_id,
                uptime_sec=uptime,
                category="Remark",
                cause="remark",
                duration_ms=dur_ms,
                raw_type="remark",
                absolute_epoch_ms=abs_epoch_ms,
            )
            if ev_raw_lines is not None:
                ev_kwargs["raw_lines"] = list(ev_raw_lines)
            ev = GCEvent(**ev_kwargs)
            i += 1
            event_id += 1
            events.append(ev)
            parsed += 1
            continue

        # 3) G1 pause
        if re.search(_RE_G1_PAUSE, body):
            ev = g1.parse_g1_pause(i, lines, body, uptime, abs_epoch_ms, ev_raw_lines)
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
                ev_kwargs = dict(
                    id=event_id,
                    uptime_sec=uptime,
                    category="Cleanup",
                    cause="cleanup",
                    heap_before_mb=_to_mb(_safe_float(hb), hbu),
                    heap_after_mb=_to_mb(_safe_float(ha), hau),
                    heap_total_mb=_to_mb(_safe_float(ht), htu),
                    duration_ms=dur_secs * 1000,
                    raw_type=body.strip(),
                    absolute_epoch_ms=abs_epoch_ms,
                )
                if ev_raw_lines is not None:
                    ev_kwargs["raw_lines"] = list(ev_raw_lines)
                ev = GCEvent(**ev_kwargs)
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
            # Detect Full GC. Three patterns:
            #   - "[Full GC (cause) ..." (modern JDK8 format)
            #   - "[GC (cause) ... [CMS: ...]" (legacy CMS Full GC: outer [GC] +
            #     inner [CMS:] sub-event indicates a full stop-the-world collection,
            #     not a minor ParNew)
            #   - "[GC (cause) ... [ParNew (promotion failed): ...]" (legacy CMS Full GC
            #     with promotion-failed fallback: outer [GC] + inner ParNew sub-event
            #     with a failed promotion IS a Full GC that includes a young-gen attempt)
            has_full_prefix = bool(re.search(r"\[Full\s+GC", body))
            has_cms_subevent = "[CMS:" in body
            has_promotion_failure = "promotion failed" in body.lower() or "promotion failure" in body.lower()
            # In legacy CMS format, the outer wrapper is bare [GC (cause). A bare [GC]
            # with an inner [CMS:] sub-event or promotion failure IS a Full GC;
            # without these markers, a bare [GC] is a minor ParNew event.
            is_full = has_full_prefix or has_cms_subevent or (
                not has_full_prefix and has_promotion_failure and "[CMS" not in body
            )
            m_cause = _RE_GC_CAUSE.search(body)
            cause = m_cause.group(1) if m_cause else ""
            ev = generational.parse_generational_gc(body, is_full, cause, uptime, abs_epoch_ms, ev_raw_lines)
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
        elif _RE_HEAP.search(body):
            # Fallback: no generation subevent in line (e.g. log without
            # PrintGCDetails, or [Times: ...] merged onto a line that lost
            # its subevent). Body has [GC (cause) ... heap data ...] and no
            # [ParNew]/[CMS]/[PSYoungGen] subevent — still a real GC event.
            # Treat as generational Young GC unless [Full GC] prefix.
            has_full_prefix = bool(re.search(r"\[Full\s+GC", body))
            m_cause = _RE_GC_CAUSE.search(body)
            cause = m_cause.group(1) if m_cause else ""
            # Default to Parallel/Serial if Cause-based detection doesn't pin it down;
            # collector inference below will refine via CommandLine flags.
            ev = generational.parse_generational_gc(body, has_full_prefix, cause, uptime, abs_epoch_ms, ev_raw_lines)
            if ev:
                ev.id = event_id
                event_id += 1
                events.append(ev)
                parsed += 1
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
    # Check command line flags first - they override generation-based detection.
    # Note: -XX:+ flags have multiple spellings (e.g. UseParallelGC, UseParallelOldGC,
    # UseParallelNewGC). Match the most common forms.
    for line in lines:
        if "UseConcMarkSweepGC" in line:
            collector = "CMS"
            break
    if collector is None:
        for line in lines:
            if "UseG1GC" in line:
                collector = "G1"
                break
    if collector is None:
        # Parallel GC (any spelling)
        for line in lines:
            if ("UseParallelGC" in line or "UseParallelOldGC" in line
                    or "UseParallelNewGC" in line):
                collector = "Parallel"
                break
    if collector is None:
        for line in lines:
            if "UseSerialGC" in line:
                collector = "Serial"
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
