"""DEPRECATED: This file has been refactored into the gc_analyzer/ package.

All functionality has been moved to the package with the same API.
This file is kept for backward compatibility - it re-exports everything from the new package.
"""
from __future__ import annotations

# Re-export everything from the new package to maintain backward compatibility
from .gc_analyzer import (
    # Types
    GCEvent,
    # Utilities
    _to_mb, _iso_to_epoch_ms, _UNIT_MB,
    # Parsing
    parse_gc_log,
    # Analysis
    analyze, compute_stats,
    # Summary
    summary_for_llm,
    # Tooling
    read_gc_report_tool,
)

__all__ = [
    'GCEvent',
    '_to_mb', '_iso_to_epoch_ms', '_UNIT_MB',
    'parse_gc_log',
    'analyze', 'compute_stats',
    'summary_for_llm',
    'read_gc_report_tool',
]
