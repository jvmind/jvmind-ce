# JVMind Community Edition

> 基于 Python + OpenAI 兼容接口的 JVM 性能诊断 ReAct Agent。社区版：单用户、本地优先、开箱即用。

JVMind CE 是一个面向 JVM 工程师的 AI 诊断助手，支持：

- 🧠 **ReAct + LangGraph 双引擎** — 同一套工具可在两种推理循环间切换
- 🪵 **GC 日志分析** — JDK 8 / 11 / 17 / 21 / 25 各代收集器（G1 / Parallel / ZGC / Shenandoah / Serial / CMS），纯正则解析，零外部依赖
- 🧵 **jstack 线程栈分析** — 死锁检测、锁竞争热点、线程池分布、火焰图、单线程钻取
- 💾 **多会话 + 长期记忆** — 每个会话独立的对话历史与 facts
- ⚙️ **可视化 LLM 配置** — OpenAI / DeepSeek / 通义 / Kimi 等 OpenAI 兼容接口，热重载
- 💾 **Heapdump 分析（可选）** — 通过 Eclipse MAT 解析 GB 级 hprof（需额外配置）
- ⚡ **真正的 SSE 流式输出** — 思考过程、工具调用、最终回答、诊断全部实时推送

---

## 快速开始

### 安装（PyPI）

```bash
pip install jvmind-ce
jvmind
# 浏览器打开 http://127.0.0.1:8000
```

### 从源码安装（开发者模式）

```bash
git clone https://github.com/jvmind/jvmind-ce.git
cd jvmind-ce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
jvmind
```

### 配置 LLM

启动后通过界面 ⚙️ 配置 LLM（推荐），或在 `.env` 中预设：

```bash
# .env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

---

## 功能特性

### 单用户 / 无认证 / 无配额

社区版默认所有请求都以本地用户 `user_local` 运行，启动即用。无需注册、登录、邮箱验证，没有 LLM 调用配额限制。

如果需要多人协作、付费墙、团队管理等商业功能，请使用商业版（JVMind SaaS）。

### GC 日志分析

点击界面 **📊 GC 分析** 标签页，上传日志：

- 自动识别 JDK 8 与 JDK 9+ 统一日志格式
- 8 张状态卡 + 类型统计表 + SVG 堆变化曲线 + 停顿分布柱状图
- 按收集器类型（G1 / ZGC / Parallel / 等）分别给出诊断建议
- 流式 LLM 诊断（按结构化模板：整体健康度 / 关键问题 / 参数调优 / 后续观察）

### jstack 线程栈分析

**🧵 线程分析** 标签页：

- 标准 `jstack -l` 输出解析
- 死锁自动检测与锁链展示
- 锁竞争热点（持有者 + 等待者列表）
- 线程池分布、火焰图、单线程钻取
- 流式 LLM 诊断（整体 + 单线程）

### Heapdump 分析（可选，需 MAT）

上传 GB 级 hprof 后通过 **🔍 Heapdump 分析** 标签页查看：

- 自动分配解析任务给后台 worker
- 实时进度（SSE）
- 概览、Top 类、连接池、线程、Environment 等多维交互查询
- AI 诊断结论

**部署 MAT**（如需使用）：

1. 安装 [Eclipse Memory Analyzer](https://www.eclipse.org/mat/)
2. 在 `.env` 中配置 `MAT_HOME=/path/to/mat`
3. 启动 worker：`jvmind-worker`

> MAT 是 JVM 工具，需要单独下载。社区版不强制依赖。

### ReAct / LangGraph Agent

默认使用经典 ReAct 循环（每轮输出 Thought + Action + Observation）。如需试用 LangGraph 实现：

```bash
# .env
USE_LANGGRAPH_AGENT=1
```

LangGraph 引擎支持更复杂的工具编排（并行工具、结构化输出、跨域诊断）。

---

## 开发

### 命令

```bash
# 启动开发服务器（uvicorn --reload）
uvicorn server:app --reload --port 8000

# 运行所有测试（不含覆盖率门禁）
python -m pytest _tests --no-cov

# 运行单个测试
python -m pytest _tests/test_gc_analyzer.py -v --no-cov

# 前端开发模式（Vite hot-reload）
cd frontend && npm install && npm run dev

# 前端构建
cd frontend && npm run build
```

### 项目结构

```
jvmind-ce/
├── server.py              # ⭐ FastAPI 后端入口
├── app/                   # FastAPI 路由、中间件、状态
│   ├── routes/            # chat/sessions/config/gc/jstack/heapdump/...
│   ├── core/              # helpers, state
│   └── services/          # audit
├── react_agent/           # 领域逻辑
│   ├── agent.py           # 经典 ReAct 循环
│   ├── graph/             # LangGraph 实现
│   ├── gc_analyzer/       # GC 日志解析器（多 JDK 适配）
│   ├── jstack_analyzer.py # jstack 解析器
│   ├── mat_tools.py       # Heapdump 工具（MAT 查询服务封装）
│   ├── heapdump_worker/   # Heapdump 解析后台 worker
│   ├── memory_db.py       # 数据库版会话/记忆/报告存储
│   ├── user_manager_db.py # 单用户版 user_manager
│   └── db.py / models.py  # SQLAlchemy 引擎与 ORM 模型
├── frontend/              # vanilla JS 前端（Vite 构建）
└── _tests/                # pytest 套件（188 个测试）
```

### 协议

```
MIT License — see LICENSE
```

### 贡献

参见 [`CONTRIBUTING.md`](./CONTRIBUTING.md) 与 [`CONVENTIONS.md`](./CONVENTIONS.md)。

提交 issue 或 PR 请走 GitHub 流程。

### 致谢

JVMind CE 基于商业版 JVMind 抽取而来。商业版包含多用户、团队协作、付费（Paddle）、埋点分析（PostHog）等能力，本仓库仅保留单用户本地优先的精简版。