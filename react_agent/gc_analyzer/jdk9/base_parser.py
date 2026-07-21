"""Shared base infrastructure for JDK9+ unified logging format."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..base import GCEvent, _iso_to_epoch_ms


# ---------- Common regex ----------
# Line head prefix: [uptime][level][tags] or [datetime][uptime][level][tags] any combination
_RE_PREFIX_BRACKETS = re.compile(r"^((?:\[[^\]]*\])+)\s*(.*)$")
# uptime like 12.345s
_RE_UPTIME = re.compile(r"^([\d.]+)s$")
# Main GC event line with before->after(total) duration
# Example: GC(0) Pause Young (Normal) (G1 Evacuation Pause) 24M->3M(256M) 12.345ms
_RE_GC_EVENT = re.compile(
    r"GC\((\d+)\)\s+(?P<full>.+?)\s+"
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])->"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])"
    r"\((?P<ht>\d+(?:\.\d+)?)(?P<htu>[BKMG])\)\s+"
    r"(?P<dur>[\d.]+)ms\s*$"
)
# No heap change but has duration (for some concurrent phases)
_RE_GC_DURATION_ONLY = re.compile(
    r"GC\((\d+)\)\s+(?P<full>.+?)\s+(?P<dur>[\d.]+)ms\s*$"
)
# ZGC cycle summary line (no heap total, only percentage):
#   GC(0) Garbage Collection (Warmup) 52M(81%)->30M(47%)
#   GC(1) Major Collection (Allocation Stall) 64M(100%)->46M(72%)
_RE_ZGC_SUMMARY = re.compile(
    r"GC\((\d+)\)\s+(?P<full>.+?)\s+"
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])\(\d+%\)->"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])\(\d+%\)"
)
# Shenandoah cycle heap change line:
#   GC(0) Concurrent cleanup 19M->10M(64M) 0.186ms
#   GC(1) Pause Final Mark 32M->29M(64M) 1.614ms
_RE_SHENANDOAH_SUMMARY = re.compile(
    r"GC\((\d+)\)\s+.*?\s+"
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])->"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])"
    r"\((?P<ht>\d+(?:\.\d+)?)(?P<htu>[BKMG])\)\s+"
    r"(?P<dur>[\d.]+)ms\s*$"
)
# GC collector detection：Using G1 / Using Parallel / Using Z / Using Shenandoah / Using Serial
_RE_COLLECTOR = re.compile(
    r"Using\s+(G1|Parallel|Serial|Shenandoah|ZGC|Z|Epsilon|CMS|Concurrent\s+Mark\s+Sweep|The\s+Z\s+Garbage\s+Collector)",
    re.I,
)
# CommandLine flags collector flag (also keep for JDK8)
_RE_COLLECTOR_FLAG = re.compile(r"-XX:\+Use(G1|Parallel|Serial|Shenandoah|Z|ZGC|Epsilon)GC")
# Heap capacity: prefer Max; ZGC init log common format "Max Capacity: 64M"
_RE_HEAP_MAX = re.compile(r"(?:Heap\s+)?Max\s+Capacity[:\s]+(\d+(?:\.\d+)?)([BKMG])", re.I)
_RE_HEAP_INIT = re.compile(r"(?:Heap\s+)?(?:Initial|Min)\s+Capacity[:\s]+(\d+(?:\.\d+)?)([BKMG])", re.I)


def _parse_prefix(line: str) -> Tuple[Optional[float], Optional[float], str]:
    """Strip leading brackets, return (uptime_sec, absolute_epoch_ms, remaining text)."""
    m = _RE_PREFIX_BRACKETS.match(line)
    if not m:
        return None, None, line
    brackets, rest = m.group(1), m.group(2)
    uptime = None
    abs_epoch_ms = None
    for piece in re.findall(r"\[([^\]]*)\]", brackets):
        stripped = piece.strip()
        # Check for uptime
        mm = _RE_UPTIME.match(stripped)
        if mm:
            try:
                uptime = float(mm.group(1))
            except ValueError:
                pass
            # continue to find absolute time after
            continue
        # Check for ISO datetime
        if abs_epoch_ms is None and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4}$", stripped):
            abs_epoch_ms = _iso_to_epoch_ms(stripped)
    return uptime, abs_epoch_ms, rest.strip()


def _classify(full_type: str) -> Tuple[str, str, bool]:
    """Extract category, cause and concurrent flag from "Pause Young (Normal) (G1 Evacuation Pause)".
       Handle causes that contain parentheses like (System.gc()) by using simpler greedy
       matching that captures everything from the opening parenthesis to the last one
       which works for most GC logging cases.
    """
    s = full_type.strip()
    causes = re.findall(r"\(([^()]+)\)", s)
    if causes:
        # Multiple or single non-nested parenthesized groups: last is the cause
        cause = causes[-1]
        base = re.sub(r"\([^()]*\)", "", s).strip()
    else:
        # No non-nested group found — either no parens at all, or nested like (System.gc())
        open_paren = s.find("(")
        if open_paren >= 0:
            close_paren = s.rfind(")")
            if close_paren > open_paren:
                cause = s[open_paren + 1:close_paren]
                base = s[:open_paren].strip()
            else:
                cause = s
                base = s
        else:
            cause = s
            base = s
    # Generational ZGC has Y:/O: prefix, e.g. "Y: Pause Mark Start"
    normalized_base = re.sub(r"^[YO]:\s+", "", base).strip()
    sl = normalized_base.lower()
    raw_lower = s.lower()
    has_z_generation_prefix = bool(re.match(r"^[YO]:\s+", base))
    is_concurrent = sl.startswith("concurrent")
    cat = "Other"
    # 优先级：Full > Mixed > Young > ZGC pause > Shenandoah > Remark > Cleanup > InitialMark > Concurrent
    # ZGC full gc: Classify as Full GC when:
    # 1. Explicitly has the word "full" (Full GC), OR
    # 2. "major collection" AND cause is "system.gc()" - JDK25+ ZGC uses this format for System.gc()
    # 3. It's "Garbage Collection (System.gc())" - older ZGC explicit full GC triggered by System.gc()
    # Use word boundary check to avoid false positives like "Warm**full**" matching "full"
    has_full = re.search(r'\bfull\b', raw_lower) is not None
    has_major_systemgc = re.search(r'\bmajor collection\b', raw_lower) is not None and cause.lower() == "system.gc()"
    if has_full or has_major_systemgc or (
        "garbage collection" in raw_lower and cause.lower() == "system.gc()"
    ):
        cat = "Full"
    elif "mixed" in raw_lower:  # G1: Pause Young (Mixed)
        cat = "Mixed"
    elif "young" in raw_lower and not has_z_generation_prefix:
        cat = "Young"
    elif has_z_generation_prefix and any(k in sl for k in ("mark start", "mark end", "relocate start")):
        cat = "ZGC"
    elif any(k in sl for k in ("mark start", "mark end", "relocate start", "relocate end")):
        cat = "ZGC"
    elif any(k in sl for k in (
        "final roots", "init mark", "final mark", "init update refs", "final update refs", "concurrent marking"
    )) or ("concurrent cleanup" in sl and "for next mark" not in sl):
        cat = "Shenandoah"
    elif "remark" in sl:
        cat = "Remark"
    elif is_concurrent:
        cat = "Concurrent"
    elif "cleanup" in sl:
        cat = "Cleanup"
    elif "initial mark" in sl:
        cat = "InitialMark"
    return cat, cause, is_concurrent


def normalize_collector_name(raw_collector: str) -> str:
    """Normalize various collector names to canonical form."""
    raw_lower = raw_collector.strip().lower()
    if raw_lower in ("z", "zgc", "the z garbage collector"):
        return "Z"
    elif raw_lower == "g1":
        return "G1"
    elif raw_lower == "shenandoah":
        return "Shenandoah"
    elif raw_lower == "parallel":
        return "Parallel"
    elif raw_lower == "serial":
        return "Serial"
    elif raw_lower in ("cms", "concurrent mark sweep"):
        return "CMS"
    elif raw_lower == "epsilon":
        return "Epsilon"
    return raw_collector.capitalize()
