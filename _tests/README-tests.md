# JVMind Test Suite

This directory holds the pytest suite for the JVMind backend (FastAPI app
under `server.py` + `app/` + `react_agent/`). It targets the **current**
multi-tenant SaaS architecture (per-user agent registry, JWT cookie auth,
plan-gated config endpoints, GC/jstack analyzers, SSE streaming).

## Quick run

```bash
# Full suite (~90s on a dev laptop, sqlite-backed, with coverage)
python -m pytest _tests

# A single file with verbose output, no coverage gate
python -m pytest _tests/test_billing.py -v --no-cov

# Stop on first failure
python -m pytest _tests -x

# HTML coverage report
python -m pytest _tests --cov-report=html
# → opens at htmlcov/index.html
```

## Coverage

`pytest.ini` enforces `--cov-fail-under=53` against `app/` + `react_agent/`.
Current baseline (127 tests, sqlite-backed):

| Layer | Coverage |
|---|---|
| **TOTAL** | **64.0 %** |
| `app/routes/billing.py` | ~85 % |
| `app/routes/config.py` | 82 % |
| `app/routes/gc_reports.py` | 79 % |
| `app/routes/jstack_reports.py` | 78 % |
| `app/routes/auth.py` | 60 % |
| `app/routes/orgs.py` | ~63 % (create/invite/join/role/seats/delete-member/update-name fully covered) |
| `app/routes/admin.py` | ~68 % (stats/plans/users/billing/audit/user-management fully covered) |
| `react_agent/gc_analyzer.py` | 79 % |
| `react_agent/jstack_analyzer.py` | 74 % |
| `react_agent/graph/facade.py` | ~50 % (SSE covered via `make_fake_agent`; live path via LangGraph graph) |
| `react_agent/user_manager.py` | 0 % (legacy JSON path; replaced by `user_manager_db`) |

Raise the gate (`pytest.ini --cov-fail-under=`) as more areas come
under test. Targeted next: `billing.py` webhook handling, `billing.py` public plan list.

`coverage.xml` is written at repo root for CI consumption (Codecov,
Cobertura, etc.). It's gitignored.

The tests spawn an in-process **uvicorn** server on a free port (port 0)
and drive it with `httpx.Client`, so they cover real ASGI middleware
(CSRF, rate-limit, auth) end-to-end.

## What gets isolated per run

`conftest.py` sets the following BEFORE any project module is imported:

| Env var | Value | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///<tmp>/test.db` | Per-run sqlite file, isolated from dev DB |
| `SESSION_DIR` | `<tmp>/sessions` | Memory store, isolated |
| `UPLOAD_DIR` | `<tmp>/uploads` | Uploaded GC/jstack files, isolated |
| `JWT_SECRET` | fixed test value | Stable cookie signing |
| `CONFIG_ENCRYPTION_KEY` | fixed 32-byte | API-key encryption at rest |
| `FREE_TIER_API_KEY` | `test-builtin-key` | Lets free-plan users hit chat without real LLM |
| `FREE_TIER_BASE_URL` | `https://fake-llm.local/v1` | Never resolved (LLM is stubbed) |
| `OPENAI_API_KEY` | empty | Force tests to go through user_manager / plans |
| Rate-limit caps | `10000` | Never trip during a normal run |

The `db_clean` fixture truncates every table between tests, clears
`app.core.state` in-memory dicts, disposes the SQLAlchemy engine pool
(important — leaked sessions from streaming tests would otherwise
exhaust `QueuePool limit of 20+10`), then re-runs `init_db()` to
re-seed the default plans (`free` / `pro` / `team`).

## Fixtures (defined in `_tests/conftest.py`)

- `client` — anonymous `httpx.Client` against the live uvicorn server.
- `auth_client` — `(client, user_dict)` already registered + logged in,
  with `X-CSRF-Token` header pre-populated from the cookie.
- `admin_client` — same shape, but `is_admin=1` is flipped via direct
  DB write.
- `db_clean` — table truncation + state reset (auto-applied by
  `auth_client` / `admin_client`).
- `fake_paddle` — replaces `state._PADDLE` with `_FakePaddle` so
  billing tests assert on captured calls instead of hitting Paddle.
- `fake_email` — captures verification emails into a list.
- `make_fake_agent(user_id, reply)` — drops a `FakeAgent` into
  `state._AGENTS[user_id]`. The agent has a real DB-backed memory but
  a stubbed `run_stream()` that yields `{user, token, final, done}`.
