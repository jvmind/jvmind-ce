# JVMind Community Edition

> An AI-powered JVM diagnostics agent. **Single-user · local-first · open source.**
>
> 一个 AI 驱动的 JVM 诊断智能体。**单用户 · 本地优先 · 开源。**
>
> [中文文档 (Chinese)](./README.zh-CN.md) · [Report a bug](https://github.com/jvmind/jvmind-ce/issues/new?template=bug.md) · [Request a feature](https://github.com/jvmind/jvmind-ce/issues/new?template=feature.md)

JVMind CE is a JVM performance diagnostics assistant built on OpenAI-compatible LLMs. It runs the agent as a local web service, accepts uploaded logs/dumps, and streams diagnostic conclusions back over SSE.

- 🧠 **LangGraph agent** — Tool orchestration via LangGraph state machines (native OpenAI function-calling).
- 🪵 **GC log analysis** — JDK 8 / 11 / 17 / 21 / 25 collectors (G1, Parallel, ZGC, Shenandoah, Serial, CMS). Pure regex parsers, no external runtime deps.
- 🧵 **jstack thread analysis** — Deadlock detection, lock-contention hotspots, thread-pool distribution, flame graph, per-thread drill-down.
- 💾 **Heapdump analysis (optional)** — Parses GB-sized hprof files via Eclipse MAT. Ships with the bundled query-service plugin.
- 🔌 **OpenAI-compatible LLM** — DeepSeek, OpenAI, Qwen, Kimi, and any compatible endpoint. Hot-reloadable UI config.
- ⚡ **Real SSE streaming** — Tool calls, reasoning, and final answers push incrementally to the browser.

The community edition ships **without** billing, team management, or rate limits. For those, use a commercial offering.

---

## Quick Start

### Install from PyPI

```bash
pip install jvmind-ce
jvmind
# open http://127.0.0.1:8000
```

### Install from source (developer mode)

```bash
git clone https://github.com/jvmind/jvmind-ce.git
cd jvmind-ce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
jvmind
```

### Configure the LLM

The UI prompts for an API key on first launch. To pre-configure via `.env`:

```bash
cp .env.example .env
# Edit .env:
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

The agent uses native OpenAI function-calling, so any model supporting `tools` works. DeepSeek's `deepseek-chat` is a sensible default.

---

## Features

### GC log analysis

Open the **📊 GC 分析** tab, upload a log file. The pipeline:

1. Stream-upload → store raw text in DB
2. Parser (pure regex) extracts events, computes statistics, builds overview
3. Render: 8 status cards + per-collector table + heap chart + pause histogram + top-10 slowest
4. Trigger a streamed LLM diagnosis with the structured template (overall health, key issues, parameter tuning, follow-up metrics)

Supported collectors: **G1** · **Parallel** · **Serial** · **ZGC** · **Shenandoah** · **CMS**. JDK 9+ unified log format and JDK 8 PrintGCDetails are both supported.

### jstack thread analysis

Open the **🧵 线程分析** tab, upload a `jstack -l` dump. Features:

- Thread state histogram (RUNNABLE / BLOCKED / WAITING / TIMED_WAITING)
- Deadlock detection with lock chain visualization
- Lock contention hotspots (holder + waiters list, click-to-drill)
- Thread pool distribution, flame graph, single-thread drill-down
- Streamed LLM diagnosis (whole dump or single thread)

### Heapdump analysis (optional — requires Eclipse MAT)

The **🔍 Heapdump 分析** tab uploads GB-sized `.hprof` files. The architecture:

```
  Browser ─▶ FastAPI (upload routes) ─▶ local disk: <dump_dir>/<report_id>/
                                              │
                                              ▼
                            worker_loop ─▶ ParseHeapDump.sh
                                              │
                                              ▼
                          ┌───── query-service (HTTP) ◀─────┐
                          │   bundled in vendor/mat/         │
                          └──────────────────────────────────┘
                                              │
                                              ▼
  Browser ◀────── SSE progress / JSON query results ◀───── FastAPI proxy
```

**Install MAT and the query-service in one step:**

```bash
./scripts/install_mat.sh /opt/mat
# or: MAT_HOME=/opt/mat ./scripts/install_mat.sh
```

This downloads Eclipse MAT, extracts it, and copies the bundled `com.jvmind.mat.query-0.1.0.jar` into the MAT plugins directory. Configure and run:

```bash
# .env
MAT_HOME=/opt/mat
MAT_QUERY_SERVICE_URL=http://127.0.0.1:8090
```

```bash
# Terminal 1: query-service
/opt/mat/MemoryAnalyzer -consoleLog -nosplash \
    -application com.jvmind.mat.query.QueryServiceApp

# Terminal 2: heapdump worker (separate process)
jvmind-worker
```

The bundled query-service jar ships inside `vendor/mat/`, so users don't need to compile Java themselves.

### Agent internals

The agent is a **LangGraph** state machine in `react_agent/graph/`:

- `facade.LangGraphAgent` — public API, matches the legacy agent's interface
- `graph_builder.build_graph` — wires nodes (Agent → Tools → Finalize)
- `nodes` — tool execution + structured reasoning (cross-domain diagnosis for OOM)
- `sse_adapter` — yields `user` / `token` / `step` / `fact_added` / `final` / `error` / `done` events
- `llm_compat` / `parsing_compat` — shared mixins for tool-call error detection

Native OpenAI function-calling is the default. If a provider rejects the `tools` parameter, the agent surfaces a clear error rather than silently degrading.

---

## Configuration

See [`.env.example`](./.env.example) for the full list. Key knobs:

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy URL; switch to PostgreSQL for prod |
| `OPENAI_API_KEY` | — | Required; user-level BYOK config also supported |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | Any OpenAI-compatible endpoint |
| `OPENAI_MODEL` | `deepseek-chat` | Model name |
| `OPENAI_TIMEOUT_SECONDS` | `60` | Per-LLM-call timeout |
| `CONFIG_ENCRYPTION_KEY` | — | Encrypts persisted API keys at rest |
| `HEAPDUMP_*` | see `.env.example` | Only relevant if using heapdump analysis |

The web UI also has a Settings (⚙️) dialog for LLM config — changes hot-reload the agent and re-encrypt stored keys.

---

## Development

```bash
# Run dev server with auto-reload
uvicorn server:app --reload --port 8000

# Run all tests
python -m pytest _tests --no-cov

# Frontend dev (Vite hot-reload)
cd frontend && npm install && npm run dev

# Frontend production build
cd frontend && npm run build

# Heapdump worker (separate process)
jvmind-worker
```

Project layout:

```
jvmind-ce/
├── server.py              # FastAPI entry point
├── app/                   # routes, middleware, helpers
├── react_agent/
│   ├── graph/             # LangGraph agent (default)
│   ├── gc_analyzer/       # GC log parsers (JDK 8 + JDK 9+)
│   ├── jstack_analyzer.py
│   ├── mat_tools.py       # Heapdump tool wrappers
│   ├── heapdump_worker/   # Background MAT parser
│   ├── memory_db.py
│   ├── user_manager_db.py # single-user
│   └── db.py / models.py
├── frontend/              # vanilla JS + Vite
├── vendor/mat/            # bundled query-service jar
├── scripts/install_mat.sh # one-step MAT install
└── _tests/                # pytest suite (146 tests)
```

## License

MIT — see [LICENSE](./LICENSE).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [CONVENTIONS.md](./CONVENTIONS.md).

## Credits

JVMind CE is extracted from the commercial JVMind product. The commercial edition includes multi-user auth, team workspaces, Paddle billing, PostHog analytics, and other features that this repository deliberately omits.