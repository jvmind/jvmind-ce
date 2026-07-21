from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.core import state
from app.core.helpers import _get_client_ip


_SENSITIVE_KEYS = {
    "password",
    "token",
    "jwt",
    "csrf_token",
    "openai_api_key",
    "api_key",
    "authorization",
    "content",
    "messages",
    "prompt",
    "ai_conclusion",
}


def _safe_details(details: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not details:
        return {}
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in _SENSITIVE_KEYS:
            safe[key] = "***"
            continue
        if isinstance(value, str) and len(value) > 500:
            safe[key] = value[:500] + "..."
            continue
        safe[key] = value
    return safe


def log_audit(
    request: Request,
    action: str,
    resource: str = "",
    user_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    db: Session = None,
) -> None:
    """Write an audit log entry without affecting the main business flow."""
    if not state._USE_DATABASE:
        return
    try:
        from react_agent.db import SessionLocal
        from react_agent.models import AuditLogModel

        ip = _get_client_ip(request)
        detail_obj = _safe_details(details)
        user_agent = request.headers.get("user-agent", "") if request else ""
        if user_agent:
            detail_obj.setdefault("user_agent", user_agent[:300])

        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            db.add(AuditLogModel(
                user_id=user_id,
                action=action,
                resource=resource,
                details=json.dumps(detail_obj, ensure_ascii=False),
                ip=ip,
            ))
            db.commit()
        finally:
            if own_db:
                try:
                    db.rollback()
                except Exception:
                    pass
                db.close()
    except Exception:
        pass