- `asgi_client` — async `httpx.AsyncClient` over `ASGITransport`
  (used where we don't need the live server).
- `sse_parser` — convenience wrapper around `parse_sse(text)`.

## Files

| File | Tests | What it covers |
|---|---|---|
| `test_auth.py` | 13 | register / login / logout / verify-email / password rules / lockouts |
| `test_billing.py` | 16 | `_FakePaddle` checkout / upgrade / cancel / webhook signature / plan rollover |
| `test_config.py` | 12 | `/api/config` GET/PUT/POST/DELETE; plan gating (free=403, pro=200); `validate_openai_base_url` is monkey-patched on **both** `app.routes.config` and `react_agent.user_manager_db` |
| `test_gc_api.py` | 10 | `/api/gc/*` upload / list / get / delete; rejects non-GC; auth required |
| `test_gc_analyzer.py` | (legacy) | Pure-function unit tests on `gc_analyzer` |
| `test_gc_matrix.py` | (legacy) | Multi-collector / multi-JDK regression matrix |
| `test_jstack_api.py` | 21 | `/api/jstack/*` upload / list / sample / analyze SSE; LangGraph path stubbed via `make_fake_agent` |
| `test_memory_isolation.py` | 4 | Per-user `MemoryImpl` directories don't bleed |
| `test_orgs_basic.py` | 9 | Org create / list / member roles |
| `test_plans_public.py` | 4 | `GET /api/plans` shape, ordering, default-plan flag |
| `test_report_page.py` | 8 | SPA shell routes (`/`, `/app`, `/pricing`, `/report/...`, `/jstack-report/...`, `/robots.txt`) and GC report `/series` & `/summary` JSON contracts |
| `test_session_isolation.py` | 4 | Sessions filtered by user_id and org_id; anonymous can't see logged-in sessions |
| `test_session_recovery.py` | 3 | Recovers cleanly after `InvalidRequestError: Instance not bound to a Session` |
| `test_stream.py` | 5 | `/api/chat/stream` SSE happy path + auth-required + CSRF |

Total: **120 tests**, currently green.

## Conventions when adding tests

1. **Always** depend on `db_clean` (directly or via `auth_client` /
   `admin_client`) — otherwise rows from a previous test leak in.
2. Don't mutate `state._AGENTS` directly outside the fixtures; use
   `make_fake_agent(user_id)`.
3. To stub the LLM during chat tests, use the `make_fake_agent`
   fixture (see `_tests/conftest.py`). It drops a `FakeAgent` into
   `state._AGENTS[user_id]` whose `run_stream()` yields the SSE events
   you want — no monkeypatching needed.
4. For `validate_openai_base_url` (does a real DNS lookup), patch
   **both** locations — the route imports it directly, but the
   user-manager update path calls it again under the hood:
   ```python
   monkeypatch.setattr("app.routes.config.validate_openai_base_url",
                       lambda *_a, **_k: True)
   monkeypatch.setattr("react_agent.user_manager_db.validate_openai_base_url",
                       lambda *_a, **_k: True)
   ```
5. Plan-gated routes (`/api/config` write paths) require `pro` or
   `team`. Use the `_promote_to_pro(user_id)` helper in
   `test_config.py` (direct `UserModel.plan = "pro"` write) — there
   is no public upgrade-without-checkout endpoint.
6. For SSE responses, use `sse_parser(resp.text)` to get a list of
   payload dicts; bytes-mode streams use `_parse_sse_bytes(chunks)`.
7. CSRF: `auth_client` already sets the header. If you log out and
   re-login inside one test, refresh it from
   `client.cookies.get("csrf_token")`.

## Known caveats

- The DB pool size is `pool_size=20, max_overflow=10`. The fixture
  calls `engine.dispose()` between tests to recycle connections;
  removing that will reintroduce `QueuePool limit reached` once the
  suite is large enough.
- `sample_gc.log` and friends are real log fixtures; do not delete
  them — `test_gc_api.py`, `test_gc_analyzer.py`, and
  `test_gc_matrix.py` all read them at runtime.
- Tests are designed to run as a single process. Parallel runners
  (`pytest-xdist`) are **not** supported because uvicorn instances
  share `state._AGENTS` and the same sqlite file.
