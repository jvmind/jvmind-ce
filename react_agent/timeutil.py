"""统一的 UTC 时间工具。

项目所有持久化时间戳一律以 **UTC** 存储，字符串格式沿用 ``"%Y-%m-%d %H:%M:%S"``
（不含时区标记，但语义为 UTC）。前端读取时按浏览器本地时区渲染。

设计要点：
- 生产端（``now_str`` / ``future_str``）输出 UTC 字符串。
- 解析端（``parse_to_epoch`` / ``parse_date_to_epoch``）按 UTC 解释字符串，返回
  绝对 epoch 秒，可直接与 ``time.time()`` 比较。
- 生产端与解析端必须成对使用，避免本地时区/UTC 混用导致的偏移。
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone

# 持久化时间戳的统一格式（语义为 UTC）。
FMT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    """当前 UTC 时间（tz-aware）。"""
    return datetime.now(timezone.utc)


def now_str() -> str:
    """当前 UTC 时间字符串，格式 ``YYYY-MM-DD HH:MM:SS``。"""
    return now().strftime(FMT)


def future_str(seconds: int) -> str:
    """相对当前时间偏移 ``seconds`` 秒后的 UTC 时间字符串。"""
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).strftime(FMT)


def parse_to_epoch(value: str) -> float:
    """把存储的 UTC 时间字符串解析为 epoch 秒。解析失败返回 0。

    与 ``time.time()`` 同为绝对 epoch，可安全比较。
    """
    try:
        return float(calendar.timegm(time.strptime(value, FMT)))
    except Exception:
        return 0.0


def parse_date_to_epoch(value: str) -> float:
    """按 UTC 解析仅日期部分（``YYYY-MM-DD``）为该日 00:00:00 UTC 的 epoch 秒。

    兼容完整时间戳字符串（取前 10 位）。解析失败返回 0。
    """
    try:
        return float(calendar.timegm(time.strptime(value[:10], "%Y-%m-%d")))
    except Exception:
        return 0.0
