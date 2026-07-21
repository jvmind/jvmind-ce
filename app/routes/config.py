from __future__ import annotations

import logging
import time as _time

from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI

from app.core import helpers, state
from app.schemas import ConfigUpdateReq, ConnTestReq
from app.services.audit import log_audit
from react_agent.config import LLMConfig, validate_openai_base_url

router = APIRouter(prefix="/api/config", tags=["config"])
_logger = logging.getLogger(__name__)


@router.get("")
def get_config(request: Request):
    user_id = helpers._get_current_user(request)
    um = helpers._ensure_user_manager()
    user = um.get_user(user_id)
    if not user:
        raise HTTPException(404, "用户不存在 / User not found")
    source_cfg = user.config or {}
    cfg = LLMConfig.from_dict(source_cfg)
    result = cfg.to_safe_dict()
    result["use_built_in"] = bool(source_cfg.get("use_built_in", True)) if isinstance(source_cfg, dict) else True
    if result["use_built_in"]:
        builtin = helpers.get_builtin_config()
        result["openai_base_url"] = builtin["openai_base_url"]
        result["openai_model"] = builtin["openai_model"] or "内置模型"
        result["openai_api_key"] = ""
        result["openai_api_key_set"] = bool(builtin["openai_api_key"])
        result["note"] = "当前使用内置模型 / Currently using built-in model"
    secret_errors = source_cfg.get("_secret_errors", []) if isinstance(source_cfg, dict) else []
    if "openai_api_key" in secret_errors and not result["use_built_in"]:
        result["openai_api_key_set"] = True
        result["openai_api_key_error"] = "API Key 解密失败，请检查 CONFIG_ENCRYPTION_KEY 或重新填写 / API Key decryption failed, check CONFIG_ENCRYPTION_KEY or re-enter"
    return result


@router.put("")
def put_config(request: Request, req: ConfigUpdateReq):
    user_id = helpers._get_current_user(request)
    um = helpers._ensure_user_manager()
    user = um.get_user(user_id)
    patch = {k: v for k, v in req.model_dump(exclude_none=False).items() if v is not None}
    if patch.get("use_built_in"):
        for k in ("openai_base_url", "openai_api_key", "openai_model"):
            patch.pop(k, None)
    try:
        user = um.update_user_config(user_id, patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # P0-2 fix (2026-07-09): invalidation order bug.
    # Old code: `_get_agent` first → returned the CACHED agent with the OLD config
    # → then closed its memory and re-inserted the same (now broken) object →
    # user agent permanently unusable (every chat request found a closed memory).
    # New code: evict first; next request rebuilds from fresh config. No
    # double-handle, no leak.
    old_agent = state._AGENTS.pop(user_id, None)
    if old_agent is not None and hasattr(old_agent.memory, "close"):
        try:
            old_agent.memory.close()
        except Exception:
            _logger.debug("agent memory close failed during config update", exc_info=True)
    state._AGENTS[user_id] = helpers._get_agent(user_id)  # rebuild with new config
    log_audit(request, "config.update", user_id=user_id, resource=f"user:{user_id}", details={"fields": sorted(patch.keys())})
    cfg = LLMConfig.from_dict(user.config or {})
    return {
        "ok": True,
        "config": cfg.to_safe_dict(),
        "agent_ready": user_id in state._AGENTS,
    }


@router.post("/test")
def test_config(request: Request, req: ConnTestReq):
    user_id = helpers._get_current_user(request)
    um = helpers._ensure_user_manager()
    user = um.get_user(user_id)
    saved_cfg = user.config or {}
    saved = LLMConfig.from_dict(saved_cfg)
    use_built_in = bool(req.use_built_in if req.use_built_in is not None else saved_cfg.get("use_built_in", False))
    if use_built_in:
        builtin = helpers.get_builtin_config()
        api_key = builtin["openai_api_key"]
        base_url = validate_openai_base_url(builtin["openai_base_url"] or "https://api.deepseek.com/v1")
        model = builtin["openai_model"] or "deepseek-chat"
    else:
        api_key = req.openai_api_key
        if not api_key or (isinstance(api_key, str) and "*" in api_key):
            api_key = saved.openai_api_key
        base_url = validate_openai_base_url(req.openai_base_url or saved.openai_base_url)
        model = req.openai_model or saved.openai_model
    if not api_key:
        raise HTTPException(400, "未提供 API Key / No API Key provided")
    t0 = _time.time()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            temperature=0,
        )
        content = (resp.choices[0].message.content or "").strip()
        latency_ms = int((_time.time() - t0) * 1000)
        log_audit(request, "config.test.success", user_id=user_id, resource=f"user:{user_id}", details={"model": model, "latency_ms": latency_ms})
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": model,
            "reply": content[:80],
        }
    except Exception as e:
        latency_ms = int((_time.time() - t0) * 1000)
        log_audit(request, "config.test.failed", user_id=user_id, resource=f"user:{user_id}", details={"model": model, "latency_ms": latency_ms, "error_type": type(e).__name__})
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "error": f"{type(e).__name__}: {e}",
        }
