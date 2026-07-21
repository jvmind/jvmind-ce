"""G1-specific parsing for JDK9+ unified logging."""
from __future__ import annotations

from typing import Dict, List, Optional

from ..base import GCEvent, _to_mb
from .base_parser import _classify


# G1 doesn't require any special separate handling beyond what's already in the common parsing
# This module is placeholder for future G1-specific special cases and keeps the organization clean
def process_line(body: str, gc_id: int, uptime: Optional[float], abs_epoch_ms: Optional[float]) -> Optional[GCEvent]:
    """Process a G1-specific line - mostly handled in common parsing."""
    return None
