"""Auth 路由 — Community Edition (单用户版，无认证)"""
from __future__ import annotations

from fastapi import APIRouter, Request

from react_agent.user_manager_db import LOCAL_USER_ID, LOCAL_USERNAME, LOCAL_EMAIL

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me():
    """返回当前「已登录」用户。社区版无登录，始终返回固定本地用户。"""
    return {
        "id": LOCAL_USER_ID,
        "username": LOCAL_USERNAME,
        "email": LOCAL_EMAIL,
        "is_admin": True,
    }


@router.post("/logout")
def logout():
    """社区版无登录态，logout 为 no-op。"""
    return {"ok": True}


@router.get("/settings")
def auth_settings():
    """前端用来探测是否需要登录/注册页面。社区版始终无需登录。"""
    return {
        "require_email_verification": False,
        "demo_login_enabled": False,
        "single_user": True,
    }