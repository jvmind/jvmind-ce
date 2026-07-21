# JVMind (ReAct Agent) — AGENTS.md

> **编码前请先读 [`CONVENTIONS.md`](./CONVENTIONS.md)** — 代码风格、模式范例与反模式速查。本文件聚焦架构、命令与测试运维。

## Entrypoints

- **Backend**: `server.py` — FastAPI app (uvicorn). Also `app.py` (Gradio, secondary).
- **Frontend**: `frontend/` — vanilla JS ES modules built with Vite. Dev server: `npm run dev` (proxies `/api` → `:8000`). Build: `npm run build`.
- **Core agent**: `react_agent/agent.py` — `ReActAgent.run()` / `run_stream()`.

## Architecture

- `app/` = FastAPI routes, middleware, state. `react_agent/` = domain logic (agent, tools, DB models, analyzers, billing).
- **DB-only**: `app/core/state.py` hardcodes `_USE_DATABASE = True`. JSON file storage is deprecated. Uses SQLAlchemy + SQLite (default) / PostgreSQL.
- **Per-user agents**: `state._AGENTS[user_id]` dict, lazy-created via `helpers._get_agent()`.
- **Frontend**: No framework — vanilla ES modules in `frontend/src/`. State is a global object in `state.js`.
- **CSRF**: All non-GET endpoints (except auth/webhook exemptions) require `X-CSRF-Token` header + `csrf_token` cookie.
- **API keys**: Encrypted at rest with `CONFIG_ENCRYPTION_KEY`. Stored in DB per-user. `config.json` holds only `jwt_secret`.

## Python environment

Project uses a **local `.venv/`** (not poetry/pipenv). Always activate before running any Python command:

```bash
source .venv/bin/activate
```

All deps are in `requirements.txt` + `requirements-dev.txt`. Install with `pip install -r requirements.txt -r requirements-dev.txt`. Do NOT use system `python`/`pip` — the venv pins versions (FastAPI, SQLAlchemy 2.x, paddle-python-sdk==1.14.1, etc.).

## Commands

(All Python commands assume `.venv` is activated.)

```bash
# Backend dev
python server.py                   # http://127.0.0.1:8000 (auto-detects localhost.pem for HTTPS)
uvicorn server:app --reload --port 8000

# Heapdump worker (独立进程, 消费 heapdump_reports 表的 QUEUED 任务)
python -m react_agent.heapdump_worker         # 前台运行, Ctrl-C 优雅退出
HEAPDUMP_WORKER_LOG_LEVEL=DEBUG python -m react_agent.heapdump_worker

# Tests (pytest, full suite with coverage gate 63%)
python -m pytest _tests                       # isolated sqlite, live uvicorn on port 0
python -m pytest _tests -x --no-cov           # stop on first fail, skip coverage
python -m pytest _tests/test_billing.py -v --no-cov   # single file

# Frontend (separate Node toolchain under frontend/)
cd frontend && npm run dev          # Vite dev (port 3000, proxy to :8000)
cd frontend && npm run build        # outputs to frontend/dist/
cd frontend && npm run test         # vitest (jsdom)
```

## Testing quirks

- **Critical**: `conftest.py` sets env vars **before** project imports (SQLite temp DB, isolated session/uploads dirs, rate limits at 10000).
- `db_clean` fixture truncates all tables + clears in-memory state between tests. Always use via `auth_client` / `admin_client`.
- `auth_client` auto-sets `X-CSRF-Token` header. `admin_client` flips `is_admin=1` via direct DB write.
- `make_fake_agent(user_id, reply)` fixture stubs `ReActAgent.run_stream()` to return fake SSE events.
- `fake_paddle` replaces `state._PADDLE` with `_FakePaddle` that captures calls instead of hitting Paddle.
- `fake_email` captures verification codes into a list instead of sending SMTP.
- To stub LLM in route tests: `monkeypatch.setattr(ReActAgent, "_chat_stream", fake_gen)`.
- Plan-gated routes need `pro`/`team` plan — tests promote via direct DB write (no checkout endpoint exists).
- `validate_openai_base_url` does real DNS; patch **both** `app.routes.config` and `react_agent.user_manager_db` in tests.
- **No `pytest-xdist`** (single-process only — uvicorn shares global state + same sqlite file).
- Coverage gate: `pytest.ini` sets `--cov-fail-under=63`.
- Markers: `db` (SQLite-backed), `smoke_llm` (hits real LLM, skipped by default), `paddle_sandbox` (hits real Paddle sandbox, skipped unless `PADDLE_SANDBOX_API_KEY` set — see `_tests/test_paddle_sandbox.py`).

## Key env vars

