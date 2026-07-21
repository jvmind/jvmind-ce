"""Query-service 反向代理（IMPLEMENTATION_GUIDE §3.3）。

所有堆分析端点统一走本模块：
- 鉴权 + 报告所有权 + 状态 (DONE) 预检查
- 注入 dumpDir（Python 从 DB 读，前端不传）
- 调用 Java query-service（MAT_QUERY_SERVICE_URL，默认 :8090，无鉴权，仅内网访问）
- 配额：页面内查询不扣 api_call_count（api_call_count 仅用于外部 OpenAI 兼容 API 访问）。
- 异步任务所有权追踪（防跨用户 poll 别人的 task_id）
- 错误归一为双语 + MAT_* code

同步端点返回 JSON；异步提交返回 HTTP 202 + {taskId, status:"RUNNING"}；
异步轮询 /tasks/{task_id} 透传并在终态回收所有权。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core import helpers, state
from app.routes.heapdump_reports import _load_report
from app.services.audit import log_audit

router = APIRouter(tags=["heapdump-proxy"])
_logger = logging.getLogger(__name__)

_BASE = os.getenv("MAT_QUERY_SERVICE_URL", "http://127.0.0.1:8090").rstrip("/")

# 各端点 timeout（秒）；OQL 最慢，top-consumers/path2gc/pool-leaks/threadlocals 次之
_TIMEOUTS: Dict[str, float] = {
    "overview": 30,
    "histogram": 30,
    "dominator": 30,
    "threads_list": 30,
    "threads_frames": 60,
    "threadlocals": 60,
    "object": 30,
    "path2gc": 60,
    "top-consumers": 60,
    "oom-diagnosis": 30,
    "sql-in-threads": 30,
    "connection-pools": 30,
    "pool-leaks": 60,
    "oql": 120,
    "submit": 10,
    "poll": 10,
    "cancel": 10,
}

_AUDIT_ENDPOINTS = {"leak-suspects", "diagnose-oom", "oql", "merge-paths", "retained-set"}

# 异步任务所有权：task_id -> (user_id, report_id)
_TASK_OWNER: Dict[str, tuple] = {}

# ---------------------------------------------------------------------------
# Shared httpx client (connection-pooled; closed in lifespan shutdown)
# ---------------------------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None


def get_mat_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=_BASE,
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
            http2=False,
        )
    return _client


async def close_mat_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


# ---------------------------------------------------------------------------
# Pre-check helpers
# ---------------------------------------------------------------------------

def _prep(request: Request, report_id: str) -> tuple:
    """鉴权 + 所有权 + 状态 (DONE) + dump_dir 存在性。返回 (user_id, report)。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    r = _load_report(user_id, report_id)
    status = r.get("status")
    if status != "DONE":
        if status in ("QUEUED", "PARSING", "CANCEL_REQUESTED"):
            pct = int((r.get("progress") or 0) * 100)
            raise HTTPException(
                409,
                f"解析中(进度 {pct}%)，稍后再试 / Parsing in progress ({pct}%), try again later",
            )
        if status == "FAILED":
            err = r.get("error") or ""
            raise HTTPException(422, f"解析失败: {err} / Parsing failed: {err}")
        if status == "CANCELLED":
            raise HTTPException(410, "报告已取消 / Report was cancelled")
        raise HTTPException(409, f"报告状态不可用: {status} / Report not ready: {status}")
    dump_dir = r.get("dump_dir")
    if not dump_dir:
        raise HTTPException(500, "报告数据缺失 / Report data missing on server")
    return user_id, r


def _check_task_owner(task_id: str, user_id: str, report_id: str) -> None:
    owner = _TASK_OWNER.get(task_id)
    if owner is None:
        return  # not tracked yet (restart) — allow
    if owner != (user_id, report_id):
        raise HTTPException(403, "无权访问该任务 / Not authorized for this task")


def _track_task(task_id: str, user_id: str, report_id: str) -> None:
    _TASK_OWNER[task_id] = (user_id, report_id)


def _untrack_task(task_id: str) -> None:
    _TASK_OWNER.pop(task_id, None)


def _quota_check(user_id: str) -> None:
    """页面内查询不计 api_call_count；保留占位函数以避免大面积改动，no-op。

    api_call_count 专门用于外部系统调用本系统提供的 OpenAI 兼容 API，
    页面内 heapdump/Gc/Jstack 分析属于文件上传后的内置功能，不消耗该配额。
    """
    return None


