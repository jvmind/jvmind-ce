from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.core import helpers, state
from app.services.audit import log_audit

router = APIRouter(prefix="/api/skills", tags=["skills"])
_logger = logging.getLogger(__name__)


def _get_skill_manager(user_id: str) -> state.SkillMgrImpl:
    return state.SkillMgrImpl(user_id)


@router.get("")
def list_skills(request: Request):
    user_id = helpers._get_current_user(request)
    sm = _get_skill_manager(user_id)
    return {"skills": sm.list()}


@router.post("")
def create_skill(request: Request, body: dict):
    user_id = helpers._get_current_user(request)
    sm = _get_skill_manager(user_id)
    required = ["name", "description", "instruction"]
    for f in required:
        if f not in body or not body[f].strip():
            raise HTTPException(400, f"缺少必填字段 '{f}'")
    skid = sm.create(body)
    if user_id in state._AGENTS:
        del state._AGENTS[user_id]
    log_audit(request, "skill.create", user_id=user_id, resource=f"skill:{skid}", details={"name": body.get("name", ""), "category": body.get("category", "")})
    return {"id": skid, "ok": True}


@router.get("/{skid}")
def get_skill(request: Request, skid: str):
    user_id = helpers._get_current_user(request)
    sm = _get_skill_manager(user_id)
    skill = sm.get(skid)
    if not skill:
        raise HTTPException(404, "skill not found")
    return skill


@router.put("/{skid}")
def update_skill(request: Request, skid: str, body: dict):
    user_id = helpers._get_current_user(request)
    sm = _get_skill_manager(user_id)
    ok = sm.update(skid, body)
    if not ok:
        raise HTTPException(404, "skill not found")
    if user_id in state._AGENTS:
        del state._AGENTS[user_id]
    log_audit(request, "skill.update", user_id=user_id, resource=f"skill:{skid}", details={"fields": sorted(body.keys()), "name": body.get("name", "")})
    return {"ok": True}


@router.delete("/{skid}")
def delete_skill(request: Request, skid: str):
    user_id = helpers._get_current_user(request)
    sm = _get_skill_manager(user_id)
    skill = sm.get(skid)
    ok = sm.delete(skid)
    if not ok:
        raise HTTPException(404, "skill not found")
    if user_id in state._AGENTS:
        del state._AGENTS[user_id]
    log_audit(request, "skill.delete", user_id=user_id, resource=f"skill:{skid}", details={"name": (skill or {}).get("name", "")})
    return {"deleted": True}


@router.post("/extract")
def extract_skill_draft(request: Request, body: dict):
    user_id = helpers._get_current_user(request)
    messages = body.get("messages", [])
    draft_name = body.get("draft_name", "")
    if not messages:
        raise HTTPException(400, "请提供对话消息 / Please provide conversation messages")
    log_audit(request, "skill.extract", user_id=user_id, details={"draft_name": draft_name, "messages_count": len(messages)})
    draft = state.SkillMgrImpl.extract_draft(messages, draft_name)

    if state._AGENTS.get(user_id):
        try:
            agent = state._AGENTS[user_id]
            msg_text = "\n".join(
                f"{m.get('role','?')}: {m.get('content','')}" for m in messages
            )[:3000]
            llm_prompt = (
                "你是一个 Skill 提取助手。以下是用户选择的一段对话，请分析并提炼出一个可复用的技能（skill）。\n\n"
                "请严格按以下 JSON 格式返回，不要加解释和 markdown 围栏：\n"
                "{\n"
                '  "name": "简短英文或拼音技能名（如 gc_analysis、code_review），只含小写字母和下划线",\n'
                '  "description": "一句话描述（中文，不超过 50 字，让 Agent 能判断何时调用此技能）",\n'
                '  "instruction": "详细的执行指导（中文 Markdown，包含：前置条件、执行步骤、输出要求、注意事项）",\n'
                '  "args_hint": "期望的输入格式描述，如 \\"问题描述\\"、\\"日志文本\\"、\\"SQL 语句\\"",\n'
                '  "category": "分类，从 [开发, 运维, 分析, 通用] 中选一个"\n'
                "}\n\n"
                "示例输出：\n"
                "{\n"
                '  "name": "gc_log_analysis",\n'
                '  "description": "分析 Java GC 日志，诊断停顿和内存问题",\n'
                '  "instruction": "## 执行步骤\\n1. 解析用户提供的 GC 日志信息\\n2. 统计各类型 GC 事件频率和停顿时间\\n3. 识别异常模式（频繁 Full GC、停顿突增）\\n4. 给出调优建议参数",\n'
                '  "args_hint": "GC 日志片段或 file_id",\n'
                '  "category": "分析"\n'
                "}\n\n"
                f"对话内容：\n{msg_text}"
            )
            refined = agent._chat(
                [{"role": "user", "content": llm_prompt}],
                stop=None,
            )
            import json as _json
            try:
                clean = refined.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[-1]
                    clean = clean.rsplit("```", 1)[0].strip()
                parsed = _json.loads(clean)
                if parsed.get("name"):
                    draft.update(parsed)
            except Exception as e:
                draft["_llm_raw"] = refined[:500]
        except Exception:
            _logger.debug("skill draft LLM refinement failed; returning raw draft", exc_info=True)

    return {"draft": draft}
