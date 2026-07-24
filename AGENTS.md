# JVMind CE — AGENTS.md

> **Read [`CONVENTIONS.md`](./CONVENTIONS.md) before writing code** — coding style, patterns & anti-patterns.
> This file covers architecture, commands, testing quirks, and CE-specific gotchas.

## Entrypoints

- **Backend**: `server.py` — FastAPI app (uvicorn). `python server.py` or `python -m jvmind`.
- **Frontend**: `frontend/` — vanilla JS ES modules built with Vite. No framework.
- **Core agent**: `react_agent/graph/facade.py` — `LangGraphAgent.run_stream()`.

## Architecture

- **CE = Community Edition, single-user**. `app/core/helpers.py:_get_current_user()` hardcodes `LOCAL_USER_ID`. No real auth, CSRF, or admin checks — all return no-op.
- `app/` = FastAPI routes, middleware, state. `react_agent/` = domain logic (agent, tools, DB models, analyzers, skills).
- **DB-only**: `app/core/state.py` hardcodes `_USE_DATABASE = True`. SQLAlchemy + SQLite (default) / PostgreSQL.
- **Per-user agents**: `state._AGENTS[user_id]` dict, lazy-created via `helpers._get_agent()`.
- **Frontend**: vanilla ES modules, global state in `frontend/src/state.js`. i18n via `frontend/i18n/`.
- **API keys**: Encrypted at rest with `CONFIG_ENCRYPTION_KEY`. Stored in DB per-user.
- **Only outbound HTTP**: to user-configured LLM provider (OpenAI-compatible). No telemetry, no PostHog, no analytics.
- **No URL allowlist**: `validate_openai_base_url()` only checks scheme (http/https) and host. Internal/private LLM endpoints (e.g. `http://10.x.x.x/v1`) are accepted.

## Python environment

Uses a **local `.venv/`** (not poetry/pipenv). Always activate first:

```bash
source .venv/bin/activate
```

Deps in `requirements.txt` + `requirements-dev.txt`. Install: `pip install -r requirements.txt -r requirements-dev.txt`. Do NOT use system `python`/`pip`.

## Commands

(Python commands assume `.venv` is activated.)

```bash
# Backend dev
python server.py                   # http://127.0.0.1:8000
uvicorn server:app --reload --port 8000

# Tests (pytest)
python -m pytest _tests                       # full suite, coverage gate 63%
python -m pytest _tests -x --no-cov           # stop on first fail, skip coverage
python -m pytest _tests/test_billing.py -v --no-cov   # single file

# Frontend (separate Node toolchain under frontend/)
cd frontend && npm run dev          # Vite dev (port 3000, proxy /api → :8000)
cd frontend && npm run build        # outputs to frontend/dist/
cd frontend && npm run test         # vitest (jsdom)

# Build wheel locally (only needed for inspecting dist/; CI builds in GitHub Actions)
cd frontend && npm run build && cd ..
mkdir -p app/frontend
rm -rf app/frontend/dist
cp -r frontend/dist app/frontend/dist
mkdir -p app/frontend/dist/src
cp -r frontend/src/style.css frontend/src/css app/frontend/dist/src/
rm -rf build jvmind_ce.egg-info
python -m build --wheel
```

## Crucial: frontend build → wheel packaging

