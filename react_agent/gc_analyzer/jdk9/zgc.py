"""ZGC-specific parsing for JDK9+ unified logging.

ZGC stores heap information in a separate summary line that needs to be backfilled
into the corresponding pause event.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..base import GCEvent, _to_mb
from .base_parser import _RE_ZGC_SUMMARY


def collect_heap_summary(body: str) -> Optional[Tuple[int, float, float]]:
    """Collect ZGC heap summary from a summary line for later backfilling.
    
    Returns (gc_id, heap_before_mb, heap_after_mb) or None if not a ZGC summary.
    """
    m = _RE_ZGC_SUMMARY.search(body)
    if not m:
        return None
    
    gid = int(m.group(1))
    hb_mb = _to_mb(float(m.group("hb")), m.group("hbu"))
    ha_mb = _to_mb(float(m.group("ha")), m.group("hau"))
    return (gid, hb_mb, ha_mb)


def backfill_heap_data(events: List[GCEvent], zgc_heap_by_id: Dict[int, Tuple[float, float]]) -> None:
    """Backfill collected ZGC heap data into the pause events."""
    if not zgc_heap_by_id:
        return
    
    for ev in events:
        if ev.heap_before_mb == 0 and ev.id in zgc_heap_by_id:
            hb, ha = zgc_heap_by_id[ev.id]
            ev.heap_before_mb = hb
            ev.heap_after_mb = ha
