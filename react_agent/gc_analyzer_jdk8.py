"""DEPRECATED: This file has been refactored into the gc_analyzer/jdk8 package.

All functionality has been moved to the new package structure. This file is kept
for backward compatibility - it re-exports the parse_gc_log_jdk8 function.
"""
from __future__ import annotations

# Re-export from new package for backward compatibility
from .gc_analyzer.jdk8 import parse_gc_log_jdk8

__all__ = ['parse_gc_log_jdk8']
