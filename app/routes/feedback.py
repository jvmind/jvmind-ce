"""诊断反馈采集路由。

用户对 AI 诊断结论（GC/jstack 报告型，或对话型回复）打「有用 / 无用 / 错误」，
并可附自由文本。采集的数据用于驱动提示词/诊断规则的持续迭代飞轮。

提交时会快照产出该结论时使用的提示词版本（prompt_key）与模型（model）——
报告型可从报告统计中回溯，对话型由调用方传入或留空，便于后续差评样本回溯迭代。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.core import helpers, state
from app.schemas import FeedbackReq
from app.services.audit import log_audit
from react_agent.db import session_scope
from react_agent.models import (
    DiagnosisFeedbackModel,
    GCReportModel,
    JStackReportModel,
    MessageModel,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
_logger = logging.getLogger(__name__)

_VALID_TARGET_TYPES = {"gc", "jstack", "chat"}
_VALID_VERDICTS = {"useful", "useless", "wrong"}
_MAX_COMMENT_LEN = 2000


def _snapshot_prompt_key(target_type: str, lang_hint: str = "") -> str:
    """诊断使用的提示词键。

    - gc/jstack 报告型：与 analysis_prompts.PROMPT_KEYS 对齐的功能提示词键；
    - chat 对话型：由 ReAct 系统提示词驱动，记其系统设置键 prompt_react_agent。
    """
    if target_type in ("gc", "jstack"):
        lang = "zh" if lang_hint == "zh" else "en"
        return f"prompt_{target_type}_{lang}"
    if target_type == "chat":
        return "prompt_react_agent"
    return ""


def _snapshot_model(user_id: str) -> str:
    """取产出该结论时用户 agent 实际使用的模型名（飞轮迭代关键维度）。"""
    try:
        agent = helpers._get_agent(user_id)
        return getattr(agent, "model", "") or ""
    except Exception:
        _logger.debug("model snapshot failed for user %s", user_id, exc_info=True)
        return ""


@router.post("")
def submit_feedback(request: Request, req: FeedbackReq):
    user_id = helpers._get_current_user(request)

    target_type = (req.target_type or "").strip().lower()
    verdict = (req.verdict or "").strip().lower()
    target_id = (req.target_id or "").strip()
    comment = (req.comment or "").strip()[:_MAX_COMMENT_LEN]

    if target_type not in _VALID_TARGET_TYPES:
        raise HTTPException(400, "无效的反馈对象类型 / Invalid target_type")
    if verdict not in _VALID_VERDICTS:
        raise HTTPException(400, "无效的反馈评价 / Invalid verdict")
    if not target_id:
        raise HTTPException(400, "缺少反馈对象 / Missing target_id")

    # 校验会话归属（若提供）
    if req.session_id:
        helpers._check_session_owner(req.session_id, user_id)

    with session_scope() as db:
        prompt_key = _snapshot_prompt_key(target_type)
        model = _snapshot_model(user_id)

        # 校验 target 存在并归属当前用户的会话
        if target_type in ("gc", "jstack"):
            ReportModel = GCReportModel if target_type == "gc" else JStackReportModel
            report = db.query(ReportModel).filter(ReportModel.id == target_id).first()
            if not report:
                raise HTTPException(404, "报告不存在 / Report not found")
            # 报告必属于某会话，校验该会话归属
            helpers._check_session_owner(report.session_id, user_id)
        else:  # chat
            try:
                mid = int(target_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "对话反馈的 target_id 必须为消息 id / chat target_id must be a message id")
            msg = db.query(MessageModel).filter(MessageModel.id == mid).first()
            if not msg:
                raise HTTPException(404, "消息不存在 / Message not found")
            helpers._check_session_owner(msg.session_id, user_id)

        # upsert：同一用户对同一对象只保留最新一条反馈
        existing = (
            db.query(DiagnosisFeedbackModel)
            .filter(
                DiagnosisFeedbackModel.user_id == user_id,
                DiagnosisFeedbackModel.target_type == target_type,
                DiagnosisFeedbackModel.target_id == target_id,
            )
            .first()
        )
        if existing:
            existing.verdict = verdict
            existing.comment = comment
            existing.session_id = req.session_id or existing.session_id
            existing.updated_at = helpers._now_str()
            if prompt_key:
                existing.prompt_key = prompt_key
            if model:
                existing.model = model
        else:
            db.add(DiagnosisFeedbackModel(
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
                session_id=req.session_id,
                verdict=verdict,
                comment=comment,
                prompt_key=prompt_key,
                model=model,
            ))
        db.commit()

    log_audit(
        request,
        "feedback.submit",
        user_id=user_id,
        resource=f"{target_type}:{target_id}",
        details={"verdict": verdict, "has_comment": bool(comment)},
    )
    return {"ok": True}


@router.get("/{target_type}/{target_id}")
def get_feedback(request: Request, target_type: str, target_id: str):
    """回显当前用户对某对象已有的反馈（前端用于高亮 👍/👎 状态）。"""
    user_id = helpers._get_current_user(request)
    target_type = (target_type or "").strip().lower()
    if target_type not in _VALID_TARGET_TYPES:
        raise HTTPException(400, "无效的反馈对象类型 / Invalid target_type")
    with session_scope() as db:
        fb = (
            db.query(DiagnosisFeedbackModel)
            .filter(
                DiagnosisFeedbackModel.user_id == user_id,
                DiagnosisFeedbackModel.target_type == target_type,
                DiagnosisFeedbackModel.target_id == (target_id or "").strip(),
            )
            .first()
        )
        if not fb:
            return {"feedback": None}
        return {"feedback": {"verdict": fb.verdict, "comment": fb.comment or ""}}