def _quota_incr(user_id: str) -> None:
    """页面内查询不扣 api_call_count。"""
    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _err(code: int, zh: str, en: str, mat_code: str) -> HTTPException:
    return HTTPException(code, {"error": f"{zh} / {en}", "code": mat_code})


def _map_java_error(resp: httpx.Response, exc: Optional[Exception]) -> HTTPException:
    """把 Java 端的非 200/202 或传输错误映射为 4xx/5xx HTTPException。"""
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return _err(504, "查询服务超时", "Query service timed out", "MAT_TIMEOUT")
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
            return _err(504, "查询服务不可用", "Query service unavailable", "MAT_UNAVAILABLE")
        return _err(502, f"查询服务错误: {exc}", f"Query service error: {exc}", "MAT_QUERY_ERROR")
    try:
        data = resp.json()
        msg = (data.get("error") if isinstance(data, dict) else str(data)) or ""
    except Exception:
        msg = resp.text or ""

    if resp.status_code == 400:
        return _err(400, f"请求参数错误: {msg}", f"Bad request: {msg}", "MAT_BAD_REQUEST")
    if resp.status_code == 404:
        if "task" in msg.lower() or "result" in msg.lower():
            return _err(404, "任务或结果不存在", "Task/result not found", "MAT_TASK_NOT_FOUND")
        return _err(404, f"查询失败: {msg}", f"Not found: {msg}", "MAT_DUMP_NOT_FOUND")
    if resp.status_code == 409:
        return _err(409, "结果集已过期，请重新查询", "Result set expired, please re-run query", "MAT_RESULT_EXPIRED")
    if resp.status_code == 500:
        low = msg.lower()
        if "oom" in low or "outofmemory" in low or "gc overhead" in low:
            return _err(502, "查询服务内存不足，请缩小查询范围", "Query service out of memory, narrow your scope", "MAT_QUERY_OOM")
        return _err(502, f"查询服务内部错误: {msg}", f"Query service error: {msg}", "MAT_QUERY_ERROR")
    return _err(502, f"查询服务错误({resp.status_code}): {msg}",
                f"Query service error ({resp.status_code}): {msg}", "MAT_QUERY_ERROR")


async def _do(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    timeout: float = 30.0,
) -> tuple:
    """发请求到 Java；返回 (status_code, json_data)。抛 HTTPException on transport error."""
    client = get_mat_client()
    req_kwargs: Dict[str, Any] = {"params": params or {}, "timeout": httpx.Timeout(timeout, connect=5.0)}
    if json_body is not None:
        req_kwargs["json"] = json_body
    try:
        resp = await client.request(method, path, **req_kwargs)
    except Exception as e:
        raise _map_java_error(httpx.Response(500), e)
    if resp.status_code not in (200, 202):
        raise _map_java_error(resp, None)
    try:
        data = resp.json()
    except Exception as e:
        raise _err(502, f"查询服务返回格式错误: {e}", f"Invalid JSON from query service: {e}", "MAT_QUERY_ERROR")
    return resp.status_code, data


# ---------------------------------------------------------------------------
# Overview — serve cached stats from DB when available (worker backfilled it)
# ---------------------------------------------------------------------------

@router.get("/api/heapdump-reports/{report_id}/overview")
async def proxy_overview(
    request: Request,
    report_id: str,
    full: bool = Query(False),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)

    if full:
        cached = r.get("stats")
        if cached:
            try:
                data = json.loads(cached) if isinstance(cached, str) else cached
                _quota_incr(user_id)
                return data
            except Exception:
                pass

    params: Dict[str, Any] = {"dumpDir": r["dump_dir"]}
    if full:
        params["full"] = "true"
    status_code, data = await _do("GET", "/overview", params=params, timeout=_TIMEOUTS["overview"])
    _quota_incr(user_id)
    return data


# ---------------------------------------------------------------------------
# Sync GET endpoints (histogram, dominator, threads, threadlocals, object,
# path2gc, top-consumers, oom-diagnosis, sql-in-threads, connection-pools, pool-leaks)
# ---------------------------------------------------------------------------

