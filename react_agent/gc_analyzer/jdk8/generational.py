"""Parallel/Serial/CMS generational collector parsing for JDK8 legacy format."""
from __future__ import annotations

from typing import Optional, Tuple

from ..base import GCEvent, _to_mb
from .base_parser import _extract_duration_secs, _extract_heap, _detect_collector

# Re-export for caller
detect_collector = _detect_collector


def parse_generational_gc(body: str, is_full: bool, cause: str, uptime: Optional[float], abs_epoch_ms: Optional[float] = None) -> Optional[GCEvent]:
    """Parse a Full GC or Young GC event for generational collectors (Parallel/Serial/CMS)."""
    heap = _extract_heap(body)
    if not heap:
        return None
    
    hb, hbu, ha, hau, ht, htu = heap
    dur_secs = _extract_duration_secs(body)
    cat = "Full" if is_full else "Young"
    
    return GCEvent(
        id=None,  # will be filled by caller
        uptime_sec=uptime,
        category=cat,
        cause=cause,
        heap_before_mb=_to_mb(float(hb), hbu),
        heap_after_mb=_to_mb(float(ha), hau),
        heap_total_mb=_to_mb(float(ht), htu),
        duration_ms=dur_secs * 1000,
        raw_type=body.strip(),
        absolute_epoch_ms=abs_epoch_ms,
    )
