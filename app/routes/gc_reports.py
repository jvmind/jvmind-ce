from __future__ import annotations

import io
import json
import logging
import uuid as _uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.core import helpers, state
from app.services.audit import log_audit
from react_agent.gc_analyzer import analyze as gc_analyze
from react_agent.upload_storage import save_uploaded_text

router = APIRouter(tags=["gc-reports"])
_logger = logging.getLogger(__name__)


def _stats_for_api(stats: dict) -> dict:
    """Strip the heavy per-event list before sending stats over the wire.

    ``stats["events"]`` holds every parsed GC event with its full raw log body,
    which can be tens of MB for large uploads (especially ZGC). Frontend renderers
    never consume it, and the LLM-side ``query_gc_events`` tool reads directly
    from the DB row, so we can safely omit it from HTTP responses. ``events_total``
    already gives the count for any UI that needs to display "N events parsed".
    """
    if not isinstance(stats, dict) or "events" not in stats:
        return stats
    slim = dict(stats)
    slim.pop("events", None)
    return slim


@router.get("/api/me/reports")
def list_my_reports(request: Request):
    user_id = helpers._get_current_user(request)
    agent = helpers._get_agent(user_id)
    if hasattr(agent.memory, "list_all_reports"):
        return {"reports": agent.memory.list_all_reports()}
    return {"reports": []}


@router.post("/api/sessions/{sid}/gc/upload")
async def upload_gc_log(request: Request, sid: str, file: UploadFile = File(...)):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
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

    # P1 (2026-07-09 code review): 在做 size check 之前先按 chunk 流式读，
    # 用 max_bytes 在读的过程中截断；这样攻击者无法用 Content-Length 缺失
    # 的多 GB 上传绕过 plan 上限并把 worker 内存撑爆。
    plan_info = helpers._get_user_plan(user_id)
    size_mb = plan_info.get("file_size_limit_mb", 50)
    max_size = size_mb * 1024 * 1024

    raw = await helpers._read_upload_bounded(file, max_size)

    if ext not in state._ALLOWED_GC_EXTS:
        raise HTTPException(400, f"不支持的文件类型 \"{ext}\"，仅允许 {', '.join(sorted(state._ALLOWED_GC_EXTS))} 格式的 GC 日志文件 / Unsupported file type \"{ext}\", only {', '.join(sorted(state._ALLOWED_GC_EXTS))} allowed")

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
        stats = gc_analyze(text)
    except Exception as e:
        raise HTTPException(400, f"GC 日志解析失败: {e} / GC log parse failed: {e}")

    if stats["events_total"] == 0:
        raise HTTPException(422, "未能解析出任何 GC 事件。请确认日志格式正确。\nJDK9+ 格式示例：[12.345s][info][gc] ...\nJDK8 格式示例：12.345: [GC (Allocation Failure) ... / No GC events parsed. Check log format.\nJDK9+ example: [12.345s][info][gc] ...\nJDK8 example: 12.345: [GC (Allocation Failure) ...")

    file_id = _uuid.uuid4().hex[:10]

    retention = helpers._get_upload_retention_days(user_id)
    expires_at = helpers._future_str(retention * 86400) if retention > 0 else ""

    report = {
        "filename": file.filename or "gc.log",
        "size": len(raw),
        "file_id": file_id,
        "stats": stats,
        "ai_conclusion": "",
    }
    rid = agent.memory.add_gc_report(sid, report)

    # DB 模式：仅持久化上传文件元数据，原文写入本地 gzip 加密存储
    if state._USE_DATABASE:
        try:
            from react_agent.models import UploadedFileModel
            meta = save_uploaded_text(user_id, file_id, "gc", text)
            db = um.db
            db.add(UploadedFileModel(
                file_id=file_id, user_id=user_id, content_type="gc", content="",
                storage_backend=meta["storage_backend"], storage_key=meta["storage_key"],
                size=meta["size"], sha256=meta["sha256"], expires_at=expires_at,
            ))
            db.commit()
        except Exception:
            _logger.warning("failed to persist uploaded GC file metadata for file_id=%s", file_id, exc_info=True)

    log_audit(request, "report.gc.upload", user_id=user_id, resource=f"gc_report:{rid}", details={"session_id": sid, "filename": report["filename"], "size": len(raw), "file_id": file_id})

    return {
        "report_id": rid,
        "file_id": file_id,
        "filename": report["filename"],
        "stats": _stats_for_api(stats),
        "expires_at": expires_at,
    }


