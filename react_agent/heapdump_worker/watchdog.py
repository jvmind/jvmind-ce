"""看门狗：扫描心跳超时的 PARSING 任务，attempts < N 时重置为 QUEUED 供重试。

规则（对齐 IMPLEMENTATION_GUIDE §4.4）：
- heartbeat 时间戳（UTC）落后 now 超过 HEAPDUMP_WORKER_HEARTBEAT_TIMEOUT 秒 → 判定死亡
- attempts < HEAPDUMP_WORKER_MAX_ATTEMPTS（默认 3）→ 重置 QUEUED, attempts += 1
- attempts >= 上限 → 置 FAILED
- 看门狗不清理 *.index（worker 可能仍活着，误删会破坏正在写的文件）
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from sqlalchemy import text

from ..db import SessionLocal
from ..timeutil import now, now_str, parse_to_epoch

_logger = logging.getLogger(__name__)

_HEARTBEAT_TIMEOUT = float(os.getenv("HEAPDUMP_WORKER_HEARTBEAT_TIMEOUT", "300"))  # 秒
_MAX_ATTEMPTS = int(os.getenv("HEAPDUMP_WORKER_MAX_ATTEMPTS", "3"))
_WATCHDOG_INTERVAL = float(os.getenv("HEAPDUMP_WATCHDOG_INTERVAL", "60"))


def _sweep_once() -> int:
    """扫一遍。返回处理的任务数（用于日志/测试）。"""
    import time as _time
    now_ts = _time.time()
    handled = 0
    db = SessionLocal()
    try:
        # 拉出所有 PARSING 记录（数量应远少于总量，直接查即可）
        rows = db.execute(text(
            "SELECT id, heartbeat, attempts FROM heapdump_reports WHERE status = 'PARSING'"
        )).mappings().all()
        for row in rows:
            hb_ts = parse_to_epoch(row["heartbeat"]) if row["heartbeat"] else 0.0
            if hb_ts == 0.0:
                # 没有心跳：把 started_at 当心跳兜底
                r2 = db.execute(text(
                    "SELECT started_at FROM heapdump_reports WHERE id = :rid"
                ), {"rid": row["id"]}).mappings().first()
                hb_ts = parse_to_epoch(r2["started_at"]) if r2 and r2["started_at"] else 0.0
            if hb_ts == 0.0 or now_ts - hb_ts <= _HEARTBEAT_TIMEOUT:
                continue

            attempts = int(row["attempts"] or 0)
            rid = row["id"]
            if attempts + 1 >= _MAX_ATTEMPTS:
                # 超上限 → FAILED
                db.execute(text(
                    "UPDATE heapdump_reports "
                    "SET status = 'FAILED', attempts = :att, worker_id = '', "
                    "    finished_at = :now, error = :err "
                    "WHERE id = :rid AND status = 'PARSING'"
                ), {
                    "att": attempts + 1,
                    "now": now_str(),
                    "err": f"worker died (heartbeat lost > {int(_HEARTBEAT_TIMEOUT)}s) after {_MAX_ATTEMPTS} attempts",
                    "rid": rid,
                })
                _logger.warning("[watchdog] rid=%s → FAILED (max attempts)", rid)
            else:
                # 重置为 QUEUED 供重试
                db.execute(text(
                    "UPDATE heapdump_reports "
                    "SET status = 'QUEUED', attempts = :att, worker_id = '', heartbeat = '' "
                    "WHERE id = :rid AND status = 'PARSING'"
                ), {"att": attempts + 1, "rid": rid})
                _logger.warning("[watchdog] rid=%s → QUEUED (attempt %d)", rid, attempts + 1)
            handled += 1
        db.commit()
    except Exception:
        db.rollback()
        _logger.exception("[watchdog] sweep failed")
    finally:
        db.close()
    return handled


async def watchdog_loop(stop_event: asyncio.Event) -> None:
    """看门狗主循环。收到 stop_event 优雅退出。"""
    _logger.info("[watchdog] started interval=%ss timeout=%ss max_attempts=%s",
                 _WATCHDOG_INTERVAL, _HEARTBEAT_TIMEOUT, _MAX_ATTEMPTS)
    while not stop_event.is_set():
        try:
            n = await asyncio.get_event_loop().run_in_executor(None, _sweep_once)
            if n:
                _logger.info("[watchdog] handled %d stale task(s)", n)
        except Exception:
            _logger.exception("[watchdog] iteration error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_WATCHDOG_INTERVAL)
        except asyncio.TimeoutError:
            continue
    _logger.info("[watchdog] stopped")
