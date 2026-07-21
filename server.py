"""FastAPI 后端：REST + SSE 流式 — JVMind Community Edition

启动：
    python -m jvmind          # 或 python server.py
    uvicorn server:app --reload --port 8000

环境变量：
    DATABASE_URL   数据库连接串（默认 sqlite:///./data/app.db）
    HOST / PORT    监听地址 / 端口
"""
from __future__ import annotations

import os
import sys
import socket
import logging
import uuid
from contextlib import asynccontextmanager

# Windows ProactorEventLoop patch for WinError 10054 (CPython #43253)
if sys.platform == "win32":
    from asyncio.proactor_events import _ProactorBasePipeTransport

    def _safe_call_connection_lost(self, exc):
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            if self._sock is not None:
                if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
                    try:
                        self._sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            server = self._server
            if server is not None:
                server._detach(self)
                self._server = None
            self._called_connection_lost = True

    _ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from fastapi.exception_handlers import http_exception_handler
from starlette.responses import Response as StarletteResponse

from app.core import state
from app.frontend import mount_frontend
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.config import router as config_router
from app.routes.feedback import router as feedback_router
from app.routes.gc_reports import router as gc_reports_router
from app.routes.health import router as health_router
from app.routes.heapdump_reports import router as heapdump_reports_router
from app.routes.heapdump_upload import router as heapdump_upload_router
from app.routes.heapdump_proxy import router as heapdump_proxy_router, close_mat_client
from app.routes.jstack_reports import router as jstack_reports_router
from app.routes.sessions import router as sessions_router
from app.routes.skills import router as skills_router
from app.routes.plans import router as plans_router
from app.routes.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from react_agent.db import engine as db_engine
    from app.core import state as app_state
    if app_state._USER_MANAGER:
        try:
            app_state._USER_MANAGER.close()
        except Exception:
            pass
    try:
        await close_mat_client()
    except Exception:
        pass
    db_engine.dispose()
    app_state._AGENTS.clear()
    app_state._SESSION_LOCKS.clear()


def _cleanup_db_sessions():
    """Close ThreadLocal DB sessions (UserManagerDB) after each request."""
    yield
    try:
        if state._USER_MANAGER:
            state._USER_MANAGER.close()
    except Exception:
        pass


app = FastAPI(title="JVMind CE", lifespan=lifespan, dependencies=[Depends(_cleanup_db_sessions)])


_g_exc_logger = logging.getLogger("server.exception")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    cid = uuid.uuid4().hex[:12]
    _g_exc_logger.exception("unhandled exception ref=%s path=%s", cid, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"内部错误（ref={cid}）/ Internal error (ref={cid})",
            "ref": cid,
        },
    )


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if state._COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=state._ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    allow_credentials=True,
)


app.include_router(auth_router)
app.include_router(health_router)
app.include_router(config_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(gc_reports_router)
app.include_router(jstack_reports_router)
app.include_router(heapdump_reports_router)
app.include_router(heapdump_upload_router)
app.include_router(heapdump_proxy_router)
app.include_router(skills_router)
app.include_router(feedback_router)
app.include_router(plans_router)
app.include_router(settings_router)

mount_frontend(app)


def main():
    import uvicorn
    os.makedirs("./data", exist_ok=True)
    log_path = os.getenv("SERVER_LOG_FILE", "./data/server.log")
    _file_handler = logging.FileHandler(log_path, encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _file_handler.propagate = False
    logging.getLogger().addHandler(_file_handler)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())

    ssl_keyfile = os.getenv("SSL_KEYFILE", "localhost-key.pem")
    ssl_certfile = os.getenv("SSLCERTFILE", "localhost.pem")
    uvicorn.run(
        "server:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        ssl_keyfile=ssl_keyfile if os.path.isfile(ssl_keyfile) else None,
        ssl_certfile=ssl_certfile if os.path.isfile(ssl_certfile) else None,
        reload=False,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()