def _mk_get_ep(path: str, timeout_key: str):
    async def _ep(request: Request, report_id: str, _params: Dict[str, Any]):
        user_id, r = _prep(request, report_id)
        _quota_check(user_id)
        params = {"dumpDir": r["dump_dir"]}
        params.update({k: v for k, v in _params.items() if v is not None})
        _clean_params(params)
        _cap_top(params, max_top=500, default_top=None)
        status_code, data = await _do("GET", path, params=params, timeout=_TIMEOUTS[timeout_key])
        _quota_incr(user_id)
        if path.strip("/") in _AUDIT_ENDPOINTS:
            log_audit(request, f"report.heapdump.query.{path.strip('/')}",
                      user_id=user_id,
                      resource=f"heapdump_report:{report_id}")
        return data
    return _ep


def _clean_params(params: Dict[str, Any]) -> None:
    """去掉 False/None/空串等无意义值，但保留 0。"""
    for k in list(params.keys()):
        v = params[k]
        if v is None or v == "":
            params.pop(k, None)


def _cap_top(params: Dict[str, Any], max_top: int, default_top: Optional[int]) -> None:
    t = params.get("top")
    if t is None:
        if default_top is not None:
            params["top"] = default_top
        return
    try:
        ti = int(t)
    except (TypeError, ValueError):
        return
    if ti > max_top:
        params["top"] = max_top
    if ti <= 0:
        params["top"] = default_top or 50