The wheel must bundle the built frontend (it's what `app/frontend.py` serves at runtime). The four `cp` lines above are mandatory — without the CSS copy, all styling breaks in production installs.

## Release flow (PyPI via Trusted Publishing)

**Do NOT `twine upload` manually** — use CI. PyPI rejects duplicate filenames with HTTP 400 (see https://pypi.org/help/#file-name-reuse), so always bump version. The workflow lives in `.github/workflows/release.yml`; it builds on Ubuntu + uploads to PyPI via OIDC (`pypa/gh-action-pypi-publish`). PyPI project has a Trusted Publisher configured for `jvmind/jvmind-ce@release.yml`. No PyPI token required locally.

```bash
# 1. Edit code, run tests, build frontend locally to spot-check
source .venv/bin/activate
cd frontend && npm run build && cd ..
cd frontend && npm test -- --run && cd ..   # 277 tests
python -m pytest _tests                     # 148 tests

# 2. Bump version in pyproject.toml
sed -i 's/version = "0.1.6"/version = "0.1.7"/' pyproject.toml

# 3. Commit, push, tag (ONE command each)
git add pyproject.toml
git commit -m "release: 0.1.7 — <short summary>"
git push origin master
git tag v0.1.7
git push origin v0.1.7
# → GitHub Actions runs release.yml → publishes to PyPI

# 4. Verify
curl -s https://pypi.org/pypi/jvmind-ce/0.1.7/json | python -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
```

Full procedure + troubleshooting: see [`docs/RELEASING.md`](./docs/RELEASING.md).

### Common release-time traps

- **Forgetting to bump version** → PyPI returns `400 File already exists`. The workflow will succeed end-to-end except for the final upload; fix by bumping + retagging.
- **Tag pointing at old commit** → fix with `git tag -d v0.x.y && git tag v0.x.y && git push origin :refs/tags/v0.x.y && git push origin v0.x.y`.
- **Frontend build skipped** → wheel has no dist; users see unstyled page. The four `cp` lines above are non-negotiable.
- **Tests pass locally but CI fails** → check `.github/workflows/tests.yml` matrix; release workflow does NOT depend on tests passing.

## LLM providers

- **Remote**: DeepSeek, OpenAI, Qwen, Kimi — any OpenAI-compatible endpoint. API key required.
- **Local Ollama**: `http://localhost:11434/v1`, no API key needed. All data stays local.
- UI preset (Settings → "Ollama · 本地") or `.env`: `OPENAI_BASE_URL=http://localhost:11434/v1` with empty `OPENAI_API_KEY`.
- `validate_openai_base_url()` only checks scheme (http/https); no allowlist or private-IP block. Any OpenAI-compatible URL (including 10.x/192.168.x) works.

## Testing quirks

- `conftest.py` sets env vars **before** project imports (temp SQLite, isolated dirs, rate limits at 10000).
- `db_clean` fixture truncates all tables + clears in-memory state. Use via `auth_client` / `admin_client`.
- `auth_client` auto-sets `X-CSRF-Token`. `admin_client` flips `is_admin=1` via direct DB write.
- `make_fake_agent(user_id, reply)` stubs `run_stream()` to return fake SSE events.
- `fake_paddle` replaces `state._PADDLE` with a capture-only fake.
- `fake_email` captures verification codes into a list instead of sending SMTP.
- Tests stub the per-user agent via the `make_fake_agent(user_id, reply)` fixture in `_tests/conftest.py`; it replaces `state._AGENTS[user_id]` with a `FakeAgent` whose `run_stream()` yields canned SSE events.
- **No `pytest-xdist`** (single-process — uvicorn shares global state + sqlite file).
- Coverage gate: 63% (`--cov-fail-under=63` in `pytest.ini`).
- Markers: `db` (SQLite), `smoke_llm` (hits real LLM, skipped by default), `paddle_sandbox` (hits Paddle sandbox, skipped unless `PADDLE_SANDBOX_API_KEY` set).
- `validate_openai_base_url` does no DNS/IP check; only validates scheme. Tests may still patch it on `app.routes.config` to bypass the openai client call.

## Key env vars

| Var | Default | Note |
|-----|---------|------|
| `DATABASE_URL` | `sqlite:///./data/app.db` | PostgreSQL also supported |
| `CONFIG_ENCRYPTION_KEY` | — | Encrypts API keys at rest. **Set in production** |
| `JWT_SECRET` | — | Only used if multi-user mode re-enabled |
| `FREE_TIER_API_KEY` | — | Built-in LLM for users who don't BYOK |
| `ENABLE_PYTHON_EXEC` | `0` | Danger: server-side code execution by agent |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |
| `COOKIE_SECURE` | `0` | Set `1` on HTTPS |
| `MAT_HOME` | `/opt/mat` | Eclipse MAT directory (heapdump analysis only) |

## Framework quirks

- **Windows patch**: `server.py` monkeypatches `_ProactorBasePipeTransport._call_connection_lost` for `WinError 10054` on client disconnect.
- **LLM mode**: Native OpenAI function-calling only. If the provider rejects the `tools` parameter, the agent surfaces a clear error rather than silently degrading.
- **Agent**: `LangGraphAgent` in `react_agent/graph/`. 40-message history window. `MAX_ITERATIONS=10` default.
- **System prompt**: `react_agent/prompts.py` — both Chinese and English templates.
- **Frontend CSS**: Served separately (not Vite-bundled). Entry at `/src/style.css` which `@import`s files from `src/css/`.
- **i18n**: `frontend/i18n/{zh,en}.json`. Frontend uses `t("key")` and `th("key")` helpers.

## Config priority

`config.json` (UI-editable) > `.env` / env vars > built-in defaults.

## File organization

- `server.py` — FastAPI entry, lifespan, exception handlers, CORS, security headers.
- `app/routes/` — API endpoints (chat, config, sessions, gc/jstack/heapdump reports, auth, skills, billing).
- `app/frontend.py` — Serves built frontend dist, mounts `/src`, `/assets`, `/lib`, `/image`.
- `react_agent/graph/` — LangGraph agent (default). `facade.py` = public API, `llm.py` = ChatOpenAI wrapper.
- `react_agent/gc_analyzer/` — GC log parsers (pure regex, multi-JDK).
- `react_agent/jstack_analyzer.py` — jstack thread dump parser.
- `react_agent/mat_tools.py` — Heapdump tool wrappers (calls local MAT query-service).
- `react_agent/heapdump_worker/` — Background MAT parser (separate process).
- `react_agent/memory_db.py` — SQLAlchemy-backed session/report storage.
- `react_agent/config.py` — LLMConfig dataclass, encryption/decryption, base URL validation.
- `frontend/` — Vanilla JS + Vite. Source in `src/`, build output in `dist/`.
- `vendor/mat/` — Bundled query-service jar for heapdump analysis.
- `_tests/` — pytest suite.
