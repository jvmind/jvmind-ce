"""Shenandoah-specific parsing for JDK9+ unified logging.

Shenandoah stores heap information in separate summary lines that need to be backfilled
into corresponding pause events. Also tracks heap_max_mb based on summary totals.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..base import GCEvent, _to_mb
from .base_parser import _RE_SHENANDOAH_SUMMARY


def collect_heap_summary(body: str) -> Optional[Tuple[int, float, float, float]]:
    """Collect Shenandoah heap summary from a summary line for later backfilling.
    
    Returns (gc_id, heap_before_mb, heap_after_mb, heap_total_mb) or None if not a Shenandoah summary.
    """
    m = _RE_SHENANDOAH_SUMMARY.search(body)
    if not m:
        return None
    
    gid = int(m.group(1))
    hb_mb = _to_mb(float(m.group("hb")), m.group("hbu"))
    ha_mb = _to_mb(float(m.group("ha")), m.group("hau"))
    ht_mb = _to_mb(float(m.group("ht")), m.group("htu"))
    return (gid, hb_mb, ha_mb, ht_mb)


def backfill_heap_data(events: List[GCEvent], shenandoah_heap_by_id: Dict[int, Tuple[float, float, float]], heap_max_mb: Optional[float]) -> Tuple[int, Optional[float]]:
    """Backfill collected Shenandoah heap data into the pause events and update heap_max if needed.
    
    Returns (number of fixed events, updated heap_max_mb).
    """
    if not shenandoah_heap_by_id:
        return (0, heap_max_mb)
    
    fixed = 0
    updated_heap_max = heap_max_mb
    for ev in events:
        if ev.heap_before_mb == 0 and ev.id in shenandoah_heap_by_id:
            hb, ha, ht = shenandoah_heap_by_id[ev.id]
            ev.heap_before_mb = hb
            ev.heap_after_mb = ha
            if ev.heap_total_mb == 0:
                ev.heap_total_mb = ht
            # Update heap_max_mb if we found a larger total than current
            if updated_heap_max is None or ht > updated_heap_max:
                updated_heap_max = ht
            fixed += 1
    
    return (fixed, updated_heap_max)