@router.get("/api/heapdump-reports/{report_id}/histogram")
async def proxy_histogram(
    request: Request, report_id: str,
    top: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort: str = Query("shallow", pattern=r"^(shallow|retained|count)$"),
    approx: bool = Query(True),
    objectSet: Optional[str] = Query(None),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "top": top, "offset": offset, "sort": sort}
    if not approx:
        params["approx"] = "false"
    if objectSet:
        params["objectSet"] = objectSet
    status_code, data = await _do("GET", "/histogram", params=params, timeout=_TIMEOUTS["histogram"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/dominator")
async def proxy_dominator(
    request: Request, report_id: str,
    parent: str = Query("ROOT"),
    top: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "parent": parent, "top": top, "offset": offset}
    status_code, data = await _do("GET", "/dominator", params=params, timeout=_TIMEOUTS["dominator"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/threads")
async def proxy_threads(
    request: Request, report_id: str,
    thread: Optional[str] = Query(None),
    frame: Optional[int] = Query(None, ge=0),
    top: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params: Dict[str, Any] = {"dumpDir": r["dump_dir"], "top": top, "offset": offset}
    if thread:
        params["thread"] = thread
    if frame is not None:
        params["frame"] = frame
    timeout_key = "threads_frames" if thread else "threads_list"
    status_code, data = await _do("GET", "/threads", params=params, timeout=_TIMEOUTS[timeout_key])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/threadlocals")
async def proxy_threadlocals(
    request: Request, report_id: str,
    groupBy: str = Query("valueClass", pattern=r"^(valueClass|thread)$"),
    top: int = Query(100, ge=1, le=500),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "groupBy": groupBy, "top": top}
    status_code, data = await _do("GET", "/threadlocals", params=params, timeout=_TIMEOUTS["threadlocals"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/object")
async def proxy_object(
    request: Request, report_id: str,
    id: int = Query(..., ge=0, description="objectId"),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "id": id}
    status_code, data = await _do("GET", "/object", params=params, timeout=_TIMEOUTS["object"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/path2gc")
async def proxy_path2gc(
    request: Request, report_id: str,
    object: int = Query(..., ge=0),
    excludeWeakSoft: bool = Query(True),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "object": object}
    if not excludeWeakSoft:
        params["excludeWeakSoft"] = "false"
    status_code, data = await _do("GET", "/path2gc", params=params, timeout=_TIMEOUTS["path2gc"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/top-consumers")
async def proxy_top_consumers(request: Request, report_id: str):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"]}
    status_code, data = await _do("GET", "/top-consumers", params=params, timeout=_TIMEOUTS["top-consumers"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/oom-diagnosis")
async def proxy_oom_diagnosis(
    request: Request, report_id: str,
    culpritPct: int = Query(30, ge=1, le=100),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "culpritPct": culpritPct}
    status_code, data = await _do("GET", "/oom-diagnosis", params=params, timeout=_TIMEOUTS["oom-diagnosis"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/sql-in-threads")
async def proxy_sql_in_threads(
    request: Request, report_id: str,
    onlyRisky: bool = Query(True),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params: Dict[str, Any] = {"dumpDir": r["dump_dir"]}
    if not onlyRisky:
        params["onlyRisky"] = "false"
    status_code, data = await _do("GET", "/sql-in-threads", params=params, timeout=_TIMEOUTS["sql-in-threads"])
    _quota_incr(user_id)
    return data


@router.get("/api/heapdump-reports/{report_id}/connection-pools")
async def proxy_connection_pools(request: Request, report_id: str):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"]}
    status_code, data = await _do("GET", "/connection-pools", params=params, timeout=_TIMEOUTS["connection-pools"])
    _quota_incr(user_id)
    _scrub_connection_pools(data)
    return data


_SECRET_KEY_RE = __import__("re").compile(r"password|secret|token|accesskey|access_key", __import__("re").I)


def _scrub_connection_pools(data: Any) -> None:
    """Remove sensitive config entries (password / secret / token) from the pools response."""
    if not isinstance(data, dict):
        return
    pools = data.get("pools")
    if not isinstance(pools, list):
        return
    for p in pools:
        if not isinstance(p, dict):
            continue
        cfg = p.get("config")
        if isinstance(cfg, list):
            p["config"] = [
                e for e in cfg
                if not (isinstance(e, dict) and _SECRET_KEY_RE.search(str(e.get("name", ""))))
            ]


@router.get("/api/heapdump-reports/{report_id}/pool-leaks")
async def proxy_pool_leaks(
    request: Request, report_id: str,
    thresholdMs: int = Query(300000, ge=1000),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    params = {"dumpDir": r["dump_dir"], "thresholdMs": thresholdMs}
    status_code, data = await _do("GET", "/pool-leaks", params=params, timeout=_TIMEOUTS["pool-leaks"])
    _quota_incr(user_id)
    return data


# ---------------------------------------------------------------------------
# OQL (POST body) — sync
# ---------------------------------------------------------------------------

@router.post("/api/heapdump-reports/{report_id}/oql")
async def proxy_oql(
    request: Request, report_id: str,
    view: str = Query("list", pattern=r"^(list|histogram)$"),
    sort: str = Query("shallow", pattern=r"^(shallow|retained|count)$"),
):
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    try:
        raw = (await request.body()).decode("utf-8") or "{}"
        body = json.loads(raw)
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON / Request body must be JSON")
    if not isinstance(body, dict) or not body.get("q"):
        raise HTTPException(400, "q 为必填字段 / 'q' is required")
    limit = int(body.get("limit", 200))
    if limit <= 0 or limit > 500:
        limit = 200 if limit <= 0 else 500
    body["limit"] = limit
    body.setdefault("offset", 0)

    params = {"dumpDir": r["dump_dir"], "view": view, "sort": sort}
    status_code, data = await _do("POST", "/oql", params=params, json_body=body, timeout=_TIMEOUTS["oql"])
    _quota_incr(user_id)
    log_audit(request, "report.heapdump.query.oql", user_id=user_id,
              resource=f"heapdump_report:{report_id}")
    return data


# ---------------------------------------------------------------------------
# Async submit endpoints (202 + {taskId, status:"RUNNING"})
# ---------------------------------------------------------------------------

async def _submit_async(
    method: str,
    java_path: str,
    request: Request,
    report_id: str,
    params: Dict[str, Any],
    json_body: Any = None,
    audit_key: Optional[str] = None,
) -> JSONResponse:
    user_id, r = _prep(request, report_id)
    _quota_check(user_id)
    full_params = {"dumpDir": r["dump_dir"]}
    full_params.update({k: v for k, v in params.items() if v is not None})

    charged = False
    try:
        status_code, data = await _do(
            method, java_path, params=full_params, json_body=json_body,
            timeout=_TIMEOUTS["submit"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _map_java_error(httpx.Response(500), e)

    # Java 成功接受才扣配额
    if status_code == 202:
        _quota_incr(user_id)
        charged = True
        tid = data.get("taskId") if isinstance(data, dict) else None
        if tid:
            _track_task(tid, user_id, report_id)

    if audit_key:
        log_audit(request, f"report.heapdump.query.{audit_key}",
                  user_id=user_id,
                  resource=f"heapdump_report:{report_id}")
    return JSONResponse(status_code=202, content=data)


@router.post("/api/heapdump-reports/{report_id}/leak-suspects", status_code=202)
async def proxy_leak_suspects(request: Request, report_id: str):
    return await _submit_async("POST", "/leak-suspects", request, report_id, {},
                               audit_key="leak-suspects")


@router.get("/api/heapdump-reports/{report_id}/diagnose-oom", status_code=202)
async def proxy_diagnose_oom(
    request: Request, report_id: str,
    culpritPct: int = Query(30, ge=1, le=100),
    onlyRisky: bool = Query(True),
):
    params: Dict[str, Any] = {"culpritPct": culpritPct}
    if not onlyRisky:
        params["onlyRisky"] = "false"
    return await _submit_async("GET", "/diagnose-oom", request, report_id, params,
                               audit_key="diagnose-oom")


@router.post("/api/heapdump-reports/{report_id}/retained-set", status_code=202)
async def proxy_retained_set(request: Request, report_id: str, objectSet: Optional[str] = Query(None)):
    params: Dict[str, Any] = {}
    json_body: Any = None
    if objectSet:
        params["objectSet"] = objectSet
    else:
        try:
            raw = (await request.body()).decode("utf-8") or "{}"
            body = json.loads(raw)
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON / Request body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("objects"), list):
            raise HTTPException(400, "objects 数组必填（或使用 ?objectSet=rs-xxx） / 'objects' array required or pass objectSet")
        if not body["objects"]:
            raise HTTPException(400, "objects 不能为空 / 'objects' must not be empty")
        json_body = body
    return await _submit_async("POST", "/retained-set", request, report_id, params,
                               json_body=json_body, audit_key="retained-set")


@router.post("/api/heapdump-reports/{report_id}/merge-paths", status_code=202)
async def proxy_merge_paths(request: Request, report_id: str, objectSet: Optional[str] = Query(None)):
    params: Dict[str, Any] = {}
    json_body: Any = None
    if objectSet:
        params["objectSet"] = objectSet
    else:
        try:
            raw = (await request.body()).decode("utf-8") or "{}"
            body = json.loads(raw)
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON / Request body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("objects"), list):
            raise HTTPException(400, "objects 数组必填（或使用 ?objectSet=rs-xxx） / 'objects' array required or pass objectSet")
        if not body["objects"]:
            raise HTTPException(400, "objects 不能为空 / 'objects' must not be empty")
        json_body = body
    return await _submit_async("POST", "/merge-paths", request, report_id, params,
                               json_body=json_body, audit_key="merge-paths")


# ---------------------------------------------------------------------------
# Async task poll / cancel
# ---------------------------------------------------------------------------

@router.get("/api/heapdump-reports/{report_id}/tasks/{task_id}")
async def proxy_task_poll(request: Request, report_id: str, task_id: str):
    user_id, r = _prep(request, report_id)
    _check_task_owner(task_id, user_id, report_id)
    # 注意：/tasks 不接受 dumpDir 参数（Java 端 task 是进程内的，不绑定 dump）
    status_code, data = await _do("GET", f"/tasks/{task_id}", timeout=_TIMEOUTS["poll"])
    # 终态回收所有权（DONE/FAILED/CANCELLED/NOT_FOUND）
    if isinstance(data, dict):
        s = data.get("status")
        if s in ("DONE", "FAILED", "CANCELLED") or s == "NOT_FOUND":
            _untrack_task(task_id)
    return data


@router.delete("/api/heapdump-reports/{report_id}/tasks/{task_id}")
async def proxy_task_cancel(request: Request, report_id: str, task_id: str):
    user_id, r = _prep(request, report_id)
    _check_task_owner(task_id, user_id, report_id)
    try:
        status_code, data = await _do("DELETE", f"/tasks/{task_id}", timeout=_TIMEOUTS["cancel"])
    except HTTPException as e:
        # Java 对未知任务返回 404 — treat as success (idempotent cancel)
        if e.status_code == 404:
            _untrack_task(task_id)
            return {"cancelled": True, "note": "task not found"}
        raise
    _untrack_task(task_id)
    return data
