from __future__ import annotations

from fastapi import APIRouter, Request

from app.core import helpers

router = APIRouter(tags=["plans"])


@router.get("/api/quota")
def get_quota(request: Request):
    """社区版无配额，返回固定「unlimited」信息。"""
    return {
        "llm_calls_limit": -1,
        "llm_calls_used": 0,
        "llm_calls_remaining": -1,
        "llm_calls_unmetered": True,
        "quota_period": "unlimited",
        "can_call": True,
        "cooldown_remaining": "",
        "cooldown_remaining_seconds": 0,
    }


@router.get("/api/plans/public")
def list_public_plans():
    """公开套餐列表（社区版无套餐，返回空列表）。"""
    return {"plans": []}