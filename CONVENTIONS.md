# CONVENTIONS.md — 编码约定速查

> **编码前必读**。本文件聚焦「代码风格 + 模式范例 + 反模式」，与 `AGENTS.md`（架构/命令/测试运维）配合使用。
> 实证自现有代码库，遵循即可与项目风格对齐，减少返工与回归。

---

## 0. 编码前 30 秒检查清单

- [ ] **后端非 GET 接口** → CSRF 由中间件自动校验；测试请求需带 `X-CSRF-Token` 头
- [ ] **改 ORM 字段** → 时间字段一律 `Column(Text)`（**非** `DateTime`，现状约定）；加字段安全，删/改类型不安全
- [ ] **用户数据操作** → 走 `UserManager` 方法，**勿**直接改 ORM 的 `password_hash` 等
- [ ] **前端 `innerHTML`** 注入用户/服务端数据 → **必须** `escapeHtml()`
- [ ] **前端事件绑定** → `addEventListener` / 事件委托 + `data-*`，**禁止**模板内联 `onclick`
- [ ] **用户可见文案** → 后端错误用 `"中文 / English"` 双语；前端用 `t("key")` + i18n
- [ ] **捕获异常** → 至少 `_logger.warning/exception(...)`，**禁止**裸 `except: pass`
- [ ] **写操作** → 调用 `log_audit(request, "action.verb", ...)`

---

## 1. 后端 Python（`app/`, `react_agent/`）

### 1.1 路由文件结构（实证 `app/routes/*.py`）

```python
from __future__ import annotations          # 必须，首行

import json                                  # 标准库
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends  # 第三方
from sqlalchemy.orm import Session

from app.core import helpers, state          # 本项目：app.*
from app.schemas import XxxReq
from app.services.audit import log_audit
from react_agent.db import get_db            # 本项目：react_agent.*

router = APIRouter(prefix="/api/xxx", tags=["xxx"])
```

- 新路由需在 `server.py` 用 `app.include_router(xxx_router)` 注册。

### 1.2 鉴权与依赖

| 场景 | 写法 |
|------|------|
| 取当前用户 | `user_id = helpers._get_current_user(request)` |
| 要求管理员 | `user_id = helpers._require_admin(request)` |
| 校验会话归属 | `helpers._check_session_owner(sid, user_id)` |
| 校验组织成员 | `helpers._check_org_member(org_id, user_id)` |
| DB 会话（路由） | `db: Session = Depends(get_db)` |
| DB 会话（非路由） | `with session_scope() as db:` |
| 取用户管理器 | `um = helpers._ensure_user_manager()` |
| 取用户套餐 | `plan_info = helpers._get_user_plan(user_id)` |

### 1.3 LLM 配额（易错点）

- 聊天/分析路径用 **`_try_consume_llm_call(user_id)`**（原子：检查+消耗）
- 仅检查用 `_can_make_llm_call(user_id)`
- 测试 mock 时**两者都要 patch**（否则 cooldown 会在第 2 次调用触发）

### 1.4 异常处理

```python
raise HTTPException(400, "请输入有效邮箱 / Please enter a valid email")   # 双语

# 防御性捕获必须记日志，禁止裸 pass
import logging
_logger = logging.getLogger(__name__)
try:
    ...
except Exception:
    _logger.exception("Failed to ...: %s", ctx)   # 或 _logger.warning(...)
```

### 1.5 DB 会话收尾范式

```python
db = SessionLocal()
try:
    ...
    db.commit()
finally:
    try:
        db.rollback()
    except Exception:
        pass
    db.close()
```

### 1.6 审计日志

```python
log_audit(request, "session.create", user_id=user_id, org_id=org_id,
          resource=f"session:{sid}", details={"title": title})
```
- action 命名：`<域>.<动作>[.<结果>]`，如 `auth.login.success`、`report.gc.upload`
- 敏感字段（password/token/api_key/content...）自动脱敏，见 `audit.py:_SENSITIVE_KEYS`

### 1.7 命名约定

