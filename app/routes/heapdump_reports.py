"""Heapdump 报告管理路由（对齐 IMPLEMENTATION_GUIDE §3.2）。

端点：
- GET    /api/heapdump-reports?session_id=  列表
- GET    /api/heapdump-reports/{id}                   详情
- GET    /api/heapdump-reports/{id}/progress          SSE 进度流
- POST   /api/heapdump-reports/{id}/cancel            置 CANCEL_REQUESTED
- DELETE /api/heapdump-reports/{id}                   删 DB + NFS
- POST   /api/heapdump-reports/{id}/save-conclusion   保存 AI 诊断结论（对齐 gc/jstack）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.core import helpers, state
from app.services.audit import log_audit

router = APIRouter(tags=["heapdump-reports"])
_logger = logging.getLogger(__name__)

# 进度 SSE 轮询间隔（秒）
_SSE_POLL_INTERVAL = float(__import__("os").getenv("HEAPDUMP_PROGRESS_POLL", "2"))
# SSE 最长保持连接（安全上限，防止僵死连接）
_SSE_MAX_DURATION = float(__import__("os").getenv("HEAPDUMP_PROGRESS_MAX_DURATION", "3600"))

_TERMINAL_STATUSES = {"DONE", "FAILED", "CANCELLED"}


def _assert_dump_dir_within_storage(dump_dir: str) -> None:
    """P1 (2026-07-09 code review): dump_dir 防御性路径包含检查。

    Java query-service 接收 ``dumpDir`` 后会打开 hprof + 索引。如果
    DB 损坏、migration bug 或 worker 代码改动导致 ``dump_dir`` 不再
    落在 ``HEAPDUMP_STORAGE_ROOT`` 之下，会变成 SSRF-like 任意文件
    读取。这里在每次读 ``dump_dir`` 时做 resolve + is_relative_to 校验，
    包含失败立刻 500，不把脏数据转发到 Java 服务。
    """
    if not dump_dir:
        raise HTTPException(500, "报告 dump_dir 为空 / report dump_dir is empty")
    # 跟 heapdump_upload 同步读 env var；如果测试 patch 了模块级
    # ``_STORAGE_ROOT``，也兼容那条路径（避免硬编码两种来源的差异）。
    from app.routes.heapdump_upload import _STORAGE_ROOT as _UPLOAD_ROOT
    storage_root = _UPLOAD_ROOT.resolve()
    try:
        resolved = Path(dump_dir).resolve()
    except Exception as e:
        raise HTTPException(500, f"dump_dir 路径无法解析 / dump_dir unresolvable: {e}")
    # 解析后必须是 storage_root 的子路径（含等号自身）
    if not (resolved == storage_root or storage_root in resolved.parents):
        # 记录但不把 storage_root / dump_dir 直接回给客户端，避免泄漏
        _logger.error(
            "heapdump dump_dir containment failed: dump_dir=%s storage_root=%s",
            dump_dir, storage_root,
        )
        raise HTTPException(
            500,
            "报告 dump_dir 路径异常，拒绝访问 / dump_dir outside storage root",
        )


def _load_report(user_id: str, report_id: str) -> Dict[str, Any]:
    """按 report_id 查找并做权限校验；返回 dict，未找到抛 404。"""
    agent = helpers._get_agent(user_id)
    r = agent.memory.get_heapdump_report_by_id(report_id)
    if not r:
        raise HTTPException(404, "heapdump 报告不存在 / heapdump report not found")
    if r.get("user_id") == user_id:
        _assert_dump_dir_within_storage(r.get("dump_dir", ""))
        return r
    raise HTTPException(403, "无权访问该报告 / Not authorized to access this report")


# ---------- 列表 ----------


@router.get("/api/heapdump-reports")
def list_heapdump_reports(
    request: Request,
    session_id: Optional[str] = None,
):
    """列表：
    - 传 session_id  → 返回该会话下的 heapdump 报告
    - 都不传         → 返回当前用户的所有 heapdump 报告（个人）
    """
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    agent = helpers._get_agent(user_id)

    if session_id:
        helpers._check_session_owner(session_id, user_id)
        return {"reports": agent.memory.list_heapdump_reports(session_id)}

    all_reports = agent.memory.list_all_reports()
    return {"reports": [r for r in all_reports if r.get("type") == "heapdump"]}


# ---------- 详情 ----------


@router.get("/api/heapdump-reports/{report_id}")
def get_heapdump_report(request: Request, report_id: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    return _load_report(user_id, report_id)


# ---------- 进度 SSE ----------


@router.get("/api/heapdump-reports/{report_id}/progress")
async def heapdump_progress_stream(request: Request, report_id: str):
    """SSE 进度流。发送 event=progress，data={status,progress,phase,error}。

    终态（DONE/FAILED/CANCELLED）后发送 event=done 并关闭。
    连接超时（默认 1 小时）也会自动关闭。
    """
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    # 首次校验（快速失败）
    _load_report(user_id, report_id)

    from react_agent.db import SessionLocal
    from react_agent.models import HeapdumpReportModel

    async def event_gen():
        loop = asyncio.get_event_loop()
        start = loop.time()
        last_key = None
        while True:
            if await request.is_disconnected():
                return
            if loop.time() - start > _SSE_MAX_DURATION:
                yield {"event": "done", "data": json.dumps({"type": "timeout"})}
                return

            db = SessionLocal()
            try:
                r = db.query(HeapdumpReportModel).filter(
                    HeapdumpReportModel.id == report_id
                ).first()
                if not r:
                    yield {"event": "error", "data": json.dumps({"type": "error", "content": "report not found"})}
                    yield {"event": "done", "data": json.dumps({"type": "done"})}
                    return
                payload = {
                    "type": "progress",
                    "status": r.status,
                    "progress": r.progress or 0,
                    "phase": r.phase or "",
                    "error": r.error or "",
                }
                status = r.status
            finally:
                db.close()

            # 变化才推（含首次），减少无效流量
            key = (payload["status"], payload["progress"], payload["phase"], payload["error"])
            if key != last_key:
                yield {"event": "progress", "data": json.dumps(payload, ensure_ascii=False)}
                last_key = key

            if status in _TERMINAL_STATUSES:
                yield {"event": "done", "data": json.dumps({"type": "done", "status": status})}
                return

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    return EventSourceResponse(event_gen())


# ---------- 取消 ----------


@router.post("/api/heapdump-reports/{report_id}/cancel")
def cancel_heapdump_report(request: Request, report_id: str):
    """置 status=CANCEL_REQUESTED，由 Worker 检测到后 kill 子进程 + 清理半成品。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    r = _load_report(user_id, report_id)
    status = r.get("status")
    if status not in ("QUEUED", "PARSING"):
        raise HTTPException(409, f"当前状态无法取消 (status={status}) / cannot cancel in {status}")

    agent = helpers._get_agent(user_id)
    ok = agent.memory.update_heapdump_report(None, report_id, status="CANCEL_REQUESTED")
    if not ok:
        raise HTTPException(404, "报告不存在 / not found")
    log_audit(request, "report.heapdump.cancel", user_id=user_id,
              resource=f"heapdump_report:{report_id}",
              details={"prev_status": status})
    return {"cancelled": True, "status": "CANCEL_REQUESTED"}