| Var | Default | Note |
|-----|---------|------|
| `DATABASE_URL` | `sqlite:///./data/app.db` | PostgreSQL also supported |
| `USE_DATABASE` | `1` (hardcoded) | JSON mode removed |
| `JWT_SECRET` | — | **Must set** in production |
| `CONFIG_ENCRYPTION_KEY` | — | For API key encryption at rest |
| `OPENAI_API_KEY` | — | LLM config (user also sets per-agent via UI) |
| `FREE_TIER_API_KEY` | — | Built-in LLM for free-plan users |
| `ENABLE_PYTHON_EXEC` | `0` | Danger: server-side code execution |
| `LOAD_TEST_MODE` | `0` | Disables all rate limits + email verification |
| `COOKIE_SECURE` | `0` | Set `1` on HTTPS |
| SSL certs | `localhost-key.pem`, `localhost.pem` | Auto-detected by server.py |
| **Heapdump 相关** | | (Worker + query-service 反代) |
| `HEAPDUMP_STORAGE_ROOT` | `./data/heapdumps` | NFS 共享挂载点：Worker 写 index，Web/query-service 只读 |
| `HEAPDUMP_UPLOAD_TMP` | `$TMPDIR/heapdump-chunks` | 上传分块临时目录（本地磁盘） |
| `MAT_HOME` | `/opt/mat` | MAT 发行版目录（Worker 用；含 `plugins/org.eclipse.equinox.launcher_*.jar`） |
| `MAT_PARSE_XMX` | `4g` | 解析器 JVM 堆内存（按最大 hprof 估算：`N×28.25 + C×1000` 字节 + 余量） |
| `MAT_QUERY_SERVICE_URL` | `http://127.0.0.1:8090` | query-service 内网地址（Python 反代目标） |
| `MAT_OVERVIEW_TIMEOUT` | `30` | stats 回填时调 `/overview` 的超时（秒） |
| `HEAPDUMP_WORKER_HEARTBEAT_INTERVAL` | `30` | Worker 心跳周期（秒） |
| `HEAPDUMP_WORKER_HEARTBEAT_TIMEOUT` | `300` | 看门狗判定 Worker 死亡阈值（秒） |
| `HEAPDUMP_WORKER_MAX_ATTEMPTS` | `3` | 任务最大重试次数（超上限置 FAILED） |
| `HEAPDUMP_WATCHDOG_INTERVAL` | `60` | 看门狗扫描周期（秒） |
| `HEAPDUMP_WORKER_IDLE_INTERVAL` | `5` | 无任务时的轮询间隔（秒） |
| `HEAPDUMP_WORKER_CANCEL_POLL` | `2` | 解析中探测 CANCEL_REQUESTED 的间隔（秒） |
| `HEAPDUMP_WORKER_PROGRESS_THROTTLE` | `1` | 进度写库节流阈值（秒） |
| `HEAPDUMP_WORKER_LOG_LEVEL` | `INFO` | Worker 日志级别 |
| `HEAPDUMP_PROGRESS_POLL` | `2` | 报告 SSE 进度轮询间隔（秒） |
| `HEAPDUMP_PROGRESS_MAX_DURATION` | `3600` | SSE 连接最长保持时间（秒） |

## Framework quirks

- **Windows patch**: `server.py` monkeypatches `_ProactorBasePipeTransport._call_connection_lost` to suppress `WinError 10054` on client disconnect.
- **LLM mode**: Defaults to native OpenAI function-calling. Falls back to text ReAct if the provider rejects `tools`. `LLM_USE_FUNCTION_CALLING=0` forces text path.
- **Agent** uses 40-message history window. `MAX_ITERATIONS=10` default.
- **System prompt** is built by `react_agent/prompts.py` — both Chinese and English templates.
- **No JS framework**: frontend is vanilla JS with manual DOM manipulation. CSS in `frontend/src/css/`.
- **i18n**: `frontend/i18n/` directory, switched via URL param or saved preference.
- **PostHog**: injected into HTML `<head>` via middleware in `server.py`. `posthog.js` served from `frontend/src/`.
- **Paddle checkout**: billing integration. `PADDLE_API_KEY`/`PADDLE_WEBHOOK_SECRET` from `.env`.

## Config priority

`config.json` (UI-editable) > `.env` / env vars > built-in defaults.

## File organization

- `react_agent/gc_analyzer.py` + `gc_analyzer/` — JDK GC log parsing (pure regex, no deps).
- `react_agent/jstack_analyzer.py` — jstack thread dump parser. Large dumps (30K+ threads) may hit OOM — open TODO.
- `app/services/email.py` — SMTP verification codes.
- `app/services/tracking.py` — PostHog analytics.
- `app/services/audit.py` — Audit log (DB-backed).
