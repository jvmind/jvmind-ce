"""G1-specific parsing for JDK8 legacy format."""
from __future__ import annotations

from typing import List, Optional

from ..base import GCEvent, _to_mb
from .base_parser import (
    _RE_G1_PAUSE, _RE_HEAP,
    _extract_duration_secs, _extract_heap, _extract_g1_heap_detail, _find_g1_heap_detail
)


def parse_g1_pause(line_idx: int, lines: List[str], body: str, uptime: Optional[float], abs_epoch_ms: Optional[float] = None) -> Optional[GCEvent]:
    """Parse JDK8 G1 pause event."""
    m_g1 = _RE_G1_PAUSE.search(body)
    if not m_g1:
        return None
    
    cause = m_g1.group(1)
    sub = m_g1.group(2)
    extra = m_g1.group(3)
    heap = _extract_heap(body)
    if not heap:
        heap = _extract_g1_heap_detail(body)
    if not heap:
        heap = _find_g1_heap_detail(lines, line_idx)
    dur_secs = _extract_duration_secs(body)
    
    # Classify category
    if extra and "initial-mark" in extra.lower():
        cat = "InitialMark"
    else:
        cat = "InitialMark" if sub == "initial-mark" else ("Mixed" if sub == "mixed" else "Young")
    
    if heap:
        hb, hbu, ha, hau, ht, htu = heap
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
    else:
        # G1 Pause may not have heap on main line (in detail line)
        return GCEvent(
            id=None,
            uptime_sec=uptime,
            category=cat,
            cause=cause,
            duration_ms=dur_secs * 1000,
            raw_type=body.strip(),
            absolute_epoch_ms=abs_epoch_ms,
        )
