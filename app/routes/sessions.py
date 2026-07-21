from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.core import helpers
from app.schemas import CreateSessionReq, FactReq, RenameReq
from app.services.audit import log_audit

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
_logger = logging.getLogger(__name__)


@router.get("")
def list_sessions(request: Request):
    user_id = helpers._get_current_user(request)
    agent = helpers._get_agent(user_id)
    return {"sessions": agent.memory.list_sessions()}


@router.post("")
def create_session(request: Request, req: CreateSessionReq):
    user_id = helpers._get_current_user(request)
    title = req.title or ""
    if len(title) > 200:
        raise HTTPException(400, "会话标题过长，最大 200 字符 / Session title too long, max 200 characters")
    agent = helpers._get_agent(user_id)
    sid = agent.memory.create_session(title)
    log_audit(request, "session.create", user_id=user_id, resource=f"session:{sid}", details={"title": title})
    return {"id": sid}


@router.get("/{sid}")
def get_session(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    return agent.memory.load(sid)


@router.get("/{sid}/meta")
def get_session_meta(request: Request, sid: str):
    """轻量会话元信息。"""
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    from app.core import state
    if state._USE_DATABASE:
        from react_agent.db import SessionLocal as SL
        from react_agent.models import MessageModel as MM, SessionModel as SM
        db = SL()
        try:
            s = db.query(SM).filter(SM.id == sid).first()
            if not s:
                raise HTTPException(404, "会话不存在 / Session not found")
            msg_count = db.query(MM).filter(MM.session_id == sid).count()
            return {"id": sid, "updated_at": s.updated_at or "", "msg_count": msg_count}
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    data = helpers._get_agent(user_id).memory.load(sid)
    return {"id": sid, "updated_at": data.get("updated_at", ""), "msg_count": len(data.get("messages", []))}


@router.delete("/{sid}")
def delete_session(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    agent = helpers._get_agent(user_id)
    helpers._check_session_owner(sid, user_id)
    ok = agent.memory.delete_session(sid)
    if ok:
        log_audit(request, "session.delete", user_id=user_id, resource=f"session:{sid}")
    return {"deleted": ok}


@router.patch("/{sid}")
def rename_session(request: Request, sid: str, req: RenameReq):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    title = req.title or ""
    if len(title) > 200:
        raise HTTPException(400, "会话标题过长，最大 200 字符 / Session title too long, max 200 characters")
    agent = helpers._get_agent(user_id)
    agent.memory.rename_session(sid, title)
    log_audit(request, "session.rename", user_id=user_id, resource=f"session:{sid}", details={"title": title})
    return {"ok": True}


@router.post("/{sid}/clear")
def clear_session(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    agent.memory.clear_messages(sid)
    log_audit(request, "session.clear", user_id=user_id, resource=f"session:{sid}")
    return {"ok": True}


@router.get("/{sid}/facts")
def list_facts(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    return {"facts": agent.memory.get_facts(sid)}


@router.post("/{sid}/facts")
def add_fact(request: Request, sid: str, req: FactReq):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    fact = req.fact or ""
    if len(fact) > 1000:
        raise HTTPException(400, "记忆内容过长，最大 1000 字符 / Fact too long, max 1000 characters")
    if len(fact) == 0:
        raise HTTPException(400, "记忆内容不能为空 / Fact cannot be empty")
    agent = helpers._get_agent(user_id)
    agent.memory.add_fact(sid, fact)
    log_audit(request, "fact.add", user_id=user_id, resource=f"session:{sid}", details={"length": len(fact)})
    return {"facts": agent.memory.get_facts(sid)}


@router.delete("/{sid}/facts/{index}")
def remove_fact(request: Request, sid: str, index: int):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    ok = agent.memory.remove_fact(sid, index)
    if ok:
        log_audit(request, "fact.delete", user_id=user_id, resource=f"session:{sid}", details={"index": index})
    return {"deleted": ok, "facts": agent.memory.get_facts(sid)}