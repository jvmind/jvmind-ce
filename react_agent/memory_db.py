"""DatabaseMemory — 替代 JSONMemory 的数据库实现

接口完全兼容 JSONMemory（memory.py），调用方无感知。
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import (
    FactModel,
    GCReportModel,
    HeapdumpReportModel,
    JStackReportModel,
    MessageModel,
    SessionModel,
)

_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    from .timeutil import now_str
    return now_str()


def _new_id(prefix: str = "", length: int = 12) -> str:
    return prefix + uuid.uuid4().hex[:length]


class DatabaseMemory:
    """数据库版的 JSONMemory，接口签名完全一致。"""

    def __init__(self, user_id: str = "", session_dir: str = "./sessions", db_session: Optional[Session] = None) -> None:
        self._user_id = user_id
        self._local = threading.local()
        if db_session is not None:
            self._local.db = db_session
        # session_dir 保留仅用于接口兼容，不再使用

    # ---- 内部工具 ----
    @property
    def db(self) -> Session:
        db = getattr(self._local, "db", None)
        if db is not None:
            return db
        db = SessionLocal()
        self._local.db = db
        return db

    def close(self) -> None:
        db = getattr(self._local, "db", None)
        if db:
            try:
                db.close()
            except Exception:
                pass
            self._local.db = None

    # ---------- 会话管理 ----------
    def list_sessions(self) -> List[Dict[str, Any]]:
        try:
            query = self.db.query(SessionModel).filter(SessionModel.user_id == self._user_id)
            rows = query.order_by(SessionModel.updated_at.desc()).all()
            items = []
            for r in rows:
                msg_count = self.db.query(MessageModel).filter(
                    MessageModel.session_id == r.id
                ).count()
                items.append({
                    "id": r.id,
                    "title": r.title,
                    "updated_at": r.updated_at or "",
                    "msg_count": msg_count,
                    "user_id": r.user_id,
                })
            return items
        finally:
            self.close()

    def create_session(self, title: Optional[str] = None) -> str:
        try:
            sid = _new_id("", 12)
            now = _now_iso()
            session = SessionModel(
                id=sid,
                user_id=self._user_id,
                title=title or f"Session {sid[:6]}",
                created_at=now,
                updated_at=now,
            )
            self.db.add(session)
            self.db.commit()
            return sid
        finally:
            self.close()

    def delete_session(self, session_id: str) -> bool:
        try:
            s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not s:
                return False
            if s.user_id and s.user_id != self._user_id:
                return False

            # Collect files to clean up before cascade delete removes report rows.
            gc_file_ids = [r.file_id for r in (s.gc_reports or []) if r.file_id]
            jstack_file_ids = [r.file_id for r in (s.jstack_reports or []) if r.file_id]
            file_ids = list(set(gc_file_ids + jstack_file_ids))
            dump_dirs = [r.dump_dir for r in (s.heapdump_reports or []) if r.dump_dir]

            # Delete the session (cascade deletes messages/facts/reports).
            self.db.delete(s)
            self.db.commit()

            # Clean up uploaded file records once reports are gone.
            for fid in file_ids:
                self._delete_uploaded_file_if_orphan(fid)

            # Clean up heapdump dump dirs.
            for ddir in dump_dirs:
                try:
                    p = Path(ddir)
                    if p.exists() and p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    _logger.warning("failed to remove dump_dir=%s", ddir, exc_info=True)

            return True
        finally:
            self.close()

    def _delete_uploaded_file_if_orphan(self, file_id: str) -> None:
        """Delete uploaded file record + storage only if no report references it anymore."""
        if not file_id:
            return
        try:
            from .models import UploadedFileModel
            from .upload_storage import delete_uploaded_text

            # Check remaining references in other sessions.
            remaining = (
                self.db.query(GCReportModel).filter(GCReportModel.file_id == file_id).count()
                + self.db.query(JStackReportModel).filter(JStackReportModel.file_id == file_id).count()
            )
            if remaining > 0:
                return

            row = self.db.query(UploadedFileModel).filter(UploadedFileModel.file_id == file_id).first()
            if row:
                delete_uploaded_text(row.storage_backend or "db", row.storage_key or "")
                self.db.delete(row)
                self.db.commit()
        except Exception:
            _logger.exception("Failed to delete uploaded file record: %s", file_id)

    def get_uploaded_text(self, file_id: str) -> str:
        """Return the uploaded file text by file_id from the DB.

        Falls back to empty string on missing record, missing storage blob,
        or storage read error. No in-memory caching — callers that need
        caching should add their own layer.
        """
        if not file_id:
            return ""
        try:
            from .models import UploadedFileModel
            from .upload_storage import load_uploaded_text
            row = self.db.query(UploadedFileModel).filter(UploadedFileModel.file_id == file_id).first()
            if not row:
                return ""
            return load_uploaded_text(
                row.storage_backend or "", row.storage_key or "", fallback=""
            ) or ""
        except Exception:
            _logger.exception("get_uploaded_text failed for file_id=%s", file_id)
            return ""

    def rename_session(self, session_id: str, title: str) -> None:
        try:
            s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not s:
                return
            if s.user_id and s.user_id != self._user_id :
                return
            s.title = title
            s.updated_at = _now_iso()
            self.db.commit()
        finally:
            self.close()

    # ---------- 读写 ----------
    def load(self, session_id: str) -> Dict[str, Any]:
        try:
            s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not s:
                return {
                    "id": session_id,
                    "title": f"Session {session_id[:6]}",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "messages": [],
                    "facts": [],
                    "gc_reports": [],
                    "jstack_reports": [],
                    "heapdump_reports": [],
                    "user_id": "",
                }
            messages = [
                {"id": m.id, "role": m.role, "content": m.content, "ts": m.ts}
                for m in self.db.query(MessageModel)
                .filter(MessageModel.session_id == session_id)
                .order_by(MessageModel.id)
                .all()
            ]
            # 用 get_facts() 过滤掉 [context:] 系统上下文，只返回用户记忆。
            # 与 GET /api/sessions/{sid}/facts 端点保持一致，避免会话详情泄露内部上下文。
            facts = self.get_facts(session_id)
            s_id = s.id
            s_title = s.title or ""
            s_created_at = s.created_at or ""
            s_updated_at = s.updated_at or ""
            s_user_id = s.user_id or ""
            # removed: org_id
            gc_reports = self._load_gc_reports(session_id)
            jstack_reports = self._load_jstack_reports(session_id)
            heapdump_reports = self._load_heapdump_reports(session_id)
            return {
                "id": s_id,
                "title": s_title,
                "created_at": s_created_at,
                "updated_at": s_updated_at,
                "messages": messages,
                "facts": facts,
                "gc_reports": gc_reports,
                "jstack_reports": jstack_reports,
                "heapdump_reports": heapdump_reports,
                "user_id": s_user_id,
                
            }

        finally:
            self.close()
    def _load_gc_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(GCReportModel)
                .filter(GCReportModel.session_id == session_id)
                .order_by(GCReportModel.created_at)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "size": r.size,
                    "file_id": r.file_id,
                    "stats": json.loads(r.stats) if r.stats else {},
                    "ai_conclusion": r.ai_conclusion or "",
                    "created_at": r.created_at or "",
                }
                for r in rows
            ]

        finally:
            self.close()
    def _load_jstack_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(JStackReportModel)
                .filter(JStackReportModel.session_id == session_id)
                .order_by(JStackReportModel.created_at)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "size": r.size,
                    "file_id": r.file_id,
                    "stats": json.loads(r.stats) if r.stats else {},
                    "ai_conclusion": r.ai_conclusion or "",
                    "created_at": r.created_at or "",
                }
                for r in rows
            ]

        finally:
            self.close()
    # ---------- 消息 ----------
    def append_message(self, session_id: str, role: str, content: str) -> Optional[int]:
        try:
            msg = MessageModel(session_id=session_id, role=role, content=content, ts=_now_iso())
            self.db.add(msg)
            # 如果是第一条用户消息，自动更新标题
            if role == "user":
                s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
                if s and s.title and s.title.startswith("Session "):
                    snippet = content.strip().splitlines()[0][:20]
                    if snippet:
                        s.title = snippet
                if s:
                    s.updated_at = _now_iso()
            else:
                s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
                if s:
                    s.updated_at = _now_iso()
            self.db.commit()
            return msg.id

        finally:
            self.close()
    def get_messages(self, session_id: str) -> List[Dict[str, str]]:
        try:
            rows = (
                self.db.query(MessageModel)
                .filter(MessageModel.session_id == session_id)
                .order_by(MessageModel.id)
                .all()
            )
            return [{"role": m.role, "content": m.content} for m in rows]

        finally:
            self.close()
    def clear_messages(self, session_id: str) -> None:
        try:
            s = self.db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not s:
                return
            if s.user_id and s.user_id != self._user_id :
                return
            self.db.query(MessageModel).filter(MessageModel.session_id == session_id).delete()
            s.updated_at = _now_iso()
            self.db.commit()

        finally:
            self.close()
    # ---------- 长期记忆（facts） ----------
    def add_fact(self, session_id: str, fact: str) -> None:
        try:
            fact = fact.strip()
            if not fact:
                return
            exists = (
                self.db.query(FactModel)
                .filter(FactModel.session_id == session_id, FactModel.content == fact)
                .first()
            )
            if not exists:
                self.db.add(FactModel(session_id=session_id, content=fact))
                self.db.commit()

        finally:
            self.close()
    def set_context_fact(self, session_id: str, key: str, fact: str) -> None:
        try:
            prefix = f"[context:{key}] "
            self.db.query(FactModel).filter(
                FactModel.session_id == session_id,
                FactModel.content.like(prefix + "%"),
            ).delete(synchronize_session=False)
            fact = fact.strip()
            if fact:
                self.db.add(FactModel(session_id=session_id, content=prefix + fact))
            self.db.commit()

        finally:
            self.close()
    def get_context_fact(self, session_id: str, key: str) -> str:
        """Read a fact previously stored via ``set_context_fact(session_id, key, ...)``.

        Returns the bare value (without the ``[context:KEY]`` prefix) or ``""`` if
        the key is absent. Never raises.
        """
        try:
            prefix = f"[context:{key}] "
            row = (
                self.db.query(FactModel)
                .filter(
                    FactModel.session_id == session_id,
                    FactModel.content.like(prefix + "%"),
                )
                .order_by(FactModel.id.desc())
                .first()
            )
            if row is None:
                return ""
            return row.content[len(prefix):]
        except Exception:
            return ""
        finally:
            self.close()
    def get_prompt_facts(self, session_id: str, model: str = "deepseek-chat",
                        budget_tokens: int = 800) -> List[Any]:
        from .memory.facts import scored_facts
        try:
            return scored_facts(self, session_id, model=model, budget_tokens=budget_tokens)
        except Exception:
            _logger.exception("scored_facts failed; falling back to legacy unfiltered facts")
            try:
                rows = (
                    self.db.query(FactModel)
                    .filter(FactModel.session_id == session_id)
                    .order_by(FactModel.id)
                    .all()
                )
                return [r.content for r in rows]
            finally:
                self.close()
    def get_facts(self, session_id: str) -> List[str]:
        items = self.get_prompt_facts(session_id)
        out: List[str] = []
        for item in items:
            if isinstance(item, dict):
                text = item.get("text", "") or ""
            else:
                text = str(item)
            if not text.startswith("[context:"):
                out.append(text)
        return out

    def remove_fact(self, session_id: str, index: int) -> bool:
        try:
            rows = (
                self.db.query(FactModel)
                .filter(FactModel.session_id == session_id)
                .order_by(FactModel.id)
                .all()
            )
            visible = [r for r in rows if not str(r.content).startswith("[context:")]
            if 0 <= index < len(visible):
                self.db.delete(visible[index])
                self.db.commit()
                return True
            return False

        finally:
            self.close()
    # ---------- GC 报告 ----------
    def add_gc_report(self, session_id: str, report: Dict[str, Any]) -> str:
        try:
            rid = _new_id("", 10)
            now = _now_iso()
            r = GCReportModel(
                id=rid,
                session_id=session_id,
                filename=report.get("filename", "gc.log"),
                size=report.get("size", 0),
                file_id=report.get("file_id", ""),
                stats=json.dumps(report.get("stats", {}), ensure_ascii=False),
                ai_conclusion=report.get("ai_conclusion", ""),
                created_at=report.get("created_at", now),
            )
            self.db.add(r)
            self.db.commit()
            return rid

        finally:
            self.close()
    def list_gc_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(GCReportModel)
                .filter(GCReportModel.session_id == session_id)
                .order_by(GCReportModel.created_at.desc())
                .all()
            )
            out = []
            for r in rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "id": r.id,
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "collector": stats.get("collector"),
                    "events_total": stats.get("events_total"),
                    "total_pause_ms": stats.get("total_pause_ms"),
                    "stats": stats,
                    "has_ai": bool(r.ai_conclusion),
                })
            return out

        finally:
            self.close()
    def get_gc_report(self, session_id: str, report_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = (
                self.db.query(GCReportModel)
                .filter(
                    GCReportModel.session_id == session_id,
                    GCReportModel.id == report_id,
                )
                .first()
            )
            if not r:
                return None
            return {
                "id": r.id,
                "filename": r.filename,
                "size": r.size,
                "file_id": r.file_id,
                "stats": json.loads(r.stats) if r.stats else {},
                "ai_conclusion": r.ai_conclusion or "",
                "created_at": r.created_at or "",
            }

        finally:
            self.close()
    def list_all_reports(self) -> List[Dict[str, Any]]:
        try:
            out = []
            gc_query = (
                self.db.query(GCReportModel, SessionModel)
                .join(SessionModel, GCReportModel.session_id == SessionModel.id)
                .filter(SessionModel.user_id == self._user_id)
            )
            gc_rows = gc_query.all()
            for r, s in gc_rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "type": "gc",
                    "id": r.id,
                    "session_id": r.session_id,
                    "session_title": s.title or "",
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "has_ai": bool(r.ai_conclusion),
                    "stats": stats,
                    "summary": {
                        "collector": stats.get("collector"),
                        "events_total": stats.get("events_total"),
                        "total_pause_ms": stats.get("total_pause_ms"),
                    },
                })
            jstack_query = (
                self.db.query(JStackReportModel, SessionModel)
                .join(SessionModel, JStackReportModel.session_id == SessionModel.id)
                .filter(SessionModel.user_id == self._user_id)
            )
            jstack_rows = jstack_query.all()
            for r, s in jstack_rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "type": "jstack",
                    "id": r.id,
                    "session_id": r.session_id,
                    "session_title": s.title or "",
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "has_ai": bool(r.ai_conclusion),
                    "summary": {
                        "total_threads": stats.get("total_threads"),
                        "blocked_count": stats.get("by_state", {}).get("BLOCKED", 0),
                        "deadlock_count": stats.get("deadlock_count", 0),
                    },
                })
            heapdump_query = (
                self.db.query(HeapdumpReportModel, SessionModel)
                .join(SessionModel, HeapdumpReportModel.session_id == SessionModel.id)
                .filter(SessionModel.user_id == self._user_id)
            )
            heapdump_rows = heapdump_query.all()
            for r, s in heapdump_rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "type": "heapdump",
                    "id": r.id,
                    "session_id": r.session_id,
                    "session_title": s.title or "",
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "has_ai": bool(r.ai_conclusion),
                    "status": r.status,
                    "progress": r.progress,
                    "summary": {
                        "num_objects": stats.get("numObjects"),
                        "num_classes": stats.get("numClasses"),
                        "used_heap_size": stats.get("usedHeapSize"),
                        "size_bytes": r.size,
                    },
                })
            out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            return out

        finally:
            self.close()
    def update_gc_report(self, session_id: str, report_id: str, **fields) -> bool:
        try:
            r = (
                self.db.query(GCReportModel)
                .filter(
                    GCReportModel.session_id == session_id,
                    GCReportModel.id == report_id,
                )
                .first()
            )
            if not r:
                return False
            if "ai_conclusion" in fields:
                r.ai_conclusion = fields["ai_conclusion"]
            if "stats" in fields:
                r.stats = json.dumps(fields["stats"], ensure_ascii=False) if isinstance(fields["stats"], dict) else str(fields["stats"])
            if "event_analyses" in fields:
                stats = json.loads(r.stats or "{}")
                existing = stats.get("event_analyses", {})
                existing.update(fields["event_analyses"])
                stats["event_analyses"] = existing
                r.stats = json.dumps(stats, ensure_ascii=False)
            self.db.commit()
            return True

        finally:
            self.close()
    def delete_gc_report(self, session_id: str, report_id: str) -> bool:
        try:
            n = (
                self.db.query(GCReportModel)
                .filter(
                    GCReportModel.session_id == session_id,
                    GCReportModel.id == report_id,
                )
                .delete()
            )
            self.db.commit()
            return n > 0

        finally:
            self.close()
    # ---------- jstack 报告 ----------
    def add_jstack_report(self, session_id: str, report: Dict[str, Any]) -> str:
        try:
            rid = _new_id("", 10)
            now = _now_iso()
            r = JStackReportModel(
                id=rid,
                session_id=session_id,
                filename=report.get("filename", "jstack.txt"),
                size=report.get("size", 0),
                file_id=report.get("file_id", ""),
                stats=json.dumps(report.get("stats", {}), ensure_ascii=False),
                ai_conclusion=report.get("ai_conclusion", ""),
                created_at=report.get("created_at", now),
            )
            self.db.add(r)
            self.db.commit()
            return rid

        finally:
            self.close()
    def list_jstack_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(JStackReportModel)
                .filter(JStackReportModel.session_id == session_id)
                .order_by(JStackReportModel.created_at.desc())
                .all()
            )
            out = []
            for r in rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "id": r.id,
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "total_threads": stats.get("total_threads"),
                    "blocked_count": stats.get("by_state", {}).get("BLOCKED", 0),
                    "deadlock_count": stats.get("deadlock_count", 0),
                    "has_ai": bool(r.ai_conclusion),
                })
            return out

        finally:
            self.close()
    def get_jstack_report(self, session_id: str, report_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = (
                self.db.query(JStackReportModel)
                .filter(
                    JStackReportModel.session_id == session_id,
                    JStackReportModel.id == report_id,
                )
                .first()
            )
            if not r:
                return None
            return {
                "id": r.id,
                "filename": r.filename,
                "size": r.size,
                "file_id": r.file_id,
                "stats": json.loads(r.stats) if r.stats else {},
                "ai_conclusion": r.ai_conclusion or "",
                "created_at": r.created_at or "",
            }

        finally:
            self.close()
    def update_jstack_report(self, session_id: str, report_id: str, **fields) -> bool:
        try:
            r = (
                self.db.query(JStackReportModel)
                .filter(
                    JStackReportModel.session_id == session_id,
                    JStackReportModel.id == report_id,
                )
                .first()
            )
            if not r:
                return False
            if "ai_conclusion" in fields:
                r.ai_conclusion = fields["ai_conclusion"]
            if "stats" in fields:
                r.stats = json.dumps(fields["stats"], ensure_ascii=False) if isinstance(fields["stats"], dict) else str(fields["stats"])
            if "thread_analyses" in fields:
                stats = json.loads(r.stats or "{}")
                existing = stats.get("thread_analyses", {})
                existing.update(fields["thread_analyses"])
                stats["thread_analyses"] = existing
                r.stats = json.dumps(stats, ensure_ascii=False)
            self.db.commit()
            return True

        finally:
            self.close()
    def delete_jstack_report(self, session_id: str, report_id: str) -> bool:
        try:
            n = (
                self.db.query(JStackReportModel)
                .filter(
                    JStackReportModel.session_id == session_id,
                    JStackReportModel.id == report_id,
                )
                .delete()
            )
            self.db.commit()
            return n > 0

        finally:
            self.close()

    # ---------- Heapdump 报告 ----------
    def _load_heapdump_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(HeapdumpReportModel)
                .filter(HeapdumpReportModel.session_id == session_id)
                .order_by(HeapdumpReportModel.created_at)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "size": r.size,
                    "status": r.status,
                    "progress": r.progress,
                    "phase": r.phase,
                    "stats": json.loads(r.stats) if r.stats else {},
                    "ai_conclusion": r.ai_conclusion or "",
                    "created_at": r.created_at or "",
                }
                for r in rows
            ]
        finally:
            self.close()

    def add_heapdump_report(self, session_id: str, report: Dict[str, Any]) -> str:
        """创建 QUEUED 状态的 heapdump 报告。report 必须包含 filename/dump_dir。

        可选传入 report["id"] 指定 rid（前缀 hd_）；缺省时自动生成。
        """
        try:
            rid = report.get("id") or _new_id("hd_", 12)
            now = _now_iso()
            r = HeapdumpReportModel(
                id=rid,
                session_id=session_id,
                user_id=self._user_id,
                filename=report.get("filename", "heapdump.hprof"),
                size=report.get("size", 0),
                status=report.get("status", "QUEUED"),
                progress=0,
                phase="",
                dump_dir=report.get("dump_dir", ""),
                parse_args=json.dumps(report.get("parse_args", {}), ensure_ascii=False),
                stats="{}",
                ai_conclusion="",
                error="",
                created_at=now,
                queued_at=now,
            )
            self.db.add(r)
            self.db.commit()
            return rid
        finally:
            self.close()

    def list_heapdump_reports(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.db.query(HeapdumpReportModel)
                .filter(HeapdumpReportModel.session_id == session_id)
                .order_by(HeapdumpReportModel.created_at.desc())
                .all()
            )
            out = []
            for r in rows:
                stats = json.loads(r.stats) if r.stats else {}
                out.append({
                    "id": r.id,
                    "filename": r.filename,
                    "created_at": r.created_at or "",
                    "status": r.status,
                    "progress": r.progress,
                    "phase": r.phase,
                    "size": r.size,
                    "num_objects": stats.get("numObjects"),
                    "num_classes": stats.get("numClasses"),
                    "used_heap": stats.get("usedHeapSize"),
                    "has_ai": bool(r.ai_conclusion),
                })
            return out
        finally:
            self.close()

    def get_heapdump_report(self, session_id: str, report_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = (
                self.db.query(HeapdumpReportModel)
                .filter(
                    HeapdumpReportModel.session_id == session_id,
                    HeapdumpReportModel.id == report_id,
                )
                .first()
            )
            if not r:
                return None
            return self._heapdump_to_dict(r)
        finally:
            self.close()

    def get_heapdump_report_by_id(self, report_id: str) -> Optional[Dict[str, Any]]:
        """按 report_id 直接查询（跨 session）。行级权限：仅返回属于当前 user_id 的报告。

        Worker 独立进程需要在没有 session_id 上下文时读报告；也用于 /api/heapdump-reports/{id} 端点。
        """
        try:
            q = self.db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id)
            if self._user_id:
                q = q.filter(HeapdumpReportModel.user_id == self._user_id)
            r = q.first()
            if not r:
                return None
            return self._heapdump_to_dict(r)
        finally:
            self.close()

    @staticmethod
    def _heapdump_to_dict(r: HeapdumpReportModel) -> Dict[str, Any]:
        return {
            "id": r.id,
            "session_id": r.session_id,
            "user_id": r.user_id,
            "filename": r.filename,
            "size": r.size,
            "status": r.status,
            "progress": r.progress,
            "phase": r.phase,
            "dump_dir": r.dump_dir,
            "parse_args": json.loads(r.parse_args) if r.parse_args else {},
            "stats": json.loads(r.stats) if r.stats else {},
            "ai_conclusion": r.ai_conclusion or "",
            "error": r.error or "",
            "worker_id": r.worker_id or "",
            "heartbeat": r.heartbeat or "",
            "attempts": r.attempts or 0,
            "created_at": r.created_at or "",
            "queued_at": r.queued_at or "",
            "started_at": r.started_at or "",
            "finished_at": r.finished_at or "",
        }

    _HEAPDUMP_UPDATABLE = {
        "status", "progress", "phase", "ai_conclusion", "error",
        "worker_id", "heartbeat", "started_at", "finished_at", "attempts",
    }

    def update_heapdump_report(self, session_id: Optional[str], report_id: str, **fields) -> bool:
        """更新 heapdump 报告字段。session_id 传空则跨 session 更新（Worker 用）。

        允许字段：status/progress/phase/ai_conclusion/error/worker_id/heartbeat/
                  started_at/finished_at/attempts/stats/parse_args
        """
        try:
            q = self.db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id)
            if session_id:
                q = q.filter(HeapdumpReportModel.session_id == session_id)
            r = q.first()
            if not r:
                return False
            for k, v in fields.items():
                if k in self._HEAPDUMP_UPDATABLE:
                    setattr(r, k, v)
                elif k == "stats":
                    r.stats = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
                elif k == "parse_args":
                    r.parse_args = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
            self.db.commit()
            return True
        finally:
            self.close()

    def delete_heapdump_report(self, session_id: Optional[str], report_id: str) -> Optional[str]:
        """删除 heapdump 报告记录。返回 dump_dir（供调用方清理 NFS 目录）；未找到返回 None。"""
        try:
            q = self.db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id)
            if session_id:
                q = q.filter(HeapdumpReportModel.session_id == session_id)
            r = q.first()
            if not r:
                return None
            dump_dir = r.dump_dir or ""
            self.db.delete(r)
            self.db.commit()
            return dump_dir
        finally:
            self.close()