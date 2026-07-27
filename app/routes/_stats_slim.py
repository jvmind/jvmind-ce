"""Shared utility: strip the heavy ``stats["events"]`` list before sending
GC reports over the wire.

Lives in its own module so that both ``app.routes.gc_reports`` (HTTP layer)
and ``react_agent.memory_db`` (DB output layer) can import it without
introducing circular imports — ``app.routes`` and ``react_agent`` are
siblings and cannot import each other at module top-level.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def slim_gc_stats(stats: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of ``stats`` with the per-event list removed.

    ``stats["events"]`` holds every parsed GC event with its full raw log
    body, which can be tens of MB for large uploads (especially ZGC). HTTP
    responses never need it: frontend renderers do not consume it, and the
    LLM-side ``query_gc_events`` / ``read_gc_report_tool`` reads directly
    from the DB row via ``memory.get_gc_report(...)``, which keeps the
    full payload for internal consumers.

    Returns ``stats`` unchanged (and un-copied) when it is not a dict or
    when the ``events`` key is absent — avoiding needless copies in the
    common (no-events) case.
    """
    if not isinstance(stats, dict) or "events" not in stats:
        return stats
    slim = dict(stats)
    slim.pop("events", None)
    return slim


def slim_gc_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of a GC report dict with ``stats.events`` stripped.

    Convenience wrapper for the very common pattern ``r["stats"] = slim_gc_stats(r["stats"])``,
    but additionally returns a new top-level dict so callers can safely mutate
    the result without affecting the underlying DB row in memory.
    """
    if not isinstance(report, dict):
        return report
    out = dict(report)
    if "stats" in out:
        out["stats"] = slim_gc_stats(out["stats"])
    return out


def slim_gc_reports(reports: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Apply :func:`slim_gc_report` to every element of ``reports``."""
    if not reports:
        return reports or []
    return [slim_gc_report(r) for r in reports]