# ---------- 删除 ----------


@router.delete("/api/heapdump-reports/{report_id}")
def delete_heapdump_report(request: Request, report_id: str):
    """删除报告：DB 删行 + NFS dump_dir 清理。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    r = _load_report(user_id, report_id)

    agent = helpers._get_agent(user_id)
    dump_dir = agent.memory.delete_heapdump_report(None, report_id)
    if dump_dir is None:
        raise HTTPException(404, "报告不存在 / not found")

    if dump_dir:
        try:
            p = Path(dump_dir)
            if p.exists() and p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            _logger.warning("failed to remove dump_dir=%s", dump_dir, exc_info=True)

    log_audit(request, "report.heapdump.delete", user_id=user_id,
              resource=f"heapdump_report:{report_id}",
              details={"dump_dir": dump_dir, "filename": r.get("filename")})
    return {"deleted": True, "dump_dir": dump_dir}


# ---------- 保存 AI 结论（对齐 gc/jstack） ----------


@router.post("/api/heapdump-reports/{report_id}/save-conclusion")
async def save_heapdump_conclusion(request: Request, report_id: str):
    """保存 AI 诊断结论（前端触发对话后手动保存到报告）。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    _load_report(user_id, report_id)

    from app.schemas import SaveConclusionReq
    try:
        body = json.loads((await request.body()).decode("utf-8"))
        req = SaveConclusionReq(**body)
    except Exception:
        raise HTTPException(400, "invalid request body")
    conclusion = (req.conclusion or "").strip()
    if not conclusion:
        raise HTTPException(400, "conclusion is required")

    agent = helpers._get_agent(user_id)
    ok = agent.memory.update_heapdump_report(None, report_id, ai_conclusion=conclusion)
    if not ok:
        raise HTTPException(404, "report not found")
    return {"saved": True}
