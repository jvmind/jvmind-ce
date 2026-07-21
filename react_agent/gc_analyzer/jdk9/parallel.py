"""Parallel/Serial/CMS generational collector parsing for JDK9+ unified logging."""
from __future__ import annotations

# Parallel/Serial/CMS don't need special separate handling beyond common parsing
# This module keeps the organization by collector type as planned
def process_line(body: str, gc_id: int, uptime: Optional[float], abs_epoch_ms: Optional[float]) -> Optional[GCEvent]:
    """Process a Parallel/Serial/CMS-specific line - mostly handled in common parsing."""
    return None
