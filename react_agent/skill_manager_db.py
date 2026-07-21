"""Skill 管理系统：技能 CRUD、从会话提取 — 数据库存储版本"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import SkillModel


def _now_iso() -> str:
    from .timeutil import now_str
    return now_str()


class DatabaseSkillManager:
    """管理单个用户的技能列表，使用数据库存储。"""

    def __init__(self, user_id: str, db_session: Optional[Session] = None) -> None:
        self.user_id = user_id
        self._db: Optional[Session] = db_session
        self._owns_db = db_session is None

    # ---- 内部工具 ----
    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def close(self) -> None:
        if self._owns_db and self._db:
            try:
                self._db.rollback()
            except Exception:
                pass
            self._db.close()
            self._db = None

    def list(self) -> List[Dict[str, Any]]:
        """列出用户的所有技能"""
        try:
            skills = self.db.query(SkillModel)\
                .filter(SkillModel.user_id == self.user_id)\
                .order_by(SkillModel.created_at)\
                .all()
            return [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "instruction": s.instruction,
                    "category": s.category,
                    "args_hint": s.args_hint,
                    "source": s.source,
                    "created_at": s.created_at,
                }
                for s in skills
            ]
        finally:
            self.close()

    def get(self, skid: str) -> Optional[Dict[str, Any]]:
        """获取单个技能"""
        try:
            skill = self.db.query(SkillModel)\
                .filter(SkillModel.id == skid)\
                .filter(SkillModel.user_id == self.user_id)\
                .first()
            if not skill:
                return None
            return {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "instruction": skill.instruction,
                "category": skill.category,
                "args_hint": skill.args_hint,
                "source": skill.source,
                "created_at": skill.created_at,
            }
        finally:
            self.close()

    def create(self, skill: Dict[str, Any]) -> str:
        """创建新技能"""
        try:
            skid = "sk_" + uuid.uuid4().hex[:10]
            skill_model = SkillModel(
                id=skid,
                user_id=self.user_id,
                name=skill.get("name", ""),
                description=skill.get("description", ""),
                instruction=skill.get("instruction", ""),
                category=skill.get("category", ""),
                args_hint=skill.get("args_hint", "input"),
                source=skill.get("source", "manual"),
                created_at=skill.get("created_at", _now_iso()),
            )
            self.db.add(skill_model)
            self.db.commit()
            return skid
        finally:
            self.close()

    def update(self, skid: str, patch: Dict[str, Any]) -> bool:
        """更新技能"""
        try:
            skill = self.db.query(SkillModel)\
                .filter(SkillModel.id == skid)\
                .filter(SkillModel.user_id == self.user_id)\
                .first()
            if not skill:
                return False
            if "name" in patch and patch["name"] is not None:
                skill.name = patch["name"]
            if "description" in patch and patch["description"] is not None:
                skill.description = patch["description"]
            if "instruction" in patch and patch["instruction"] is not None:
                skill.instruction = patch["instruction"]
            if "category" in patch and patch["category"] is not None:
                skill.category = patch["category"]
            if "args_hint" in patch and patch["args_hint"] is not None:
                skill.args_hint = patch["args_hint"]
            self.db.commit()
            return True
        finally:
            self.close()

    def delete(self, skid: str) -> bool:
        """删除技能"""
        try:
            skill = self.db.query(SkillModel)\
                .filter(SkillModel.id == skid)\
                .filter(SkillModel.user_id == self.user_id)\
                .first()
            if not skill:
                return False
            self.db.delete(skill)
            self.db.commit()
            return True
        finally:
            self.close()

    @staticmethod
    def extract_draft(messages: List[Dict[str, str]], draft_name: str = "") -> Dict[str, str]:
        """从对话消息中提取 skill 草稿（仅做简单摘要，LLM 提炼由前端触发）。"""
        # 拼接消息文本（全量不截断）
        text = "\n".join(
            f"{m.get('role','?')}: {m.get('content','')}" for m in messages
        )
        draft = {
            "name": draft_name or "",
            "description": "",
            "instruction": text,
            "args_hint": "input",
            "category": "",
        }
        return draft
