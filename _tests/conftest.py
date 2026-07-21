"""Shared fixtures for JVMind CE test suite.

Sets environment variables BEFORE importing any project module.
社区版无认证/无Paddle/无邮件：相关 fixture 全部移除。
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

# ---------- Environment setup BEFORE any project import ----------

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="jvmind_tests_"))
_DB_FILE = _TMP_ROOT / "test.db"
_SESSION_DIR = _TMP_ROOT / "sessions"
_UPLOAD_DIR = _TMP_ROOT / "uploads"
_SESSION_DIR.mkdir(parents=True, exist_ok=True)
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("USE_DATABASE", "1")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.as_posix()}"
os.environ["SESSION_DIR"] = _SESSION_DIR.as_posix()
os.environ["UPLOAD_DIR"] = _UPLOAD_DIR.as_posix()
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-encryption-key-32b!")
os.environ.setdefault("COOKIE_SECURE", "0")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("FREE_TIER_API_KEY", "test-builtin-key")
os.environ.setdefault("FREE_TIER_BASE_URL", "https://fake-llm.local/v1")
os.environ.setdefault("FREE_TIER_MODEL", "fake-model")

import json  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

import server as server_module  # noqa: E402
from app.core import state  # noqa: E402
from react_agent import db as ra_db  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def app_module():
    return server_module


@pytest.fixture(scope="session")
def fastapi_app(app_module):
    return app_module.app


def _truncate_all_tables():
    from sqlalchemy import inspect, text
    try:
        ra_db.engine.dispose()
    except Exception:
        pass
    insp = inspect(ra_db.engine)
    with ra_db.engine.begin() as conn:
        for tbl in reversed(insp.get_table_names()):
            try:
                conn.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass


@pytest.fixture
def db_clean():
    """Reset DB rows + in-memory state between tests."""
    _truncate_all_tables()
    state._AGENTS.clear()
    state._USER_MANAGER = None
    state._SESSION_LOCKS.clear()
    ra_db.init_db()
    yield
    state._AGENTS.clear()
    state._USER_MANAGER = None


# ---------- Fake LLM agent (for streaming/chat) ----------

class _FakeMemoryShim:
    def __init__(self) -> None:
        self.context_facts: dict = {}
        self._sessions: dict = {}

    def set_context_fact(self, session_id, key, value):
        self.context_facts[(session_id, key)] = value

    def get_gc_report(self, sid, rid):
        return None

    def get_jstack_report(self, sid, rid):
        return None

    def list_sessions(self, **_kw):
        return list(self._sessions.values())

    def create_session(self, title, **_kw):
        sid = "sess_" + uuid.uuid4().hex[:8]
        self._sessions[sid] = {"id": sid, "title": title or "untitled", "messages": []}
        return sid

    def load(self, sid):
        return self._sessions.get(sid, {"id": sid, "messages": [], "facts": []})


class FakeAgent:
    def __init__(self, user_id: str = "", reply: str = "fake reply", real_memory=True) -> None:
        self.reply = reply
        self.received: list[tuple] = []
        self.skills: list = []
        if real_memory:
            self.memory = state.MemoryImpl(user_id=user_id, session_dir=os.path.join(_SESSION_DIR.as_posix(), user_id or "anon"))
        else:
            self.memory = _FakeMemoryShim()
        self.api_key = "test"
        self.base_url = "https://fake.local/v1"
        self.model = "fake-model"
        self.temperature = 0.0
        self.max_iterations = 1

    def load_skills(self, skills):
        self.skills = list(skills or [])

    def run_stream(self, session_id: str, message: str, llm_input: str = "", **kw):
        self.received.append((session_id, message, llm_input))
        try:
            if hasattr(self.memory, "append_message"):
                self.memory.append_message(session_id, "user", message)
        except Exception:
            pass
        yield {"type": "user", "content": message}
        yield {"type": "token", "phase": "final", "content": self.reply}
        yield {"type": "final", "content": self.reply}
        try:
            if hasattr(self.memory, "append_message"):
                self.memory.append_message(session_id, "assistant", self.reply)
        except Exception:
            pass
        yield {"type": "done"}


def _inject_fake_agent_for(user_id: str, reply: str = "fake reply") -> FakeAgent:
    fa = FakeAgent(user_id=user_id, reply=reply)
    state._AGENTS[user_id] = fa
    return fa


@pytest.fixture
def make_fake_agent():
    return _inject_fake_agent_for


# ---------- Live uvicorn server (port 0) ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(fastapi_app):
    import uvicorn
    port = _free_port()
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("uvicorn failed to start in time")
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def client(live_server) -> Iterator[httpx.Client]:
    """Anonymous httpx client pointing at the live uvicorn server.

    社区版无登录：所有请求直接落本地用户 user_local，无需登录/CSRF。
    """
    with httpx.Client(base_url=live_server, timeout=15.0) as c:
        yield c


@pytest.fixture
def auth_client(db_clean, client):
    """兼容旧测试命名 — 社区版下等价于 client（无登录态）。"""
    return client, {"id": "user_local"}


@pytest.fixture
def admin_client(db_clean, client):
    """兼容旧测试命名 — 社区版下所有用户都是 admin。"""
    return client, {"id": "user_local", "is_admin": True}


def _register_and_login(*args, **kwargs) -> dict:
    """CE 无注册：所有请求按 user_local 处理。仅供旧测试导入兼容。"""
    return {"user": {"id": "user_local", "is_admin": True}}


@pytest.fixture
async def asgi_client(db_clean, fastapi_app):
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for chunk in text.split("\n\n"):
        line = next((ln for ln in chunk.splitlines() if ln.startswith("data:")), None)
        if not line:
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            events.append(json.loads(body))
        except Exception:
            events.append({"_raw": body})
    return events


@pytest.fixture
def sse_parser():
    return parse_sse