- 私有辅助函数：`_snake_case`（如 `_get_client_ip`、`_validate_email`）
- ID 生成：`"<prefix>_" + uuid.uuid4().hex[:N]`，前缀如 `user_` `evc_` `prc_` `fid_` `rid_` `plan_`
- 时间字符串：`time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())`（`helpers._now_str()`）

### 1.8 输入验证

- 文件上传：文件名长度 ≤255、路径遍历防护（`fname.replace("\\","/").split("/")[-1]`）、扩展名白名单（`state._ALLOWED_*_EXTS`）
- 文本字段：会话标题 ≤200、fact ≤1000；空内容拦截
- **空文件不要短路返回 400** → 让其落到解析器的 422「未能解析」路径（既有契约）

---

## 2. 数据库 / 模型（`react_agent/models.py`）

```python
class XxxModel(Base):
    __tablename__ = "xxx"
    __table_args__ = (
        Index("ix_xxx_user_id", "user_id"),
        UniqueConstraint("a", "b", name="uq_xxx_ab"),
    )
    id = Column(Text, primary_key=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    created_at = Column(Text, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
```

- **时间字段统一 `Column(Text)`**（项目现状，非 DateTime）
- 索引/唯一约束写 `__table_args__`，启动时 `_auto_migrate()` 自动补齐（**仅追加**：加列/加索引；不删列、不改类型）
- 加列安全；删列、改类型、删约束**不安全**

---

## 3. ReAct Agent（`react_agent/graph/facade.py`, `tools.py`）

- 单一执行路径：原生 OpenAI function-calling via LangGraph；provider 拒绝 `tools` 时直接报错
- 新增工具：在 `default_tools()` 中 `reg.register(Tool(...))`

```python
Tool(
    name="analyze_gc_log",
    description="Analyze GC log... / 分析 GC 日志...",   # 双语
    func=gc_tool,
    args_hint="file_id",
    parameters={                                          # OpenAI function-calling JSON Schema
        "type": "object",
        "properties": {"file_id": {"type": "string", "description": "... / ..."}},
        "required": [],
    },
)
```

- Memory 接口（`DatabaseMemory`）常用方法：`append_message` / `get_messages` / `clear_messages` / `add_fact` / `get_facts` / `get_prompt_facts` / `set_context_fact` / `create_session` / `load` / `add_gc_report` / `get_gc_report` / `list_all_reports` / `add_jstack_report`...

---

## 4. 前端 Vanilla JS（`frontend/src/`）

### 4.1 模块模式

- 功能模块结尾用 `Object.assign(app, { fn1, fn2, ... })` 把函数挂到全局 `app`（`app.js`）
- 全局状态集中在 `state.js`（`import { state } from "./state.js"`）
- import 顺序惯例：`state` → `app` → `shared` → `api` → `i18n` → 其它

### 4.2 API 调用（统一入口）

```js
import { api } from "./api.js";
const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({...}) });
// api() 自动注入 CSRF 头、处理 401（触发已注册的 showLoginUI）、双语错误解析
```
- **勿**再造 `apiWithAuth`/`csrfHeaders`/`escapeHtml` 副本——复用 `api.js` / `shared.js`

### 4.3 安全铁律（XSS / CSP）

```js
// ✅ 用户/服务端数据进 innerHTML 必须转义
el.innerHTML = `<div>${escapeHtml(report.filename)}</div>`;

// ✅ 事件用 data-* + 委托（挂在不会被 innerHTML 重置的稳定父容器）
container.innerHTML = `<button data-act="send" data-id="${escapeHtml(id)}">...</button>`;
container.onclick = (ev) => {
  const btn = ev.target.closest('[data-act="send"]');
  if (btn) sendToAgent(btn.dataset.id);
};

// ❌ 禁止：内联 onclick（转义在事件属性内无效，且阻碍 CSP）
// `<button onclick="sendToAgent('${filename}')">`
```

- Markdown 渲染走 `renderMarkdown()`（内部 DOMPurify 消毒）
- 图表 resize 用 `attachResize(el, chart)`（`charts.js`），避免监听器泄漏
- document 级监听需幂等守卫（模块级 flag），避免重复注册

