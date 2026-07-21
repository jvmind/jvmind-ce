from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import APITimeoutError, AuthenticationError
from sse_starlette.sse import EventSourceResponse

from app.core import helpers, state
from app.schemas import ChatReq, ChatStopReq
from react_agent.i18n import _bf

router = APIRouter(prefix="/api/chat", tags=["chat"])
_logger = logging.getLogger(__name__)


def _remember_report_context(agent, session_id: str, contexts: Optional[List[Dict[str, Any]]], lang: str = "") -> None:
    if not contexts or not hasattr(agent.memory, "set_context_fact"):
        return
    lines = []
    for ctx in contexts[:5]:
        rtype = str(ctx.get("type") or "").lower()
        sid = str(ctx.get("session_id") or "")
        rid = str(ctx.get("report_id") or "")
        if rtype not in ("gc", "jstack", "heapdump") or not sid or not rid:
            continue
        if rtype == "gc":
            report = agent.memory.get_gc_report(sid, rid)
        elif rtype == "jstack":
            report = agent.memory.get_jstack_report(sid, rid)
        else:  # heapdump
            report = agent.memory.get_heapdump_report(sid, rid)
        if not report:
            continue
        if rtype == "gc":
            label = "GC"
            file_id = report.get("file_id", "")
            filename = report.get("filename", "")
            tool_hint = f"read_gc_report({rid})"
            lines.append(f"{label} report: session_id={sid}, report_id={rid}, file_id={file_id}, filename={filename}, tool={tool_hint}")
        elif rtype == "jstack":
            label = "JStack"
            file_id = report.get("file_id", "")
            filename = report.get("filename", "")
            tool_hint = f"read_jstack_report({rid}); analyze_specific_thread({file_id},thread_name/nid)"
            lines.append(f"{label} report: session_id={sid}, report_id={rid}, file_id={file_id}, filename={filename}, tool={tool_hint}")
        else:  # heapdump
            label = "Heapdump"
            filename = report.get("filename", "")
            status = report.get("status", "")
            progress = report.get("progress", 0)
            tool_hint = f"mat_overview({rid}); mat_leak_suspects({rid}); mat_histogram({rid}); mat_dominator({rid})"
            lines.append(
                f"{label} report: session_id={sid}, report_id={rid}, filename={filename}, "
                f"status={status}, progress={progress}%, tool={tool_hint}"
            )
    if lines:
        prefix = "当前附加的报告" if lang == "zh" else "Current attached reports for this conversation"
        agent.memory.set_context_fact(session_id, "reports", prefix + ": " + " | ".join(lines))


def _build_report_context_message(agent, user_message: str, contexts: Optional[List[Dict[str, Any]]], lang: str = "") -> str:
    if not contexts:
        return user_message
    blocks = []
    for ctx in contexts[:5]:
        rtype = str(ctx.get("type") or "").lower()
        sid = str(ctx.get("session_id") or "")
        rid = str(ctx.get("report_id") or "")
        if rtype not in ("gc", "jstack", "heapdump") or not sid or not rid:
            continue
        if rtype == "gc":
            report = agent.memory.get_gc_report(sid, rid)
        elif rtype == "jstack":
            report = agent.memory.get_jstack_report(sid, rid)
        else:
            report = agent.memory.get_heapdump_report(sid, rid)
        if not report:
            continue
        filename = report.get("filename", "")
        index = len(blocks) + 1
        if rtype == "gc":
            label = "GC"
            tool_hint = f"read_gc_report({rid})"
        elif rtype == "jstack":
            label = "JStack"
            file_id = report.get("file_id", "")
            tool_hint = f"read_jstack_report({rid}) ; for single thread drilling use analyze_specific_thread({file_id},thread_name/nid)"
        else:
            label = "Heapdump"
            status = report.get("status", "")
            progress = report.get("progress", 0)
            if status != "DONE":
                # 未就绪：明确告诉 agent 无法调查询工具
                tool_hint = (
                    f"[{status} progress={progress}%] 报告仍在解析中，MAT 工具尚不可用；"
                    f"仅可读取 mat_overview({rid}) 的元数据"
                    if lang == "zh"
                    else f"[{status} progress={progress}%] Report still parsing; MAT tools not yet available; "
                         f"only mat_overview({rid}) metadata is readable"
                )
            else:
                tool_hint = (
                    f"mat_overview({rid}); mat_leak_suspects({rid}); mat_histogram({rid}); "
                    f"mat_dominator({rid}); mat_threads({rid}); mat_connection_pools({rid}); "
                    f"mat_diagnose_oom({rid}); mat_oql({rid},<query>); mat_object({rid},<objectId>); "
                    f"mat_path2gc({rid},<objectId>)"
                )
        if lang == "zh":
            blocks.append(
                f"[R{index}] {label} · {filename}\n"
                f"     读取详情工具：{tool_hint}"
            )
        else:
            blocks.append(
                f"[R{index}] {label} · {filename}\n"
                f"     Read detail tool: {tool_hint}"
            )
    if not blocks:
        return user_message
    if lang == "zh":
        return (
            "以下只是用户当前附加的报告索引清单，不是完整报告内容。\n"
            "回答规则：\n"
            "1. 用户提到「这份报告/当前报告/上述报告」时，优先指向这些索引。\n"
            "2. 需要统计、结论、线程、GC 细节时，必须调用对应读取工具，不要凭索引元信息编造。\n"
            "3. 如果附加多份报告且用户没有指明对象，请按 R1~R5 分别说明，或在必须单选时先澄清。\n"
            "4. Heapdump 报告若 status != DONE，MAT 查询工具不可用，请告知用户等待解析完成。\n\n"
            + "\n".join(blocks)
            + f"\n\n用户问题：\n{user_message}"
        )
    return (
        "Below is the user's attached report index list, NOT the full report content.\n"
        "Rules:\n"
        "1. When user mentions \"this report/current report/above report\", prioritize these indexes.\n"
        "2. For stats, conclusions, threads, GC details, you MUST invoke the corresponding read tool — "
        "do NOT fabricate from index metadata.\n"
        "3. If multiple reports are attached and user doesn't specify which, explain per R1/R2/R3, "
        "or clarify first when a single choice is required.\n"
        "4. For heapdump reports with status != DONE, MAT query tools are not usable; tell the user to wait.\n\n"
        + "\n".join(blocks)
        + f"\n\nUser message:\n{user_message}"
    )


