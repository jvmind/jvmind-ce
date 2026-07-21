"""任务抢占：PostgreSQL 用 FOR UPDATE SKIP LOCKED，SQLite 单进程模式退化为简单 SELECT。

多 Worker 部署时依赖 PG 的行级锁天然互斥；SQLite 只用于本地开发/单进程测试。
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

from sqlalchemy import text

from ..db import SessionLocal, engine
from ..models import HeapdumpReportModel
from ..timeutil import now_str

_logger = logging.getLogger(__name__)


def worker_id() -> str:
    """当前进程的 worker 标识。写入 heapdump_reports.worker_id。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def _is_postgres() -> bool:
    try:
        return engine.url.get_backend_name().startswith("postgres")
    except Exception:
        return False


def claim_next_task() -> Optional[dict]:
    """抢一个 QUEUED 任务并置为 PARSING，返回 heapdump 记录 dict；无任务返回 None。

    - PostgreSQL: SELECT ... FOR UPDATE SKIP LOCKED（多 worker 天然互斥）。
    - SQLite: 简单 SELECT LIMIT 1，仅供单进程开发/测试。

    幂等：如果同一记录同时被两个 worker 抢到（SQLite 场景），后 update 者会看到状态已变化，
    我们通过在 UPDATE 时带 WHERE status='QUEUED' 二次校验来防止双消费。
    """
    wid = worker_id()
    db = SessionLocal()
    try:
        if _is_postgres():
            row = db.execute(text(
                "SELECT id FROM heapdump_reports "
                "WHERE status = 'QUEUED' "
                "ORDER BY queued_at ASC "
                "LIMIT 1 "
                "FOR UPDATE SKIP LOCKED"
            )).mappings().first()
        else:
            row = db.execute(text(
                "SELECT id FROM heapdump_reports "
                "WHERE status = 'QUEUED' "
                "ORDER BY queued_at ASC "
                "LIMIT 1"
            )).mappings().first()

        if not row:
            return None

        rid = row["id"]
        now = now_str()
        # 条件 UPDATE：仅当仍为 QUEUED 才转 PARSING，避免竞争
        result = db.execute(text(
            "UPDATE heapdump_reports "
            "SET status = 'PARSING', started_at = :now, heartbeat = :now, worker_id = :wid "
            "WHERE id = :rid AND status = 'QUEUED'"
        ), {"now": now, "wid": wid, "rid": rid})
        if result.rowcount == 0:
            db.rollback()
            return None
        db.commit()

        # 重读完整记录
        r = db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == rid).one()
        return _row_to_dict(r)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _row_to_dict(r: HeapdumpReportModel) -> dict:
    import json as _json
    return {
        "id": r.id,
        "session_id": r.session_id,
        "user_id": r.user_id,
        "org_id": r.org_id,
        "filename": r.filename,
        "size": r.size,
        "status": r.status,
        "progress": r.progress or 0,
        "phase": r.phase or "",
        "dump_dir": r.dump_dir or "",
        "parse_args": _json.loads(r.parse_args) if r.parse_args else {},
        "attempts": r.attempts or 0,
        "worker_id": r.worker_id or "",
    }