### 4.4 国际化（i18n）

```js
import { t } from "../i18n/index.js";
el.textContent = t("sidebar.quota_title", { plan: "免费版" });   // 变量用 {name}
```
- 新文案需**同时**加到 `frontend/i18n/zh.json` 和 `en.json`（缺 key 回退英文，再回退 key 名）
- HTML 静态文案用 `data-i18n="key"`，由 `applyI18n()` 渲染
- 后端双语消息 `"中文 / English"` 在前端用 `i18nText()` 按当前语言切分

---

## 5. 测试

### 5.1 后端（pytest，`_tests/`）

- fixture：`auth_client`（已登录，自动 CSRF）、`admin_client`（管理员）、`db_clean`（清表+重置状态）
- `make_fake_agent` stub `run_stream`；`fake_paddle` / `fake_email` 替换外部服务
- mock 配额：`_bypass_quota` 须**同时** patch `_can_make_llm_call` + `_try_consume_llm_call`
- `conftest.py` 在 import 项目前设置 env（含强制清空 SMTP，保证 invite 返回 `link`）
- **邮件安全**：autouse fixture `_block_real_smtp` 全局拦截 `email._send_message`，**任何测试都绝不会真实发信**（防止给虚构地址 `*@example.com` 发信导致 `support@jvmind.io` 退信）。若测试 patch `is_email_enabled=True`（如 `fake_email`），必须 stub **所有** `send_*` 函数（验证码/重置/邀请），否则会落到真实传输层
- 仅需 invite `link` 的测试**不要**用 `fake_email`（它强制 email 启用 → 返回 `email_sent:True` 而无 `link`）
- 命令：`python -m pytest _tests`（覆盖率门槛 63%，见 `pytest.ini`）；`-x --no-cov` 快速调试

### 5.2 前端（vitest + jsdom）

- 模块顶层会访问 DOM → 测试**前**先 `document.body.innerHTML = "..."` 准备元素
- 测 `<tr>` 渲染需真实 `<table><tbody id="...">`（jsdom 会丢弃裸 div 内的 `<tr>`）
- 需要 `marked`/`echarts`/`DOMPurify` 时用 `globalThis.xxx = {...}` 提供最小桩
- 命令：`cd frontend && npm run test`

---

## 6. 反模式清单（避免回归，实证自历史审查）

| ❌ 反模式 | ✅ 正确做法 |
|----------|-----------|
| `except Exception: pass` | `except Exception: _logger.warning/exception(...)` |
| 前端模板内联 `onclick="fn('${data}')"` | `data-*` 属性 + 事件委托 |
| `innerHTML` 注入未转义数据 | 先 `escapeHtml()` |
| 路由直接改 `user.password_hash` | `um.update_password(uid, pw)` |
| 重复实现 `apiWithAuth`/`escapeHtml` | 复用 `api.js` / `shared.js` |
| 硬编码用户可见文案 | `t("key")` + i18n json |
| 空文件上传短路 400 | 落到解析器 422「未能解析」 |
| fire-and-forget async 无 `.catch` | `Promise.resolve(fn()).catch(console.warn)` |
| 时间字段用 `DateTime` | 沿用 `Column(Text)`（现状约定） |
| 测试只 patch `_can_make_llm_call` | 同时 patch `_try_consume_llm_call` |
| 测试 `is_email_enabled=True` 但漏 stub 某个 `send_*` | stub 全部 `send_*`（验证码/重置/邀请），靠 `_block_real_smtp` 兜底 |

---

## 7. 命令速查

```bash
# 后端
python server.py                          # http://127.0.0.1:8000
python -m pytest _tests                    # 全量 + 覆盖率（门槛 63%）
python -m pytest _tests/test_xxx.py -x --no-cov   # 快速调试

# 前端
cd frontend && npm run dev                 # Vite dev (3000 → proxy 8000)
cd frontend && npm run build               # 输出 dist/
cd frontend && npm run test                # vitest
```

> 更多架构、env vars、测试 quirks 见 **`AGENTS.md`**。