@router.post("/stop")
def chat_stop(request: Request, req: ChatStopReq):
    user_id = helpers._get_current_user(request)
    helpers._check_session_owner(req.session_id, user_id)
    helpers.set_cancel_flag(req.session_id)
    return {"cancelled": True}


@router.post("/stream")
def chat_stream(request: Request, req: ChatReq):
    user_id = helpers._get_current_user(request)
    lang = req.lang or ""

    err = helpers._check_llm_ready(user_id, lang)
    if err:
        def _cfg_err():
            yield {"event": "error", "data": json.dumps({"type": "error", "content": err})}
            yield {"event": "done", "data": json.dumps({"type": "done"})}
        return EventSourceResponse(_cfg_err())

    # 内容安全过滤
    from react_agent.content_filter import check_input
    ok, reason = check_input(req.message)
    if not ok:
        def _filter_err():
            yield {"event": "error", "data": json.dumps({"type": "error", "content": reason})}
            yield {"event": "done", "data": json.dumps({"type": "done"})}
        return EventSourceResponse(_filter_err())

    # 输入长度限制
    max_len = 100000
    if state._USE_DATABASE:
        try:
            from app.routes.settings import get_public_settings
            max_len = int(get_public_settings().get("max_input_length", 100000))
        except Exception:
            _logger.debug("max_input_length lookup failed; using default", exc_info=True)
    if len(req.message) > max_len:
        def _len_err():
            yield {"event": "error", "data": json.dumps({"type": "error", "content": _bf(
                "消息过长，最大 {n} 字符", "Message too long, max {n} characters", lang, n=max_len,
            )})}
            yield {"event": "done", "data": json.dumps({"type": "done"})}
        return EventSourceResponse(_len_err())

    helpers._check_session_owner(req.session_id, user_id)
    session_lock = helpers._get_session_lock(req.session_id)
    if not session_lock.acquire(blocking=False):
        helpers._release_session_lock(req.session_id)
        return JSONResponse(
            {"detail": "当前会话正在生成回复，请等待完成后再发送 / Session is generating a reply, please wait"},
            status_code=409,
        )

    helpers.clear_cancel_flag(req.session_id)

    try:
        report_contexts = req.report_contexts or ([req.report_context] if req.report_context else [])
        report_contexts = report_contexts[:5]
        for ctx in report_contexts:
            if ctx and ctx.get("session_id"):
                helpers._check_session_owner(str(ctx.get("session_id")), user_id)
        agent = helpers._get_agent(user_id)
        _remember_report_context(agent, req.session_id, report_contexts, lang)
        llm_message = _build_report_context_message(agent, req.message, report_contexts, lang)
    except Exception:
        session_lock.release()
        helpers._release_session_lock(req.session_id)
        raise

    def event_gen():
        try:
            try:
                should_stop = lambda: helpers.is_cancelled(req.session_id)
                for event in agent.run_stream(req.session_id, req.message, llm_input=llm_message, lang=lang, should_stop=should_stop):
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event, ensure_ascii=False),
                    }
            except APITimeoutError:
                yield {"event": "error", "data": json.dumps({"type": "error", "content": "大模型请求超时，请检查 API Key 和网络连接 / LLM request timeout, check API Key and network"}, ensure_ascii=False)}
                yield {"event": "done", "data": json.dumps({"type": "done"})}
            except AuthenticationError:
                yield {"event": "error", "data": json.dumps({"type": "error", "content": "API Key 无效或已过期，请在 ⚙️ 配置中更新 / Invalid or expired API Key, update in ⚙️ Settings"}, ensure_ascii=False)}
                yield {"event": "done", "data": json.dumps({"type": "done"})}
            except Exception as e:
                yield {
                    "event": "error",
                    "data": json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False),
                }
                yield {"event": "done", "data": json.dumps({"type": "done"})}
        finally:
            helpers.clear_cancel_flag(req.session_id)
            session_lock.release()
            helpers._release_session_lock(req.session_id)

    return EventSourceResponse(event_gen())