@router.get("/api/sessions/{sid}/gc/reports")
def list_gc_reports(request: Request, sid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    return {"reports": agent.memory.list_gc_reports(sid)}


@router.get("/api/sessions/{sid}/gc/reports/{rid}")
def get_gc_report(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    r = agent.memory.get_gc_report(sid, rid)
    if not r:
        raise HTTPException(404, "report not found")
    if isinstance(r, dict) and isinstance(r.get("stats"), dict):
        r = dict(r)
        r["stats"] = _stats_for_api(r["stats"])
    return r


@router.get("/api/sessions/{sid}/gc/reports/{rid}/export")
def export_gc_report(request: Request, sid: str, rid: str, fmt: str = "json"):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    r = agent.memory.get_gc_report(sid, rid)
    if not r:
        raise HTTPException(404, "report not found")
    stats = r.get("stats", {})
    fname = (r.get("filename") or "gc_report").rsplit(".", 1)[0]

    if fmt == "csv":
        buf = io.StringIO()
        buf.write("Category,Count,TotalPauseMs,AvgMs,MaxMs,P95,P99,AvgFreedMb\n")
        for cat, s in stats.get("by_category", {}).items():
            buf.write(f"{cat},{s['count']},{s['total_pause_ms']},{s['avg_pause_ms']},{s['max_pause_ms']},{s['p95_pause_ms']},{s['p99_pause_ms']},{s['avg_freed_mb']}\n")
        buf.write("\nSlowest Events\n")
        buf.write("ID,TimeSec,Category,Cause,DurationMs,BeforeMb,AfterMb\n")
        for e in stats.get("slowest", []):
            buf.write(f"{e['id']},{e['t']},{e['cat']},{e['cause']},{e['dur']},{e['before']},{e['after']}\n")
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})

    # 默认 JSON 导出
    export_r = r
    if isinstance(r, dict) and isinstance(r.get("stats"), dict):
        export_r = dict(r)
        export_r["stats"] = _stats_for_api(r["stats"])
    return JSONResponse(
        content=export_r,
        headers={"Content-Disposition": f'attachment; filename="{fname}.json"'},
    )


@router.delete("/api/sessions/{sid}/gc/reports/{rid}")
def delete_gc_report(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    report = agent.memory.get_gc_report(sid, rid)
    file_id = report.get("file_id", "") if report else ""
    ok = agent.memory.delete_gc_report(sid, rid)
    if ok:
        helpers._delete_uploaded_file_record(file_id)
        log_audit(request, "report.gc.delete", user_id=user_id, resource=f"gc_report:{rid}", details={"session_id": sid, "file_id": file_id})
    return {"deleted": ok}


@router.post("/api/sessions/{sid}/gc/reports/{rid}/save-conclusion")
async def save_gc_conclusion(request: Request, sid: str, rid: str):
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "gc")
    helpers._check_session_owner(sid, user_id)
    agent = helpers._get_agent(user_id)
    from app.schemas import SaveConclusionReq
    import json as _json
    try:
        body = _json.loads((await request.body()).decode("utf-8"))
        req = SaveConclusionReq(**body)
    except Exception:
        raise HTTPException(400, "invalid request body")
    conclusion = (req.conclusion or "").strip()
    if not conclusion:
        raise HTTPException(400, "conclusion is required")
    ok = agent.memory.update_gc_report(sid, rid, ai_conclusion=conclusion)
    if not ok:
        raise HTTPException(404, "report not found")
    return {"saved": True}
