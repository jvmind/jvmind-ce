"""Parallel/Serial/CMS generational collector parsing for JDK8 legacy format."""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..base import GCEvent, _to_mb
from .base_parser import _extract_duration_secs, _extract_heap, _extract_metaspace, _detect_collector

# Re-export for caller
detect_collector = _detect_collector


def parse_generational_gc(body: str, is_full: bool, cause: str, uptime: Optional[float], abs_epoch_ms: Optional[float] = None, raw_lines: Optional[List[str]] = None) -> Optional[GCEvent]:
    """Parse a Full GC or Young GC event for generational collectors (Parallel/Serial/CMS)."""
    heap = _extract_heap(body)
    if not heap:
        return None

    hb, hbu, ha, hau, ht, htu = heap
    dur_secs = _extract_duration_secs(body)
    cat = "Full" if is_full else "Young"
    metaspace = _extract_metaspace(body)

    # raw_body is auto-derived from raw_lines by __post_init__. When raw_lines
    # is provided (multi-line event), the joined raw_body preserves sub-event
    # text embedded in legacy CMS Full GC logs like:
    #   [GC (Allocation Failure) ... [ParNew (promotion failed): ...] [CMS: ...]
    # so rule pattern matchers (e.g. cms_promotion_failed searching for
    # 'promotion failed' in raw_body) can still see it.
    kwargs = dict(
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
    if metaspace is not None:
        kwargs["metaspace_before_mb"] = metaspace[0]
        kwargs["metaspace_after_mb"] = metaspace[1]
        kwargs["metaspace_total_mb"] = metaspace[2]
    if raw_lines is not None:
        kwargs["raw_lines"] = list(raw_lines)
    return GCEvent(**kwargs)
