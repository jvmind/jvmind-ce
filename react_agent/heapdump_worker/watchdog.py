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
import shutil
from datetime import timedelta
from pathlib import Path

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

        # 扫 CANCEL_REQUESTED：worker 已死（心跳超时）时没有任何代码会把它转成终态，
        # 这里落 CANCELLED 并清理 dump_dir，否则任务永久卡在非终态（前端无限转圈）。
        cancel_rows = db.execute(text(
            "SELECT id, heartbeat, started_at, dump_dir FROM heapdump_reports "
            "WHERE status = 'CANCEL_REQUESTED'"
        )).mappings().all()
        for row in cancel_rows:
            hb_ts = parse_to_epoch(row["heartbeat"]) if row["heartbeat"] else 0.0
            if hb_ts == 0.0:
                hb_ts = parse_to_epoch(row["started_at"]) if row["started_at"] else 0.0
            if hb_ts != 0.0 and now_ts - hb_ts <= _HEARTBEAT_TIMEOUT:
                # 心跳还新鲜：worker 可能马上处理取消，稍后再扫
                continue
            rid = row["id"]
            db.execute(text(
                "UPDATE heapdump_reports "
                "SET status = 'CANCELLED', worker_id = '', finished_at = :now, error = 'user cancelled' "
                "WHERE id = :rid AND status = 'CANCEL_REQUESTED'"
            ), {"rid": rid, "now": now_str()})
            dump_dir = row["dump_dir"] or ""
            if dump_dir:
                try:
                    p = Path(dump_dir)
                    if p.exists() and p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    _logger.warning("[watchdog] failed to clean dump_dir on cancel rid=%s", rid, exc_info=True)
            _logger.warning("[watchdog] rid=%s → CANCELLED (stale cancel request)", rid)
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
