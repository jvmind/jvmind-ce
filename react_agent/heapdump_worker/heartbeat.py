"""Worker 注册表心跳：worker_loop 周期性 UPSERT heapdump_workers 表，admin 据此判断存活。"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

from ..db import SessionLocal
from ..timeutil import now_str

_logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = float(os.getenv("HEAPDUMP_WORKER_HEARTBEAT_INTERVAL", "30"))


def _heartbeat(wid: str, current_task_id: str = "", last_error: str = "", started: bool = False) -> None:
    """写一次心跳。called from executor thread (sync)."""
    db = SessionLocal()
    try:
        from ..models import HeapdumpWorkerModel
        existing = db.query(HeapdumpWorkerModel).filter(HeapdumpWorkerModel.worker_id == wid).first()
        if existing:
            existing.last_heartbeat = now_str()
            if current_task_id is not None:
                existing.current_task_id = current_task_id
            if last_error:
                existing.last_error = last_error
            db.commit()
        else:
            hostname = socket.gethostname()
            try:
                pid = int(wid.split(":")[-1]) if ":" in wid else 0
            except Exception:
                pid = 0
            db.add(HeapdumpWorkerModel(
                worker_id=wid,
                hostname=hostname,
                pid=pid,
                started_at=now_str() if started else now_str(),
                last_heartbeat=now_str(),
                current_task_id=current_task_id or "",
                last_error=last_error or "",
            ))
            db.commit()
    except Exception:
        _logger.exception("[worker-heartbeat] failed wid=%s", wid)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def beat(wid: str, current_task_id: str = "") -> None:
    """Periodic heartbeat — use during idle loop and task processing."""
    _heartbeat(wid, current_task_id=current_task_id, started=False)


def register(wid: str) -> None:
    """Register worker on startup."""
    _heartbeat(wid, current_task_id="", started=True)
    _logger.info("[worker-heartbeat] registered wid=%s", wid)


def unregister(wid: str) -> None:
    """Remove worker row on graceful shutdown."""
    db = SessionLocal()
    try:
        from ..models import HeapdumpWorkerModel
        db.query(HeapdumpWorkerModel).filter(HeapdumpWorkerModel.worker_id == wid).delete()
        db.commit()
    except Exception:
        _logger.exception("[worker-heartbeat] unregister failed wid=%s", wid)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def get_heartbeat_interval() -> float:
    return _HEARTBEAT_INTERVAL
