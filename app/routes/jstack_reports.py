from __future__ import annotations

import json
import logging
import uuid as _uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.core import helpers, state
from app.services.audit import log_audit
from react_agent.jstack_analyzer import (
    compute_stats as jstack_compute_stats,
    parse_jstack,
)
from react_agent.upload_storage import save_uploaded_text

router = APIRouter(tags=["jstack-reports"])
_logger = logging.getLogger(__name__)


@router.post("/api/sessions/{sid}/jstack/upload")
async def upload_jstack(request: Request, sid: str, file: UploadFile = File(...)):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "jstack")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    um = helpers._ensure_user_manager()

    fname = (file.filename or "upload").lower()
    # 防止路径遍历攻击，仅保留文件名部分
    fname = fname.replace("\\", "/").split("/")[-1]
    ext = "." + fname.split(".")[-1] if "." in fname else ""

    # 文件名长度限制
    if len(fname) > 255:
        raise HTTPException(400, "文件名过长，最大 255 字符 / Filename too long, max 255 characters")

    # P1 (2026-07-09 code review): 同 gc_reports — 在 size check 之前按
    # chunk 流式读入，用 max_size 在读的过程中截断。
    plan_info = helpers._get_user_plan(user_id)
    size_mb = plan_info.get("file_size_limit_mb", 50)
    max_size = size_mb * 1024 * 1024

    raw = await helpers._read_upload_bounded(file, max_size)

    if ext not in state._ALLOWED_JSTACK_EXTS:
        raise HTTPException(400, f"不支持的文件类型 \"{ext}\"，仅允许 {', '.join(sorted(state._ALLOWED_JSTACK_EXTS))} 格式的 jstack 文件 / Unsupported file type \"{ext}\", only {', '.join(sorted(state._ALLOWED_JSTACK_EXTS))} allowed")

    # 二次 size 校验：_read_upload_bounded 已用 max_size 截断流，这里再
    # 做一次最终 size 校验以便 audit/track_upload 拿到正确字节数。
    if len(raw) > max_size:
        raise HTTPException(413, f"当前套餐文件最大 {size_mb}MB，当前 {(len(raw)/1024/1024):.1f}MB / Plan file size limit {size_mb}MB, current {(len(raw)/1024/1024):.1f}MB")

    # 上传数量限制（原子化，避免并发超限）
    can, reason = um.try_consume_file_upload(user_id)
    if not can:
        raise HTTPException(429, reason)

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(400, f"读取文件失败: {e} / Failed to read file: {e}")

    helpers._cleanup_expired_uploads()

    try:
        parsed = parse_jstack(text)
        stats = jstack_compute_stats(parsed)
    except Exception as e:
        raise HTTPException(400, f"jstack 解析失败: {e} / jstack parse failed: {e}")

    if stats["total_threads"] == 0:
        raise HTTPException(422, "未能解析出任何线程。请确认文件是标准 jstack 输出格式（包含 \"thread-name\" #tid ...） / No threads parsed. Check file is standard jstack format (contains \"thread-name\" #tid ...)")

    file_id = _uuid.uuid4().hex[:10]

    retention = helpers._get_upload_retention_days(user_id)
    expires_at = helpers._future_str(retention * 86400) if retention > 0 else ""

    report = {
        "filename": file.filename or "thread_dump.txt",
        "size": len(raw),
        "file_id": file_id,
        "stats": stats,
        "ai_conclusion": "",
    }
    rid = agent.memory.add_jstack_report(sid, report)

    # DB 模式：仅持久化上传文件元数据，原文写入本地 gzip 加密存储
    if state._USE_DATABASE:
        try:
            from react_agent.models import UploadedFileModel
            meta = save_uploaded_text(user_id, file_id, "jstack", text)
            db = um.db
            db.add(UploadedFileModel(
                file_id=file_id, user_id=user_id, content_type="jstack", content="",
                storage_backend=meta["storage_backend"], storage_key=meta["storage_key"],
                size=meta["size"], sha256=meta["sha256"], expires_at=expires_at,
            ))
            db.commit()
        except Exception:
            _logger.warning("failed to persist uploaded jstack file metadata for file_id=%s", file_id, exc_info=True)

    log_audit(request, "report.jstack.upload", user_id=user_id, resource=f"jstack_report:{rid}", details={"session_id": sid, "filename": report["filename"], "size": len(raw), "file_id": file_id})

    return {
        "report_id": rid,
        "file_id": file_id,
        "filename": report["filename"],
        "stats": stats,
        "expires_at": expires_at,
    }


@router.get("/api/sessions/{sid}/jstack/reports")
def list_jstack_reports(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "jstack")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    return {"reports": agent.memory.list_jstack_reports(sid)}


@router.get("/api/sessions/{sid}/jstack/reports/{rid}")
def get_jstack_report(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "jstack")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    r = agent.memory.get_jstack_report(sid, rid)
    if not r:
        raise HTTPException(404, "report not found")
    return r


@router.delete("/api/sessions/{sid}/jstack/reports/{rid}")
def delete_jstack_report(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "jstack")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    report = agent.memory.get_jstack_report(sid, rid)
    file_id = report.get("file_id", "") if report else ""
    ok = agent.memory.delete_jstack_report(sid, rid)
    if ok:
        helpers._delete_uploaded_file_record(file_id)
        log_audit(request, "report.jstack.delete", user_id=user_id, resource=f"jstack_report:{rid}", details={"session_id": sid, "file_id": file_id})
    return {"deleted": ok}


@router.post("/api/sessions/{sid}/jstack/reports/{rid}/save-conclusion")
async def save_jstack_conclusion(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "jstack")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    from app.schemas import SaveConclusionReq
    try:
        body = json.loads((await request.body()).decode("utf-8"))
        req = SaveConclusionReq(**body)
    except Exception:
        raise HTTPException(400, "invalid request body")
    conclusion = (req.conclusion or "").strip()
    if not conclusion:
        raise HTTPException(400, "conclusion is required")
    ok = agent.memory.update_jstack_report(sid, rid, ai_conclusion=conclusion)
    if not ok:
        raise HTTPException(404, "report not found")
    return {"saved": True}
