"""单任务解析编排：fork MAT 解析子进程 + 进度实时回写 DB + cancel 支持 + 心跳。

对齐 mat-study/07 §11 状态机与 EXECUTION_PLAN P2.2/P2.3：
- 从 parse_args.hprof_file / hprof_kind 决定 hprof 路径（P1 上传阶段已写入）。
- 支持 .hprof / .hprof.gz：MAT 靠扩展名自动解压。
- 每次进度更新节流到 ≥1s 一次（避免每点写 DB）。
- 后台心跳协程每 30s 更新 heartbeat，供 watchdog 判活。
- 每 2s 探测一次 CANCEL_REQUESTED → kill 子进程 → 清理 *.index。
- 退出码：0=DONE, 79=FAILED(OOM), 130=CANCELLED, 其他=FAILED。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from ..db import SessionLocal
from ..models import HeapdumpReportModel
from ..timeutil import now_str
from .mat_parse_runner import run_parse_v1, _clean_partial_indexes

_logger = logging.getLogger(__name__)

# 心跳间隔
_HEARTBEAT_INTERVAL = float(os.getenv("HEAPDUMP_WORKER_HEARTBEAT_INTERVAL", "30"))
# cancel 探测间隔
_CANCEL_POLL_INTERVAL = float(os.getenv("HEAPDUMP_WORKER_CANCEL_POLL", "2"))
# 进度写库节流（秒）
_PROGRESS_THROTTLE = float(os.getenv("HEAPDUMP_WORKER_PROGRESS_THROTTLE", "1"))
# 单次解析墙钟超时（秒）。MAT 正常解析动辄几十分钟，默认 24h 防极端挂死。
_PARSE_TIMEOUT = float(os.getenv("HEAPDUMP_WORKER_PARSE_TIMEOUT", "86400"))


def _update_fields(report_id: str, *, where_status: Optional[str] = None, **fields) -> bool:
    """轻量级 UPDATE（不走 ORM，减少一次 SELECT）。

    ``where_status`` 提供乐观并发守卫：仅当行仍处于该状态时才更新并 commit。
    返回是否更新成功——用于防止一个 worker 的终态写入覆盖另一个 worker 已认领
    的 PARSING 状态（watchdog 重置 + 重认领的竞争窗口）。
    """
    if not fields:
        return True
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields)
    params["rid"] = report_id
    db = SessionLocal()
    try:
        if where_status:
            set_clause += ", heartbeat = heartbeat"
            result = db.execute(
                text(f"UPDATE heapdump_reports SET {set_clause} WHERE id = :rid AND status = :expected_status"),
                {**params, "expected_status": where_status},
            )
            if result.rowcount == 0:
                db.rollback()
                return False
        else:
            db.execute(text(f"UPDATE heapdump_reports SET {set_clause} WHERE id = :rid"), params)
        db.commit()
        return True
    except Exception:
        db.rollback()
        _logger.warning("update_fields failed rid=%s fields=%s", report_id, list(fields.keys()), exc_info=True)
        return False
    finally:
        db.close()


def _get_status(report_id: str) -> Optional[str]:
    db = SessionLocal()
    try:
        row = db.query(HeapdumpReportModel.status).filter(HeapdumpReportModel.id == report_id).first()
        return row[0] if row else None
    except Exception:
        # DB 故障期间保守返回 None（视为"未请求取消"），绝不让取消探测协程因
        # 异常静默死亡——那会让用户彻底失去取消能力。
        _logger.warning("get_status failed rid=%s", report_id, exc_info=True)
        return None
    finally:
        db.close()


def _finish_terminal(report_id: str, status: str, error: str, finished_at: str) -> None:
    """把任务落为终态（CANCELLED/FAILED）。先读当前状态再写：若已被 watchdog 重置
    或另一个 worker 认领（状态不再处于 PARSING/CANCEL_REQUESTED），则不改动，避免
    覆盖并发状态机转换。"""
    current = _get_status(report_id)
    if current not in (None, "PARSING", "CANCEL_REQUESTED"):
        return
    _update_fields(report_id, status=status, error=error, finished_at=finished_at)


def _acquire_dump_lock(dump_dir: str) -> Optional[int]:
    """对 dump_dir 加排它 flock，防止孤儿 MAT 进程与重新认领的 worker 并发写同一目录。

    返回 fd（调用方持有直到解析结束）；锁被占用时返回 None。进程退出时 fd 自动关闭，
    锁自动释放——kill -9 的 worker 留下的孤儿 java 进程会持有锁，新 worker 据此拒绝
    并发写同一批 index 文件。
    """
    try:
        import fcntl
    except ImportError:  # Windows — 退化为无锁
        return -1
    lock_path = os.path.join(dump_dir, ".worker.lock")
    try:
        fd = open(lock_path, "w")
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        return None


def _release_dump_lock(fd) -> None:
    if fd is None:
        return
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fd.close()
    except Exception:
        pass


async def run_parse_task(task: dict) -> str:
    """执行一个 heapdump 解析任务，返回最终 status（DONE/FAILED/CANCELLED）。

    task 结构见 claim._row_to_dict。
    """
    rid = task["id"]
    dump_dir = task["dump_dir"]
    parse_args = task.get("parse_args") or {}
    hprof_file = parse_args.get("hprof_file") or "app.hprof"
    hprof_path = str(Path(dump_dir) / hprof_file)
    xmx = parse_args.get("xmx") or os.getenv("MAT_PARSE_XMX", "4g")
    mat_home = parse_args.get("mat_home") or os.getenv("MAT_HOME", "/opt/mat")

    # 就绪校验
    if not Path(hprof_path).is_file():
        err = f"hprof file missing: {hprof_path}"
        _logger.error("[worker] rid=%s %s", rid, err)
        _update_fields(rid, status="FAILED", error=err, finished_at=now_str())
        return "FAILED"

    if not Path(mat_home).is_dir():
        err = f"MAT_HOME missing: {mat_home}"
        _logger.error("[worker] rid=%s %s", rid, err)
        _update_fields(rid, status="FAILED", error=err, finished_at=now_str())
        return "FAILED"

    # dump_dir 排它锁：防止孤儿 MAT 进程与重新认领的 worker 并发写同一批 index。
    lock_fd = await asyncio.get_event_loop().run_in_executor(None, _acquire_dump_lock, dump_dir)
    if lock_fd is None:
        err = "dump_dir is locked by another MAT process (orphaned worker?); refusing concurrent write"
        _logger.error("[worker] rid=%s %s", rid, err)
        _update_fields(rid, status="FAILED", error=err, finished_at=now_str())
        return "FAILED"

    # 状态：进度节流
    _last_progress_write = {"t": 0.0, "p": -1.0, "phase": ""}

    async def on_update(progress: Optional[float], phase: str) -> None:
        if progress is None:
            return
        # 节流：距上次 ≥1s 或 phase 变化才写库
        loop_t = asyncio.get_event_loop().time()
        if (
            loop_t - _last_progress_write["t"] < _PROGRESS_THROTTLE
            and phase == _last_progress_write["phase"]
        ):
            return
        _last_progress_write["t"] = loop_t
        _last_progress_write["p"] = progress
        _last_progress_write["phase"] = phase
        # progress 存 0-100 整数（对齐 P1 schema）
        pct = int(round(progress * 100)) if progress <= 1.0 else int(round(progress))
        _update_fields(rid, progress=max(0, min(100, pct)), phase=phase or "")

    async def on_message(msg: str) -> None:
        _logger.info("[worker] rid=%s parse-msg: %s", rid, msg)

    async def should_cancel() -> bool:
        status = _get_status(rid)
        return status == "CANCEL_REQUESTED"

    # 心跳协程
    stop_heartbeat = asyncio.Event()

    async def heartbeat_loop():
        while not stop_heartbeat.is_set():
            _update_fields(rid, heartbeat=now_str())
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                continue

    hb = asyncio.create_task(heartbeat_loop())

    try:
        _logger.info(
            "[worker] rid=%s parse start hprof=%s xmx=%s mat_home=%s",
            rid, hprof_path, xmx, mat_home,
        )
        try:
            final = await asyncio.wait_for(
                run_parse_v1(
                    mat_home=mat_home,
                    hprof_path=hprof_path,
                    dump_dir=dump_dir,
                    xmx=xmx,
                    on_update=on_update,
                    on_message=on_message,
                    should_cancel=should_cancel,
                ),
                timeout=_PARSE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # 墙钟超时：wait_for 已取消 run_parse_v1（其 finally 会 kill 进程组），
            # 这里清理半成品并落 FAILED，避免任务无限挂起。
            _logger.error("[worker] rid=%s parse exceeded timeout %ss", rid, _PARSE_TIMEOUT)
            _clean_partial_indexes(dump_dir, os.path.basename(hprof_path))
            _update_fields(rid, status="FAILED", error=f"parse timeout after {int(_PARSE_TIMEOUT)}s",
                           finished_at=now_str(), where_status="PARSING")
            return "FAILED"
        except asyncio.CancelledError:
            # 优雅停机：run_parse_v1 的 finally 已 kill 进程组并清理。标记 FAILED 防止
            # watchdog 稍后把半成品任务复活，然后继续向上传播取消信号。
            _clean_partial_indexes(dump_dir, os.path.basename(hprof_path))
            _update_fields(rid, status="FAILED", error="worker shutdown during parse",
                           finished_at=now_str(), where_status="PARSING")
            raise
    except Exception as e:
        _logger.exception("[worker] rid=%s parse crashed", rid)
        _clean_partial_indexes(dump_dir, os.path.basename(hprof_path))
        _update_fields(rid, status="FAILED", error=f"parser crash: {e}"[:500], finished_at=now_str())
        return "FAILED"
    finally:
        stop_heartbeat.set()
        try:
            await hb
        except Exception:
            pass
        await asyncio.get_event_loop().run_in_executor(None, _release_dump_lock, lock_fd)

    # 处理最终状态 —— DONE 状态会在 __init__ 中 backfill_stats 之后再提交，
    # 此处仅写中间状态 INDEXED，防止用户看到空白卡片时误以为解析失败。
    now = now_str()
    # 处理最终状态 —— DONE 状态会在 __init__ 中 backfill_stats 之后再提交，
    # 此处仅写中间状态 PARSING(phase=stats)，防止用户看到空白卡片时误以为解析失败。
    now = now_str()
    if final == "DONE":
        _update_fields(rid, status="PARSING", progress=100, phase="stats",
                       error="", where_status="PARSING")
        _logger.info("[worker] rid=%s INDEXED (awaiting stats backfill)", rid)
        return "DONE"
    if final == "CANCELLED":
        _finish_terminal(rid, "CANCELLED", "user cancelled", now)
        _logger.info("[worker] rid=%s CANCELLED", rid)
        return "CANCELLED"
    # FAILED
    _finish_terminal(rid, "FAILED", "parse failed (see worker log)", now)
    _logger.warning("[worker] rid=%s FAILED", rid)
    return "FAILED"
