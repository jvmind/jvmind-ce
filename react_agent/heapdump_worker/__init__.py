"""Heapdump 解析 Worker（独立进程）。

启动：
    python -m react_agent.heapdump_worker

架构定位：
- 独立于 Web 进程，通过 PostgreSQL SKIP LOCKED 抢任务，天然支持水平扩展。
- 单次抢一个 QUEUED 任务 → PARSING → 调 MAT ParseHeapDump（V1 文本进度）→ DONE/FAILED/CANCELLED。
- DONE 后调 query-service /overview 回填 stats。
- 后台看门狗协程重置心跳超时的僵尸任务。

对齐 EXECUTION_PLAN P2 与 mat-study/07 §10。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .claim import claim_next_task, worker_id
from .heartbeat import beat, get_heartbeat_interval, register, unregister
from .parse import run_parse_task
from .stats import backfill_stats
from .watchdog import watchdog_loop

__all__ = ["worker_loop", "watchdog_loop", "worker_id"]

_logger = logging.getLogger(__name__)

# 无任务时的轮询间隔
_IDLE_INTERVAL = float(os.getenv("HEAPDUMP_WORKER_IDLE_INTERVAL", "5"))


async def worker_loop(stop_event: asyncio.Event) -> None:
    """Worker 主循环：抢任务 → 解析 → 回填 stats。收到 stop_event 优雅退出。"""
    wid = worker_id()
    _logger.info("[worker] started id=%s idle_interval=%ss", wid, _IDLE_INTERVAL)
    register(wid)

    hb_interval = get_heartbeat_interval()
    last_hb = 0.0
    import time as _time

    try:
        while not stop_event.is_set():
            now_ts = _time.time()
            if now_ts - last_hb >= hb_interval:
                try:
                    await asyncio.get_event_loop().run_in_executor(None, beat, wid, "")
                except Exception:
                    _logger.exception("[worker] heartbeat failed")
                last_hb = now_ts

            try:
                task = await asyncio.get_event_loop().run_in_executor(None, claim_next_task)
            except Exception:
                _logger.exception("[worker] claim_next_task failed, backing off")
                await _sleep_or_stop(stop_event, _IDLE_INTERVAL)
                continue

            if not task:
                await _sleep_or_stop(stop_event, _IDLE_INTERVAL)
                continue

            rid = task["id"]
            try:
                await asyncio.get_event_loop().run_in_executor(None, beat, wid, rid)
            except Exception:
                pass
            last_hb = _time.time()

            final_status = None
            try:
                final_status = await run_parse_task(task)
            except Exception:
                _logger.exception("[worker] run_parse_task crashed rid=%s", rid)
                try:
                    await asyncio.get_event_loop().run_in_executor(None, beat, wid, "")
                except Exception:
                    pass
                continue

            if final_status == "DONE":
                stop_hb = asyncio.Event()

                async def _stats_heartbeat():
                    from .parse import _update_fields
                    from ..timeutil import now_str
                    interval = float(os.getenv("HEAPDUMP_WORKER_HEARTBEAT_INTERVAL", "30"))
                    while not stop_hb.is_set():
                        _update_fields(rid, heartbeat=now_str())
                        try:
                            beat(wid, rid)
                        except Exception:
                            pass
                        try:
                            await asyncio.wait_for(stop_hb.wait(), timeout=interval)
                        except asyncio.TimeoutError:
                            continue

                hb_stats = asyncio.create_task(_stats_heartbeat())
                backfill_ok = False
                try:
                    stats = await backfill_stats(rid, task["dump_dir"])
                    backfill_ok = bool(stats)
                except Exception:
                    _logger.exception("[worker] backfill_stats crashed rid=%s", rid)
                finally:
                    stop_hb.set()
                    try:
                        await hb_stats
                    except Exception:
                        pass
                from .parse import _update_fields
                from ..timeutil import now_str
                if backfill_ok:
                    _update_fields(rid, status="DONE", progress=100, phase="DONE",
                                   finished_at=now_str(), error="")
                    _logger.info("[worker] rid=%s DONE", rid)
                else:
                    _update_fields(rid, status="FAILED",
                                   finished_at=now_str(),
                                   error="failed to load overview stats from MAT")
                    _logger.warning("[worker] rid=%s backfill_stats failed", rid)
            elif final_status == "CANCELLED":
                pass
            else:
                pass

            try:
                await asyncio.get_event_loop().run_in_executor(None, beat, wid, "")
            except Exception:
                pass
            last_hb = _time.time()
    finally:
        unregister(wid)
        _logger.info("[worker] stopped")


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return
