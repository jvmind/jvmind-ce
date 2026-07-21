from __future__ import annotations

from fastapi import APIRouter

from app.core import helpers, state
from react_agent.db import get_pool_stats

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    um = helpers._ensure_user_manager()
    return {
        "ok": True,
        "users_count": len(um.list_users()),
        "agents_count": len(state._AGENTS),
        "db_pool": get_pool_stats(),
    }
