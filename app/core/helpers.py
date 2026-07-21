"""Helper utilities — Community Edition

精简版：去掉所有套餐、配额、组织、邮件、CSRF、登录限流相关函数。
所有需要「当前用户」的函数直接返回固定本地用户。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import state
from react_agent.user_manager_db import LOCAL_USER_ID

_logger = logging.getLogger(__name__)


def _ensure_user_manager():
    if state._USER_MANAGER is None:
        state._USER_MANAGER = state.UserMgrImpl()
        if state._USE_DATABASE and hasattr(state._USER_MANAGER, "db"):
            _load_uploaded_from_db()
        _cleanup_expired_uploads()
    return state._USER_MANAGER


def _is_demo_user_id(user_id: str) -> bool:
    return False


def _now_str() -> str:
    from react_agent.timeutil import now_str
    return now_str()


def _future_str(seconds: int) -> str:
    from react_agent.timeutil import future_str
    return future_str(seconds)


def _parse_time_str(value: str) -> float:
    from react_agent.timeutil import parse_to_epoch
    return parse_to_epoch(value)


def _get_system_setting(key: str, default: str = "", db: Session = None) -> str:
    if not state._USE_DATABASE:
        return os.getenv(key.upper(), default)
    try:
        from react_agent.db import SessionLocal
        from react_agent.models import SystemSettingModel
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            row = db.query(SystemSettingModel).filter(SystemSettingModel.key == key).first()
            return row.value if row else default
        finally:
            if own_db:
                try:
                    db.rollback()
                except Exception:
                    pass
                db.close()
    except Exception:
        return default


def _set_system_setting(key: str, value: str, db: Session = None) -> None:
    if not state._USE_DATABASE:
        return
    from react_agent.db import SessionLocal
    from react_agent.models import SystemSettingModel
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        row = db.query(SystemSettingModel).filter(SystemSettingModel.key == key).first()
        if row:
            row.value = value
            row.updated_at = _now_str()
        else:
            db.add(SystemSettingModel(key=key, value=value, updated_at=_now_str()))
        db.commit()
    finally:
        if own_db:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def is_email_verification_required() -> bool:
    return False


# ---- 单用户版：所有「当前用户」调用都直接返回本地用户 ----

def _get_client_ip(request) -> str:
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip.split(",")[0].split(":")[0].strip()
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].split(":")[0].strip()
    xri = (request.headers.get("X-Real-IP") or "").strip()
    if xri:
        return xri.split(":")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_current_user(request) -> str:
    return LOCAL_USER_ID


def _require_admin(request) -> str:
    return LOCAL_USER_ID


def _check_csrf(request) -> None:
    return None


def _check_login_rate(request, username: str) -> None:
    return None


def _check_session_owner(sid: str, user_id: str) -> None:
    if user_id != LOCAL_USER_ID:
        raise HTTPException(403, "无权访问该会话 / No permission to access this session")
    um = _ensure_user_manager()
    um.check_session_owner(sid, user_id)


def _check_analysis_feature(user_id: str, feature: str) -> None:
    return None


def _check_org_member(org_id: str, user_id: str) -> None:
    return None


def _get_org_owner_config(user_id: str):
    return None


def _get_user_plan(user_id: str, db: Session = None) -> dict:
    return {
        "slug": "community",
        "features": {"self_config": True, "analysis_features": ["gc", "jstack", "heapdump"]},
        "file_size_limit_mb": 500,
        "llm_calls_limit": -1,
    }


def _can_make_llm_call(user_id: str, lang: str = "") -> tuple:
    if state._LOAD_TEST:
        return True, ""
    return True, ""


def _increment_metered_llm_call(user_id: str) -> None:
    return None


def _try_consume_llm_call(user_id: str, lang: str = "") -> tuple:
    return True, ""


def _check_llm_ready(user_id: str, lang: str = "") -> Optional[str]:
    cfg_src, err = _get_llm_config_source(user_id)
    if err:
        return err
    if not cfg_src.get("openai_api_key", ""):
        from react_agent.i18n import _b
        return _b("请先在 ⚙️ 配置中填写你的 API Key", "Please configure your API Key in ⚙️ Settings first", lang)
    return None


def _check_chat_rate(user_id: str, lang: str = "") -> None:
    return None


# ---- 会话锁 ----

def _get_session_lock(session_id: str) -> threading.Lock:
    with state._SESSION_LOCKS_GUARD:
        entry = state._SESSION_LOCKS.get(session_id)
        if entry is None:
            if len(state._SESSION_LOCKS) >= state._SESSION_LOCKS_MAX:
                _reclaim_idle_session_locks(exclude=session_id)
            entry = [threading.Lock(), 0]
            state._SESSION_LOCKS[session_id] = entry
        entry[1] += 1
        return entry[0]


def _release_session_lock(session_id: str) -> None:
    with state._SESSION_LOCKS_GUARD:
        entry = state._SESSION_LOCKS.get(session_id)
        if entry is not None and entry[1] > 0:
            entry[1] -= 1


def _reclaim_idle_session_locks(exclude: str = "") -> int:
    reclaimed = 0
    for sid in list(state._SESSION_LOCKS.keys()):
        if sid == exclude:
            continue
        entry = state._SESSION_LOCKS[sid]
        if entry[1] <= 0:
            del state._SESSION_LOCKS[sid]
            reclaimed += 1
    return reclaimed


# ---- Agent 工厂 ----

def get_builtin_config() -> dict:
    raw_key = _get_system_setting("FREE_TIER_API_KEY", "")
    if raw_key and raw_key.startswith("enc:v1:"):
        from react_agent.config import decrypt_secret
        try:
            api_key = decrypt_secret(raw_key)
        except Exception:
            api_key = ""
    elif raw_key:
        api_key = raw_key
    else:
        api_key = os.getenv("FREE_TIER_API_KEY", "")
    base_url = _get_system_setting("FREE_TIER_BASE_URL", "") or os.getenv("FREE_TIER_BASE_URL", "")
    model = _get_system_setting("FREE_TIER_MODEL", "") or os.getenv("FREE_TIER_MODEL", "")
    return {"openai_api_key": api_key, "openai_base_url": base_url, "openai_model": model}


def _get_agent(user_id: str):
    if user_id not in state._AGENTS:
        with state._AGENTS_LOCK:
            if user_id in state._AGENTS:
                return state._AGENTS[user_id]
            um = _ensure_user_manager()
            user = um.get_user(user_id)
            if not user:
                raise HTTPException(404, "用户不存在 / User not found")
            cfg = dict(user.config or {})
            session_dir = os.getenv("SESSION_DIR", "./sessions")
            use_builtin = bool(cfg.get("use_built_in", True)) if cfg else True
            if use_builtin:
                builtin = get_builtin_config()
                if builtin["openai_api_key"]:
                    cfg["openai_api_key"] = builtin["openai_api_key"]
                if builtin["openai_base_url"]:
                    cfg["openai_base_url"] = builtin["openai_base_url"]
                if builtin["openai_model"]:
                    cfg["openai_model"] = builtin["openai_model"]

            from react_agent.config import validate_openai_base_url
            api_key = cfg.get("openai_api_key", "") or ""
            base_url = validate_openai_base_url(cfg.get("openai_base_url", "") or "https://api.deepseek.com/v1")
            model = cfg.get("openai_model", "") or "deepseek-chat"

            memory = state.MemoryImpl(user_id=user_id, session_dir=f"{session_dir}/{user_id}")
            common_kwargs = dict(
                api_key=api_key,
                base_url=base_url,
                model=model,
                temperature=float(cfg.get("temperature", 0.3)),
                system_prompt_template=_get_system_setting("prompt_react_agent", ""),
                system_prompt_extra=cfg.get("system_prompt_extra", "") or "",
                memory=memory,
                max_iterations=int(cfg.get("max_iterations", 10)),
            )

            from react_agent.graph.facade import LangGraphAgent
            agent = LangGraphAgent(**common_kwargs)
            sm = state.SkillMgrImpl(user_id)
            skills = sm.list()
            agent.load_skills(skills)
            state._AGENTS[user_id] = agent
    return state._AGENTS[user_id]


def _get_llm_config_source(user_id: str):
    um = _ensure_user_manager()
    user = um.get_user(user_id)
    if not user:
        return None, "用户不存在 / User not found"
    cfg_src = user.config or {}
    if isinstance(cfg_src, dict) and cfg_src.get("use_built_in"):
        return cfg_src, ""
    return cfg_src, ""


def _uses_builtin_model(user_id: str) -> bool:
    cfg_src, _ = _get_llm_config_source(user_id)
    if isinstance(cfg_src, dict):
        return bool(cfg_src.get("use_built_in", True))
    return True


_DEMO_SCOPE_PROMPT = ""


# ---- 上传文件辅助 ----

import logging as _log
_logger = _log.getLogger(__name__)


def _delete_uploaded_file_record(file_id: str, session_id: str = "") -> None:
    if not state._USE_DATABASE or not file_id:
        return
    try:
        from react_agent.db import SessionLocal
        from react_agent.models import UploadedFileModel
        from react_agent.upload_storage import delete_uploaded_text
        db = SessionLocal()
        try:
            row = db.query(UploadedFileModel).filter(UploadedFileModel.file_id == file_id).first()
            if row:
                delete_uploaded_text(row.storage_backend or "db", row.storage_key or "")
                db.delete(row)
                db.commit()
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    except Exception:
        _logger.exception("Failed to delete uploaded file record: %s", file_id)


async def _read_upload_bounded(file, max_bytes: int) -> bytes:
    """P1 (2026-07-09 code review): chunked streaming read with hard size cap.

    Fast-fail: Content-Length header check, then 1 MiB streaming with running total.
    """
    from fastapi import HTTPException
    cl_header = None
    if hasattr(file, "headers") and file.headers:
        cl_header = file.headers.get("content-length")
    if cl_header:
        try:
            cl = int(cl_header)
            if cl > max_bytes:
                size_mb = max_bytes / 1024 / 1024
                raise HTTPException(
                    413,
                    f"上传文件过大（{cl} bytes），当前套餐文件最大 {size_mb:.0f}MB / "
                    f"Uploaded file too large ({cl} bytes), current plan file size limit {size_mb:.0f}MB",
                )
        except ValueError:
            pass
    chunks = bytearray()
    read_total = 0
    chunk_size = 1 * 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        read_total += len(chunk)
        if read_total > max_bytes:
            size_mb = max_bytes / 1024 / 1024
            raise HTTPException(
                413,
                f"上传文件过大（>{size_mb:.0f}MB），当前套餐文件最大 {size_mb:.0f}MB / "
                f"Uploaded file too large (>{size_mb:.0f}MB), current plan file size limit {size_mb:.0f}MB",
            )
        chunks.extend(chunk)
    return bytes(chunks)


def _get_upload_retention_days(user_id: str) -> int:
    return 30


def _cleanup_expired_uploads() -> None:
    if not state._USE_DATABASE:
        return
    try:
        from react_agent.db import SessionLocal
        from react_agent.models import UploadedFileModel
        from react_agent.upload_storage import delete_uploaded_text
        db = SessionLocal()
        try:
            now = _now_str()
            rows = db.query(UploadedFileModel).filter(
                UploadedFileModel.expires_at != "",
                UploadedFileModel.expires_at <= now,
            ).all()
            for r in rows:
                delete_uploaded_text(r.storage_backend or "db", r.storage_key or "")
                db.delete(r)
            db.commit()
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    except Exception:
        _logger.exception("Failed to cleanup expired uploads")


def _load_uploaded_from_db():
    return


# ---- 取消/中断标志 ----

def set_cancel_flag(session_id: str) -> None:
    pass


def is_cancelled(session_id: str) -> bool:
    return False


def clear_cancel_flag(session_id: str) -> None:
    pass


# ---- 暴露给其他模块 ----
__all__ = [
    "_ensure_user_manager", "_is_demo_user_id", "_now_str", "_future_str",
    "_parse_time_str", "_get_system_setting", "_set_system_setting", "_truthy",
    "is_email_verification_required",
    "_get_client_ip", "_get_current_user", "_require_admin", "_check_csrf",
    "_check_login_rate", "_check_session_owner", "_check_analysis_feature",
    "_check_org_member", "_get_org_owner_config",
    "_get_user_plan", "_can_make_llm_call", "_increment_metered_llm_call",
    "_try_consume_llm_call", "_check_llm_ready", "_check_chat_rate",
    "_get_session_lock", "_release_session_lock",
    "get_builtin_config", "_get_agent", "_get_llm_config_source",
    "_uses_builtin_model", "_DEMO_SCOPE_PROMPT",
    "_delete_uploaded_file_record", "_read_upload_bounded",
    "_get_upload_retention_days", "_cleanup_expired_uploads", "_load_uploaded_from_db",
    "set_cancel_flag", "is_cancelled", "clear_cancel_flag",
]