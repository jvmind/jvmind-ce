"""Epsilon GC parsing for JDK9+ unified logging.

Epsilon doesn't do any garbage collection, so it just has initialization logging.
"""
from __future__ import annotations

# Epsilon doesn't produce collection events, just needs collector detection.
# Collector detection is already handled in base_parser.normalize_collector_name
# This module exists for the organization structure
def process_line(body: str, gc_id: int, uptime: Optional[float], abs_epoch_ms: Optional[float]) -> Optional[GCEvent]:
    """Process an Epsilon-specific line - no collection events expected."""
    return None
