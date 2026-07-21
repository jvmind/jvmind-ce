"""DatabaseUserManager — 单用户版

社区版无认证：所有调用都直接返回固定的本地用户。保留接口签名以最小化调用方改动。
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session

from .config import decrypt_config_secrets
from .db import SessionLocal
from .models import SessionModel, UserModel
from .timeutil import now_str

_HASH_ITERATIONS = 100_000

LOCAL_USER_ID = "user_local"
LOCAL_USERNAME = "local"
LOCAL_EMAIL = "local@jvmind.local"


def _hash_password(password: str) -> str:
    salt = "localuser$"  # 仅本地单用户模式，无需登录验证，固定 salt 可接受
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _HASH_ITERATIONS)
    return f"{salt}${base64.b64encode(dk).decode()}"


class UserData:
    def __init__(self, **kwargs):
        self.id: str = kwargs.get("id", "")
        self.username: str = kwargs.get("username", "")
        self.password_hash: str = kwargs.get("password_hash", "")
        self.is_admin: bool = bool(kwargs.get("is_admin", False))
        self.created_at: str = kwargs.get("created_at", "")
        self.email: str = kwargs.get("email", "")
        self.config: Optional[Dict[str, Any]] = kwargs.get("config")

    def to_safe_dict(self) -> Dict[str, Any]:
        from .config import LLMConfig
        d = {
            "id": self.id,
            "username": self.username,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
            "email": self.email,
        }
        if self.config:
            cfg = LLMConfig.from_dict(self.config)
            d["config"] = cfg.to_safe_dict()
        else:
            d["config"] = None
        return d


class DatabaseUserManager:
    """单用户版：所有用户操作都直接落到固定用户 user_local 上。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._local = threading.local()
        self._lock = threading.RLock()

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if callable(attr) and not name.startswith("_") and name != "close":
            def locked(*args, **kwargs):
                with object.__getattribute__(self, "_lock"):
                    return attr(*args, **kwargs)
            return locked
        return attr

    @property
    def db(self) -> Session:
        db = getattr(self._local, "db", None)
        if db is None:
            db = SessionLocal()
            self._local.db = db
        return db

    def _reset_db(self) -> Session:
        db = getattr(self._local, "db", None)
        try:
            if db:
                db.rollback()
                db.close()
        except Exception:
            pass
        db = SessionLocal()
        self._local.db = db
        return db

    def _query(self, *args, **kwargs):
        try:
            return self.db.query(*args, **kwargs)
        except sa_exc.InvalidRequestError:
            return self._reset_db().query(*args, **kwargs)

    def check_session_owner(self, sid: str, user_id: str) -> None:
        from fastapi import HTTPException
        try:
            s = self.db.query(SessionModel).filter(SessionModel.id == sid).first()
        except sa_exc.InvalidRequestError:
            self._reset_db()
            s = self.db.query(SessionModel).filter(SessionModel.id == sid).first()
        if not s:
            raise HTTPException(404, "会话不存在 / Session not found")
        if s.user_id and s.user_id != user_id:
            raise HTTPException(403, "无权访问该会话 / No permission to access this session")

    def close(self) -> None:
        with self._lock:
            db = getattr(self._local, "db", None)
            if db:
                try:
                    db.close()
                except Exception:
                    pass
                self._local.db = None

    def _model_to_userdata(self, u: UserModel) -> UserData:
        try:
            config = json.loads(u.config) if u.config and u.config != "{}" else None
        except Exception:
            config = None
        if config:
            try:
                config = decrypt_config_secrets(config)
            except Exception:
                pass
        return UserData(
            id=u.id,
            username=u.username,
            password_hash=u.password_hash,
            is_admin=bool(u.is_admin),
            created_at=u.created_at or "",
            email=u.email or "",
            config=config,
        )

    def _get_local_user(self) -> Optional[UserData]:
        u = self.db.query(UserModel).filter(UserModel.id == LOCAL_USER_ID).first()
        if not u:
            return None
        return self._model_to_userdata(u)

    # ---- 用户 CRUD（单用户版） ----

    def register(self, username: str, password: str, email: str = "") -> UserData:
        raise NotImplementedError("社区版不支持注册，请编辑 ~/.env 重启以重置用户")

    def login(self, username: str, password: str) -> Optional[UserData]:
        return self._get_local_user()

    def get_user(self, user_id: str) -> Optional[UserData]:
        if user_id != LOCAL_USER_ID:
            return None
        return self._get_local_user()

    def verify_password(self, user_id: str, password: str) -> bool:
        return user_id == LOCAL_USER_ID

    def update_password(self, user_id: str, new_password: str) -> None:
        return None

    def list_users(self) -> List[UserData]:
        u = self._get_local_user()
        return [u] if u else []

    def update_user_config(self, user_id: str, patch: Dict[str, Any]) -> UserData:
        u = self.db.query(UserModel).filter(UserModel.id == user_id).first()
        if not u:
            raise ValueError("用户不存在 / User not found")
        try:
            existing = json.loads(u.config) if u.config and u.config != "{}" else {}
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(patch or {})
        u.config = json.dumps(existing, ensure_ascii=False)
        self.db.commit()
        return self._model_to_userdata(u)

    # ---- 无登录态：JWT/token 全部走占位实现 ----

    def create_token(self, user_id: str) -> str:
        return f"local-{user_id}"

    def verify_token(self, token: str) -> Optional[str]:
        if not token:
            return None
        if token.startswith("local-"):
            return token[len("local-"):]
        return LOCAL_USER_ID

    def revoke_token(self, token: str) -> None:
        return None

    def create_refresh_token(self, user_id: str) -> str:
        return f"local-refresh-{user_id}"

    def verify_refresh_token(self, token: str) -> Optional[str]:
        if token and token.startswith("local-refresh-"):
            return token[len("local-refresh-"):]
        return None

    @property
    def jwt_secret(self) -> str:
        return ""

    # ---- 文件上传（无配额） ----

    def can_upload_file(self, user_id: str, lang: str = "") -> tuple:
        return True, ""

    def increment_file_upload(self, user_id: str) -> None:
        return None

    def try_consume_file_upload(self, user_id: str) -> tuple:
        return True, ""

    # ---- LLM 配额（无限制） ----

    def can_make_llm_call(self, user_id: str, lang: str = "") -> tuple:
        return True, ""

    def increment_llm_call(self, user_id: str) -> None:
        return None

    def try_consume_llm_call(self, user_id: str, lang: str = "") -> tuple:
        return True, ""

    def get_quota_info(self, user_id: str) -> Dict[str, Any]:
        return {
            "llm_calls_limit": -1,
            "llm_calls_used": 0,
            "llm_calls_remaining": -1,
            "quota_period": "unlimited",
            "cooldown_remaining_seconds": 0,
        }

    # ---- 会话配额（无限制） ----

    def can_create_session(self, user_id: str) -> tuple:
        return True, ""

    def get_max_sessions(self, user_id: str) -> int:
        return -1