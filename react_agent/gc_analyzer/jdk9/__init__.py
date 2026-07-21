"""JDK9+ unified logging format parser.

Dispatches collector-specific processing and aggregates the result.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..base import GCEvent, _to_mb
from .base_parser import (
    _RE_GC_EVENT, _RE_GC_DURATION_ONLY, _RE_COLLECTOR, _RE_COLLECTOR_FLAG, _RE_ZGC_SUMMARY,
    _RE_HEAP_MAX, _RE_HEAP_INIT, _parse_prefix, _classify, normalize_collector_name
)
from . import zgc
from . import shenandoah

# Detect [gc,start] tag in bracket prefix to capture GC start time
_RE_GC_START = re.compile(r'\[gc,start\s*\]')
# Allow event creation only from [gc] or [gc,phases] tags — exclude [gc,marking] etc.
_RE_GC_MAIN_OR_PHASES = re.compile(r'\[gc\s*\]|\[gc,phases\s*\]')


def parse_gc_log_jdk9(text: str) -> Dict:
    """Parse JDK9+ unified GC log format."""
    events: List[GCEvent] = []
    collector: Optional[str] = None
    heap_max_mb: Optional[float] = None
    first_uptime: Optional[float] = None
    last_uptime: Optional[float] = None
    parsed = 0
    total = 0
    jdk_version: Optional[str] = "9+"

    # Use (gc_id, category, raw_type) as key: same GC id under multiple phases (like ZGC Mark Start/End)
    by_key: Dict[Tuple[int, str, str], GCEvent] = {}
    # ZGC summary heap data: { gc_id: (before_mb, after_mb) }
    zgc_heap_by_id: Dict[int, Tuple[float, float]] = {}
    # Shenandoah summary heap data: { gc_id: (before_mb, after_mb, total_mb) }
    shenandoah_heap_by_id: Dict[int, Tuple[float, float, float]] = {}
    # Collect all body lines by GC ID for multi-line event context
    gcid_bodies: Dict[int, List[str]] = {}

    # Cache start times from [gc,start] lines: { gc_id: (uptime_sec, abs_epoch_ms) }
    gc_start_times: Dict[int, Tuple[Optional[float], Optional[float]]] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        total += 1

        uptime, abs_epoch_ms, body = _parse_prefix(line)
        if uptime is not None:
            if first_uptime is None:
                first_uptime = uptime
            last_uptime = uptime

        # Collect body lines by GC ID for combined multi-line context
        gc_id_m = re.search(r"GC\((\d+)\)", body)
        if gc_id_m:
            gid = int(gc_id_m.group(1))
            if gid not in gcid_bodies:
                gcid_bodies[gid] = []
            gcid_bodies[gid].append(body.strip())

        # Detect [gc,start] lines: cache start time and skip (no heap/duration data)
        if _RE_GC_START.search(line) and gc_id_m:
            gc_start_times[int(gc_id_m.group(1))] = (uptime, abs_epoch_ms)
            parsed += 1
            continue

        # Collector detection
        if collector is None:
            cm = _RE_COLLECTOR.search(body)
            if cm:
                raw_collector = cm.group(1).strip()
                collector = normalize_collector_name(raw_collector)
            else:
                # Fallback: detect from CommandLine flags
                cf = _RE_COLLECTOR_FLAG.search(body)
                if cf:
                    raw_collector = cf.group(1).strip()
                    collector = normalize_collector_name(raw_collector)

        # Heap capacity - prefer Max
        hm = _RE_HEAP_MAX.search(body)
        if hm:
            try:
                heap_max_mb = _to_mb(float(hm.group(1)), hm.group(2))
            except ValueError:
                pass
        elif heap_max_mb is None:
            hm = _RE_HEAP_INIT.search(body)
            if hm:
                try:
                    heap_max_mb = _to_mb(float(hm.group(1)), hm.group(2))
                except ValueError:
                    pass

        # Check for ZGC summary first (before main event matching)
        zgc_summary = zgc.collect_heap_summary(body)
        if zgc_summary:
            gid, hb, ha = zgc_summary
            zgc_heap_by_id[gid] = (hb, ha)
            # For full ZGC GC like "Garbage Collection (System.gc())", the summary line
            # contains the full type info already - create the full event here if it doesn't exist
            m_z = _RE_ZGC_SUMMARY.match(body)
            raw_type = m_z.group("full").strip()
            cat, cause, is_concurrent = _classify(raw_type)
            key = (gid, cat, raw_type)
            if key not in by_key:
                by_key[key] = GCEvent(
                    id=gid,
                    uptime_sec=uptime,
                    absolute_epoch_ms=abs_epoch_ms,
                    category=cat,
                    cause=cause,
                    heap_before_mb=hb,
                    heap_after_mb=ha,
                    heap_total_mb=0,
                    duration_ms=0,
                    raw_type=raw_type,
                    raw_body=body.strip(),
                    is_concurrent=is_concurrent,
                )
                parsed += 1

        # Check for Shenandoah summary
        shenandoah_summary = shenandoah.collect_heap_summary(body)
        if shenandoah_summary:
            gid, hb, ha, ht = shenandoah_summary
            shenandoah_heap_by_id[gid] = (hb, ha, ht)

        # Main GC event
        m = _RE_GC_EVENT.search(body)
        if m:
            gid = int(m.group(1))
            raw_type = m.group("full").strip()
            cat, cause, is_concurrent = _classify(raw_type)
            ev = GCEvent(
                id=gid,
                uptime_sec=uptime,
                absolute_epoch_ms=abs_epoch_ms,
                category=cat,
                cause=cause,
                heap_before_mb=_to_mb(float(m.group("hb")), m.group("hbu")),
                heap_after_mb=_to_mb(float(m.group("ha")), m.group("hau")),
                heap_total_mb=_to_mb(float(m.group("ht")), m.group("htu")),
                duration_ms=float(m.group("dur")),
                raw_type=raw_type,
                raw_body=body.strip(),
                is_concurrent=is_concurrent,
            )
            key = (gid, cat, raw_type)
            # Same (id, cat, raw_type) duplicates, keep the one with more heap info
            if key not in by_key or ev.heap_before_mb > 0:
                by_key[key] = ev
            parsed += 1
            continue

        # Duration-only events (no heap change) — only from [gc] or [gc,phases] tags
        m2 = _RE_GC_DURATION_ONLY.search(body)
        if m2 and _RE_GC_MAIN_OR_PHASES.search(line):
            raw_type = m2.group("full").strip()
            raw_type_lower = raw_type.lower()
            if "pause" in raw_type_lower or "concurrent" in raw_type_lower:
                gid = int(m2.group(1))
                cat, cause, is_concurrent = _classify(raw_type)
                key = (gid, cat, raw_type)
                if key not in by_key:
                    by_key[key] = GCEvent(
                        id=gid,
                        uptime_sec=uptime,
                        absolute_epoch_ms=abs_epoch_ms,
                        category=cat,
                        cause=cause,
                        duration_ms=float(m2.group("dur")),
                        raw_type=raw_type,
                        raw_body=body.strip(),
                        is_concurrent=is_concurrent,
                    )
                    parsed += 1

    # For ZGC: accumulate all pause durations from multiple phases of the same GC ID
    # because ZGC splits one full GC into multiple STW phases across multiple lines,
    # each with its own pause time that needs to be accumulated
    # Always do this accumulation if we have ZGC-style events with categories ZGC,
    # even if collector wasn't detected early
    has_zgc_events = any(ev.category == 'ZGC' or 'Pause' in ev.raw_type for ev in by_key.values())
    if has_zgc_events:
        # Accumulate only STW pause durations per GC ID
        total_pause_by_gcid: Dict[int, float] = {}
        for ev in by_key.values():
            gid = ev.id
            # Only accumulate Pause phases (they are STW pauses)
            if 'Pause' in ev.raw_type or ev.category == 'ZGC':
                if gid not in total_pause_by_gcid:
                    total_pause_by_gcid[gid] = 0.0
                total_pause_by_gcid[gid] += ev.duration_ms
        
        # Update the main Full GC event with the total accumulated STW pause
        for ev in by_key.values():
            gid = ev.id
            if ev.category == "Full" and gid in total_pause_by_gcid:
                ev.duration_ms = total_pause_by_gcid[gid]
        
        # Keep all events (including phases) but now Full GC event has total pause
        events = sorted(by_key.values(), key=lambda e: (e.uptime_sec or 0.0, e.id))
    else:
        events = sorted(by_key.values(), key=lambda e: (e.uptime_sec or 0.0, e.id))

    # Apply [gc,start] cached start times: overwrite uptime with GC start time
    for ev in events:
        if ev.id in gc_start_times:
            start_uptime, start_epoch = gc_start_times[ev.id]
            if start_uptime is not None:
                ev.uptime_sec = start_uptime
            if start_epoch is not None:
                ev.absolute_epoch_ms = start_epoch

    # ZGC backfill: fill heap data from summary lines into corresponding pause events
    zgc.backfill_heap_data(events, zgc_heap_by_id)

    # Shenandoah backfill: fill heap data from summary lines
    fixed_shenandoah, heap_max_mb = shenandoah.backfill_heap_data(events, shenandoah_heap_by_id, heap_max_mb)

    # Infer heap maximum if not found explicitly
    if heap_max_mb is None and events:
        heap_max_mb = max(e.heap_total_mb for e in events) or None

    # Replace each event's raw_body with combined multi-line context for the same GC ID
    for ev in events:
        combined = "\n".join(gcid_bodies.get(ev.id, [ev.raw_body or ""]))
        if combined:
            ev.raw_body = combined

    return {
        "collector": collector or "Unknown",
        "heap_max_mb": heap_max_mb,
        "events": events,
        "first_uptime": first_uptime,
        "last_uptime": last_uptime,
        "parsed_lines": parsed,
        "total_lines": total,
        "jdk_version": jdk_version,
    }
