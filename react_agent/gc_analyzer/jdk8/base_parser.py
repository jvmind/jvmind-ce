"""Shared base infrastructure for JDK8 legacy GC logging format."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..base import GCEvent, _to_mb


# ---------- 公共正则 ----------
# 行首时间戳: "12.345: " 或 "2024-06-01T12:00:00.123+0800: 12.345: "
# JDK8 时间戳：uptime, date+uptime, 或仅 date（此时 uptime=None）
_RE_TS = re.compile(r"^(?:[^\s]+:\s+)?([\d.]+):\s+(.*)$")
# 用于仅日期前缀（无 uptime 数值），如 "2024-06-01T12:00:00.123+0800: [GC..."
_RE_TS_DATE_ONLY = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4}):\s+(.*)$")

# [Times: user=0.01 sys=0.00, real=0.00 secs]
_RE_TIMES = re.compile(r"\[Times:\s+user=[\d.]+\s+sys=[\d.]+,\s+real=([\d.]+)\s+secs\]")

# 堆变化: 17472K->2175K(19648K) 或 50M->10M(512M)
_RE_HEAP = re.compile(
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])\s*->\s*"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])\s*"
    r"\((?P<ht>\d+(?:\.\d+)?)(?P<htu>[BKMG])\)"
)

# G1 detail line: [Eden: ... Survivors: ... Heap: 26014.0K(65536.0K)->8396.5K(65536.0K)]
_RE_G1_HEAP_DETAIL = re.compile(
    r"Heap:\s*"
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])\s*"
    r"\((?P<hbt>\d+(?:\.\d+)?)(?P<hbtu>[BKMG])\)\s*->\s*"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])\s*"
    r"\((?P<ht>\d+(?:\.\d+)?)(?P<htu>[BKMG])\)"
)

# 代名称: [ParNew: ...] [PSYoungGen: ...] [CMS: ...]
# 注意: 不含 Metaspace — 它不属于堆代际，单独出现不应触发 generational fallback
# （会让 G1/CMS 日志因 [Metaspace:] 落到分支 6 误判为 Young）。
_RE_GEN = re.compile(r"\[(ParNew|PSYoungGen|DefNew|CMS|ParOldGen|PSOldGen|Tenured|PSPermGen):")

# GC/Full GC + cause: [GC (Allocation Failure) ... 或 [Full GC (Allocation Failure) ...
# Handle causes that contain parentheses (e.g. System.gc()) by capturing everything starting from the
# opening parenthesis after GC to the position before the next space/heap segment, which is after
# the outer closing parenthesis.
_RE_GC_CAUSE = re.compile(r"\[(?:Full )?GC\s+\((.*?)\)\s+")

# G1 pause: [GC pause (cause) (young|mixed|initial-mark) ...
#   可能包含第三个括号如 (initial-mark)：[GC pause (G1 Humongous Allocation) (young) (initial-mark)
_RE_G1_PAUSE = re.compile(r"\[GC\s+pause\s+\(([^)]+)\)\s*\((young|mixed|initial-mark)\)(?:\s+\(([^)]+)\))?")

# Concurrent phases: [GC concurrent-mark-start] / [GC concurrent-mark-end, 0.199 secs]
_RE_CONCURRENT = re.compile(r"\[GC\s+(concurrent-[a-z-]+(?:-start|-end)?)(?:[,\s]+([\d.]+)\s*secs)?\]")

# G1 remark: [GC remark ... , X.XXXXXXX secs]
_RE_REMARK = re.compile(r"\[GC\s+remark\s+.*?,\s+([\d.]+)\s*secs\]")

# G1 cleanup: [GC cleanup before->after(total), X.XXXX secs]
_RE_CLEANUP = re.compile(
    r"\[GC\s+cleanup\s+"
    r"(?P<hb>\d+(?:\.\d+)?)(?P<hbu>[BKMG])\s*->\s*"
    r"(?P<ha>\d+(?:\.\d+)?)(?P<hau>[BKMG])\s*"
    r"\((?P<ht>\d+(?:\.\d+)?)(?P<htu>[BKMG])\)[^]]*\]"
)

# 收集器映射
_GEN_COLLECTOR: Dict[str, str] = {
    "PSYoungGen": "Parallel",
    "PSOldGen": "Parallel",
    "ParNew": "Parallel",
    "ParOldGen": "Parallel",
    "DefNew": "Serial",
    "Tenured": "Serial",
    "CMS": "CMS",
}


def _detect_collector(gen_names: set) -> Optional[str]:
    """Detect collector from generation names found in log."""
    if "PSYoungGen" in gen_names or "PSOldGen" in gen_names:
        return "Parallel"
    if "ParNew" in gen_names or "ParOldGen" in gen_names:
        return "Parallel"
    if "DefNew" in gen_names or "Tenured" in gen_names:
        return "Serial"
    if "CMS" in gen_names:
        return "CMS"
    return None


def _extract_duration_secs(body: str) -> float:
    """Extract total pause duration in seconds from GC event body."""
    heap_matches = [(m.start(), m.end()) for m in _RE_HEAP.finditer(body)]
    if heap_matches:
        # 取最后一个堆变化之后找时长
        _, end = heap_matches[-1]
        after = body[end:]
    else:
        after = body
    m = re.search(r",\s+([\d.]+)\s+secs", after)
    if m:
        return float(m.group(1))
    # Fallback: search for [Times: real=X.XX secs]
    m = _RE_TIMES.search(body)
    if m:
        return float(m.group(1))
    return 0.0


def _extract_heap(body: str) -> Optional[Tuple[str, str, str, str, str, str]]:
    """Extract last overall heap change (hb, hbu, ha, hau, ht, htu).
    
    Skips Metaspace/PermGen/gen-level matches that usually come after the overall heap.
    """
    matches = list(_RE_HEAP.finditer(body))
    if not matches:
        return None
    # Search backwards, skip gen/region-level matches that come after overall heap
    for m in reversed(matches):
        pre = body[max(0, m.start() - 40):m.start()].strip()
        # Skip matches that are preceded by [xxx: which means it's inside a generation/region bracket
        if re.search(r'\[[^]]+\s*:\s*$', pre):
            continue
        return m.groups()
    # Fallback: return last match if we didn't find any non-gen matches (shouldn't happen)
    return matches[-1].groups()


def _extract_g1_heap_detail(body: str) -> Optional[Tuple[str, str, str, str, str, str]]:
    """Extract G1 detailed heap info from the detail line after pause."""
    m = _RE_G1_HEAP_DETAIL.search(body)
    if not m:
        return None
    return (
        m.group("hb"),
        m.group("hbu"),
        m.group("ha"),
        m.group("hau"),
        m.group("ht"),
        m.group("htu"),
    )


def _find_g1_heap_detail(lines: List[str], start_index: int) -> Optional[Tuple[str, str, str, str, str, str]]:
    """Find G1 heap detail in subsequent lines after a G1 pause line."""
    j = start_index + 1
    while j < len(lines):
        line = lines[j]
        if _RE_TS.match(line) or _RE_TS_DATE_ONLY.match(line):
            break
        heap = _extract_g1_heap_detail(line)
        if heap:
            return heap
        if line.startswith("[Times:"):
            break
        j += 1
    return None


def _classify_concurrent(phase: str) -> str:
    """Classify concurrent phase into event category."""
    if phase.startswith("concurrent-"):
        return "Concurrent"
    if "remark" in phase:
        return "Remark"
    if "cleanup" in phase:
        return "Cleanup"
    return "Concurrent"


def _preprocess_lines(text: str) -> List[str]:
    """Preprocess JDK8 lines: merge [Times: continuations and indented GC detail lines, skip heap dump blocks."""
    lines = text.splitlines()
    merged = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n").strip()
        if not line:
            continue
        if line.startswith("{") or line.startswith("Heap before GC") or line.startswith("Heap after GC"):
            continue
        if line.startswith("[Times:") and merged:
            merged[-1] = merged[-1] + " " + line
        elif raw_line.startswith((" ", "\t")) and merged and re.search(r"\[(?:Full )?GC\s", merged[-1]):
            merged[-1] = merged[-1] + " " + line
        else:
            merged.append(line)
    return merged
