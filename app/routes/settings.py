"""公共设置接口 — Community Edition"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.core import state

router = APIRouter()


_SETTING_DEFAULTS = {
    "max_input_length": "100000",
}


def _get(key: str) -> str:
    return _SETTING_DEFAULTS.get(key, "")


def _load(key: str) -> str:
    default = _get(key)
    if not state._USE_DATABASE:
        return default
    try:
        from react_agent.db import SessionLocal
        from react_agent.models import SystemSettingModel
        db = SessionLocal()
        try:
            row = db.query(SystemSettingModel).filter(SystemSettingModel.key == key).first()
            if row and row.value:
                return row.value
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    except Exception:
        pass
    return default


def get_public_settings() -> Dict[str, Any]:
    return {k: _load(k) for k in _SETTING_DEFAULTS}


@router.get("/api/settings/public")
def public_settings():
    return get_public_settings()