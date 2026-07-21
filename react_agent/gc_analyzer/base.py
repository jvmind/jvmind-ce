"""Shared base types and utilities for GC analysis."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------- 单位换算 ----------
_UNIT_MB = {"B": 1 / 1024 / 1024, "K": 1 / 1024, "M": 1.0, "G": 1024.0}


def _to_mb(value: float, unit: str) -> float:
    return value * _UNIT_MB.get(unit.upper(), 1.0)


# ---------- ISO-8601 绝对时间戳 ----------
_RE_ISO_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4})$")


def _iso_to_epoch_ms(s: str) -> Optional[float]:
    """将 ISO-8601 时间戳转换为毫秒级 epoch（UTC）。"""
    import datetime
    try:
        # Python 3.7-3.10 时区偏移需要冒号分隔：+0800 -> +08:00
        if re.search(r"[+-]\d{4}$", s):
            s = s[:-2] + ":" + s[-2:]
        dt = datetime.datetime.fromisoformat(s)
        return dt.timestamp() * 1000
    except (ValueError, OverflowError):
        return None


# ---------- 数据结构 ----------
@dataclass
class GCEvent:
    id: int                       # GC 编号
    uptime_sec: Optional[float]   # JVM 启动后秒数
    category: str                 # "Young" / "Full" / "Mixed" / "Concurrent" / "Remark" / "Cleanup" / "Initial Mark" / "Other"
    cause: str                    # 例 "G1 Evacuation Pause"
    absolute_epoch_ms: Optional[float] = None  # 绝对时间（epoch ms，从日志 ISO 时间戳解析）
    heap_before_mb: float = 0.0
    heap_after_mb: float = 0.0
    heap_total_mb: float = 0.0
    duration_ms: float = 0.0
    raw_type: str = ""            # 原始的 type 字符串，用于调试
    raw_body: str = ""            # 原始完整日志行主体（含 type + heap + duration）
    is_concurrent: bool = False    # 是否为非 STW 并发阶段
