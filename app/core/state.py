from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Dict

from react_agent.db import init_db
from react_agent.memory_db import DatabaseMemory
from react_agent.user_manager_db import DatabaseUserManager
from react_agent.skill_manager_db import DatabaseSkillManager


class _LRUDict(OrderedDict):
    """Dict with bounded size, evicts least-recently-used entry. Thread-safe."""

    def __init__(self, maxsize: int = 0):
        super().__init__()
        self._maxsize = int(maxsize or 0)
        self._lock = threading.RLock()

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)

    def __getitem__(self, key):
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key, default=None):
        with self._lock:
            if super().__contains__(key):
                value = super().__getitem__(key)
                self.move_to_end(key)
                return value
            return default

    def __setitem__(self, key, value):
        with self._lock:
            if super().__contains__(key):
                super().__setitem__(key, value)
                self.move_to_end(key)
                return
            super().__setitem__(key, value)
            if self._maxsize > 0:
                while len(self) > self._maxsize:
                    old_key = next(iter(self))
                    super().__delitem__(old_key)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)

    def clear(self):
        with self._lock:
            super().clear()

    def pop(self, key, *args):
        with self._lock:
            return super().pop(key, *args)

    def __len__(self):
        with self._lock:
            return super().__len__()


_USE_DATABASE = True
MemoryImpl = DatabaseMemory
UserMgrImpl = DatabaseUserManager
SkillMgrImpl = DatabaseSkillManager
init_db()

# 单用户：所有请求都按 LOCAL_USER_ID 运行（社区版无认证）
DEFAULT_USER_ID = "user_local"

_AGENTS_MAX = int(os.getenv("AGENT_CACHE_MAX", "500"))
_SESSION_LOCKS_MAX = int(os.getenv("SESSION_LOCK_CACHE_MAX", "2000"))

_USER_MANAGER = None
_AGENTS: Dict[str, object] = _LRUDict(_AGENTS_MAX)
_AGENTS_LOCK = threading.Lock()
_SESSION_LOCKS: Dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()

_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"]

_ALLOWED_GC_EXTS = {".log", ".txt", ".gc"}
_ALLOWED_JSTACK_EXTS = {".txt", ".log", ".tdump", ".jstack", ".json"}
_MAX_GC_SIZE = 10 * 1024 * 1024
_MAX_JSTACK_SIZE = 5 * 1024 * 1024

_LOAD_TEST = os.getenv("LOAD_TEST_MODE", "0").lower() in ("1", "true", "yes")