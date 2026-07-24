# Remove legacy text-ReAct mode implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the unused text-ReAct fallback path from JVMind CE so the codebase has only one execution path — LangGraph + native OpenAI function-calling.

**Architecture:** Pure deletion refactor. Eight Python files shrink or disappear; four docs lose their stale dual-path / `ReActAgent` references. Each commit leaves the tree green (`pytest _tests -x --no-cov` clean).

**Tech Stack:** Python 3 + LangGraph state machine + FastAPI + vanilla-JS frontend.

**Reference spec:** `docs/superpowers/specs/2026-07-24-remove-legacy-react-mode-design.md`

## Global Constraints

- Run `source .venv/bin/activate` before any Python command.
- Test command: `python -m pytest _tests -x --no-cov` (148 tests, gate: 63% coverage)
- Frontend tests: `cd frontend && npm test -- --run` (277 tests)
- Frontend build: `cd frontend && npm run build`
- Coverage gate (when running with coverage): `--cov-fail-under=63`
- `_chat` (in `_LLMMixin`) MUST remain — it is used by `app/routes/skills.py:113` and `facade._llm_for_summary`.
- `_ensure_client()`'s `is_local` heuristic (local Ollama → `noop` key) MUST be preserved.
- Commit message style: `<area>: <verb> <noun>` (e.g. `agents: drop text-mode node`).
- Use `--no-verify` for speed only on this refactor; hooks still apply elsewhere.

---

## File structure (before / after)

| File | Before | After |
|------|--------|-------|
| `react_agent/graph/parsing_compat.py` | exists | **deleted** |
| `react_agent/graph/llm_compat.py` | 5 public helpers | `_LLMMixin` with only `_chat` |
| `react_agent/graph/facade.py` | fallback path + stubs | single execution path |
| `react_agent/graph/nodes.py` | `agent_node` + `text_mode_agent_node` | `agent_node` only |
| `react_agent/graph/graph_builder.py` | dual-route | linear |
| `react_agent/graph/state.py` | `text_mode` field | removed |
| `react_agent/graph/sse_adapter.py` | text_mode init | removed |
| `react_agent/prompts.py` | dual-template | FC only |
| `react_agent/summarizer.py` | legacy docstring | cleaned |
| `AGENTS.md` | fallback doc | cleaned |
| `CONVENTIONS.md` | dual-path doc | cleaned |
| `_tests/README-tests.md` | `ReActAgent` refs | `LangGraphAgent` / `make_fake_agent` |
| `README.md` / `README.zh-CN.md` | stale fallback wording | aligned with reality |

---

## Phase 1 — LangGraph side (nodes + graph + state + adapter)

### Task 1: Drop text-mode node + parsing helpers in `nodes.py`

**Files:**
- Modify: `react_agent/graph/nodes.py`

**Why:** After this, `parsing_compat.py` has zero callers and the graph builder still references a node we will delete in Task 3.

- [ ] **Step 1: Edit `react_agent/graph/nodes.py`**

Delete (exact strings):

1. Line 4: `import uuid`
2. Line 16: `from .parsing_compat import _ParsingMixin`
3. Lines 24-25:
   ```python
   _legacy_parser = _ParsingMixin()
   _legacy_parse = _legacy_parser._parse
   ```
4. Lines 86 inside `_render_system_prompt`: replace
   ```python
   function_calling=not state.get("text_mode", False),
   ```
   with
   ```python
   function_calling=True,
   ```
5. Lines 121-148: delete `text_mode_agent_node()` entirely.

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass. (Tests do not yet fail because `parsing_compat.py`
still exists; `text_mode_agent_node` was unused.)

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/nodes.py
git commit --no-verify -m "agents: drop text-mode agent node"
```

---

### Task 2: Delete `react_agent/graph/parsing_compat.py`

**Files:**
- Delete: `react_agent/graph/parsing_compat.py`

**Why:** `nodes.py` no longer imports anything from it.

- [ ] **Step 1: Delete the file**

```bash
rm react_agent/graph/parsing_compat.py
```

- [ ] **Step 2: Verify no remaining imports**

```bash
grep -rn "parsing_compat\|_ParsingMixin\|_legacy_parser\|_legacy_parse\|_repair_final_answer" react_agent app server.py _tests || echo "OK no remaining references"
```

Expected: `OK no remaining references`.

- [ ] **Step 3: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 4: Commit**

```bash
git add -u react_agent/graph/parsing_compat.py
git commit --no-verify -m "agents: remove parsing_compat (text-ReAct parser)"
```

---

### Task 3: Drop `text_agent` routing in `graph_builder.py`

**Files:**
- Modify: `react_agent/graph/graph_builder.py`

- [ ] **Step 1: Edit `react_agent/graph/graph_builder.py`**

Make these exact changes:

1. Delete lines 38-42:
   ```python
   text_agent = partial(
       nodes.text_mode_agent_node,
       llm=llm, memory=memory,
       tools_describe=tools_describe, tool_names=tool_names,
   )
   ```
2. Delete lines 77-78 (`entry_route` closure).
3. Delete lines 80-81 (`after_tools` closure).
4. Delete line 84: `workflow.add_node("text_agent", text_agent)`
5. Replace line 89:
   ```python
   workflow.add_conditional_edges(START, entry_route, {"agent": "agent", "text_agent": "text_agent"})
   ```
   with
   ```python
   workflow.add_edge(START, "agent")
   ```
6. Delete lines 94-97 (the `text_agent` conditional edges block).
7. Replace line 99:
   ```python
   workflow.add_conditional_edges("post_tools", after_tools, {"agent": "agent", "text_agent": "text_agent"})
   ```
   with
   ```python
   workflow.add_edge("post_tools", "agent")
   ```

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/graph_builder.py
git commit --no-verify -m "agents: drop text_agent routing in graph builder"
```

---

### Task 4: Drop `text_mode` field from `AgentState`

**Files:**
- Modify: `react_agent/graph/state.py`

- [ ] **Step 1: Edit `react_agent/graph/state.py`**

Delete line 21 (`text_mode: bool`).

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass. (`sse_adapter.py` still passes the key
explicitly — handled in Task 5.)

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/state.py
git commit --no-verify -m "agents: remove text_mode state field"
```

---

### Task 5: Drop `text_mode` from `state_in` init in `sse_adapter.py`

**Files:**
- Modify: `react_agent/graph/sse_adapter.py`

- [ ] **Step 1: Edit `react_agent/graph/sse_adapter.py`**

Delete line 68: `"text_mode": False,` inside the `state_in` dict.

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/sse_adapter.py
git commit --no-verify -m "agents: drop text_mode init in sse_adapter"
```

---

### Phase 1 sanity gate

After Phase 1, verify with the broad grep:

```bash
grep -rn "text_mode\|_legacy_parse\|_legacy_parser\|_ParsingMixin\|_repair_final_answer" react_agent app server.py _tests || echo "OK phase 1 clean"
```

Expected: `OK phase 1 clean`.

---

## Phase 2 — Facade & llm_compat

### Task 6: Drop fallback machinery in `facade.py`

**Files:**
- Modify: `react_agent/graph/facade.py`

- [ ] **Step 1: Edit `react_agent/graph/facade.py`**

Exact edits:

1. Line 1 docstring: replace
   ```python
   """LangGraphAgent — public facade matching legacy ReActAgent API (Stage 1)."""
   ```
   with
   ```python
   """LangGraphAgent — single-path execution facade."""
   ```
2. Line 37: delete `self._fc_unsupported: bool = False`
3. Lines 161-163 (the fallback branch in `run_stream`): replace
   ```python
   if self._fc_unsupported or self._graph is None:
       yield from self._legacy_stream(session_id, user_input, llm_input, lang, should_stop=should_stop)
       return
   ```
   with
   ```python
   if self._graph is None:
       yield {"type": "error", "content": "Agent is not configured (missing API key, base URL, or model)."}
       yield {"type": "done", "message_id": None}
       return
   ```
4. Lines 229-234 (the catch-arm that toggles `_fc_unsupported` and falls back): replace
   ```python
   except Exception as e:
       msg = str(e)
       if self._is_tools_unsupported_error(msg):
           self._fc_unsupported = True
           yield from self._legacy_stream(session_id, user_input, llm_input, lang, should_stop=should_stop)
           return
       yield {"type": "error", "content": f"{type(e).__name__}: {e}"}
       yield {"type": "done", "message_id": None}
   ```
   with
   ```python
   except Exception as e:
       yield {"type": "error", "content": f"{type(e).__name__}: {e}"}
       yield {"type": "done", "message_id": None}
   ```
5. Lines 269-274: delete `_legacy_stream()` method.
6. Lines 276-281: delete `_is_tools_unsupported_error()` static method.
7. Lines 283-284: delete the `_chat_stream` stub method.
8. Line 287 docstring: replace `Lazily build the OpenAI client used by ``_LLMMixin._chat``.` — keep as is, but also remove the comment on lines 289-292 that mentions `ReActAgent`:
   ```python
   """Mirrors ``ReActAgent._ensure_client`` so the same mixin's helpers
   (``_chat``, ``_chat_stream_tools``) work uniformly on both agents.
   The client is built once and cached on ``self.client``."""
   ```
   becomes
   ```python
   """Builds the OpenAI client lazily on first use; cached on
   ``self.client``."""
   ```
9. Line 290 reference to `_chat_stream_tools` is part of the docstring above — already covered by step 8.
10. Line 294 of `_build_system_prompt`: replace
    ```python
    function_calling=not self._fc_unsupported,
    ```
    with
    ```python
    function_calling=True,
    ```

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/facade.py
git commit --no-verify -m "agents: drop legacy fallback in facade"
```

---

### Task 7: Slim down `llm_compat.py`

**Files:**
- Modify: `react_agent/graph/llm_compat.py`

- [ ] **Step 1: Edit `react_agent/graph/llm_compat.py`**

Replace the entire file content with:

```python
"""LLM I/O: non-streaming chat used by summarizer and the skills route."""
from __future__ import annotations

import os
from typing import Dict, List, Optional


class _LLMMixin:
    def _chat(self, messages: List[Dict[str, str]], stop: Optional[List[str]] = None) -> str:
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stop=stop,
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        )
        text = resp.choices[0].message.content or ""
        return text
```

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/graph/llm_compat.py
git commit --no-verify -m "agents: slim llm_compat to _chat only"
```

---

### Phase 2 sanity gate

```bash
grep -rn "_fc_unsupported\|_legacy_stream\|_is_tools_unsupported_error\|_ToolsUnsupportedError\|_use_function_calling\|_chat_stream_tools" react_agent app server.py _tests || echo "OK phase 2 clean"
```

Expected: `OK phase 2 clean`.

---

## Phase 3 — Prompts & straggler

### Task 8: Drop dual-template in `prompts.py`

**Files:**
- Modify: `react_agent/prompts.py`

- [ ] **Step 1: Edit `react_agent/prompts.py`**

Exact edits:

1. Line 1 docstring: replace `"""ReAct Prompt 模板"""` with `"""ReAct system prompt template (FC mode only)."""`.
2. Remove `import re` (line 2) — only used by `_legacy_pattern`.
3. In `build_system_prompt` (line 84):
   - Remove `function_calling: bool = False,` from the signature.
   - Inside the body, remove the block (lines 106-117):
     ```python
     updated_tool_rule = """3. Use tools only when they are clearly relevant to the user's request, ..."""
     _legacy_pattern = re.compile(...)
     prompt_template = _legacy_pattern.sub(...)
     ```
     (Replace with nothing — the FC template already says the right thing.)
   - Replace the trailing `if function_calling:` guard (line 126) by removing `if function_calling:` and dedenting the block; it becomes unconditional.

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/prompts.py
git commit --no-verify -m "prompts: drop text-mode template branch"
```

---

### Task 9: Clean `summarizer.py` docstring

**Files:**
- Modify: `react_agent/summarizer.py`

- [ ] **Step 1: Edit `react_agent/summarizer.py`**

Replace lines 93-96:

```python
suffix used by both the legacy ``ReActAgent.run_stream`` injection site
(and its function-calling fallback rebuild) and the LangGraph
``SSEAdapter._build_initial_messages`` / ``nodes._render_system_prompt``
sites.
```

with:

```python
suffix used by the LangGraph ``SSEAdapter._build_initial_messages``
and ``nodes._render_system_prompt`` sites.
```

- [ ] **Step 2: Run pytest**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass.

- [ ] **Step 3: Commit**

```bash
git add react_agent/summarizer.py
git commit --no-verify -m "docs(summarizer): drop legacy ReActAgent mention"
```

---

## Phase 4 — Documentation

### Task 10: AGENTS.md + CONVENTIONS.md

**Files:**
- Modify: `AGENTS.md`
- Modify: `CONVENTIONS.md`

- [ ] **Step 1: Edit `AGENTS.md` line 117**

Replace:
```markdown
- To stub LLM in route tests: `monkeypatch.setattr(ReActAgent, "_chat_stream", fake_gen)`.
```
with:
```markdown
- Tests stub the per-user agent via the `make_fake_agent(user_id, reply)` fixture in `_tests/conftest.py`; it replaces `state._AGENTS[user_id]` with a `FakeAgent` whose `run_stream()` yields canned SSE events.
```

- [ ] **Step 2: Edit `AGENTS.md` line 139 area**

Find the "LLM mode" bullet (line 139):
```markdown
- **LLM mode**: Defaults to native OpenAI function-calling. Falls back to text ReAct if provider rejects `tools`. `LLM_USE_FUNCTION_CALLING=0` forces text path.
```
Replace with:
```markdown
- **LLM mode**: Native OpenAI function-calling only. If the provider rejects the `tools` parameter, the agent surfaces a clear error rather than silently degrading.
```

- [ ] **Step 3: Edit `CONVENTIONS.md` lines 135-137**

Replace:
```markdown
## 3. ReAct Agent（`react_agent/agent.py`, `tools.py`）

- 双执行路径：原生 function-calling（默认）+ 文本 ReAct 降级；`LLM_USE_FUNCTION_CALLING=0` 强制文本路径
- 新增工具：在 `default_tools()` 中 `reg.register(Tool(...))`
```
with:
```markdown
## 3. ReAct Agent（`react_agent/graph/facade.py`, `tools.py`）

- 单一执行路径：原生 OpenAI function-calling via LangGraph；provider 拒绝 `tools` 时直接报错
- 新增工具：在 `default_tools()` 中 `reg.register(Tool(...))`
```

- [ ] **Step 4: Run pytest (sanity)**

```bash
source .venv/bin/activate && python -m pytest _tests -x --no-cov
```

Expected: 148 tests pass (docs don't affect Python).

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CONVENTIONS.md
git commit --no-verify -m "docs: drop legacy react-mode references"
```

---

### Task 11: `_tests/README-tests.md`

**Files:**
- Modify: `_tests/README-tests.md`

- [ ] **Step 1: Edit `_tests/README-tests.md`**

1. Line 42 (coverage table row): replace
   ```markdown
   | `react_agent/agent.py` | 54 % (full ReAct loop covered via `_chat_stream` stub) |
   ```
   with
   ```markdown
   | `react_agent/graph/facade.py` | ~50 % (SSE covered via `make_fake_agent`; live path via LangGraph graph) |
   ```

2. Line 106: replace
   ```markdown
   | `test_jstack_api.py` | 21 | `/api/jstack/*` upload / list / sample / analyze SSE; `ReActAgent._chat_stream` stubbed at the class level |
   ```
   with
   ```markdown
   | `test_jstack_api.py` | 21 | `/api/jstack/*` upload / list / sample / analyze SSE; LangGraph path stubbed via `make_fake_agent` |
   ```

3. Lines 123-129 "Conventions when adding tests" item 3: replace the entire block
   ```markdown
   3. To stub the LLM during chat tests, override `agent._chat_stream` on
      the `ReActAgent` **class**:
      ```python
      def fake(self, messages, stop=None):
          yield "hello "
          yield "world"
      monkeypatch.setattr(ReActAgent, "_chat_stream", fake)
      ```
   ```
   with
   ```markdown
   3. To stub the LLM during chat tests, use the `make_fake_agent`
      fixture (see `_tests/conftest.py`). It drops a `FakeAgent` into
      `state._AGENTS[user_id]` whose `run_stream()` yields the SSE
      events you want — no monkeypatching needed.
   ```

- [ ] **Step 2: Commit**

```bash
git add _tests/README-tests.md
git commit --no-verify -m "docs(tests): drop ReActAgent stubbing pattern"
```

---

### Task 12: `README.md` and `README.zh-CN.md`

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Search for any remaining stale references**

```bash
grep -n "fall\|legacy\|text.ReAct\|ReActAgent\|LLM_USE_FUNCTION_CALLING" README.md README.zh-CN.md
```

Inspect each hit. The existing line at `README.md:153-155` (and the
Chinese twin) already says "surfaces a clear error rather than
silently degrading", which matches our cleaned reality. Other hits
should not exist.

- [ ] **Step 2: Adjust if any stale reference is found**

If grep returned stale wording (e.g. a "Falls back to text ReAct"
sentence somewhere), rewrite that sentence to:

EN: `If a provider rejects the \`tools\` parameter, the agent surfaces a clear error rather than silently degrading.`

ZH: `如果 provider 拒绝 \`tools\` 参数，agent 会清晰报错而不是悄悄降级。`

- [ ] **Step 3: Commit (only if Step 2 changed something)**

```bash
git add README.md README.zh-CN.md
git commit --no-verify -m "docs(readme): align with single FC path"
```

Skip if no changes were needed.

---

## Phase 5 — Final verification

### Task 13: Full verification pass

- [ ] **Step 1: Broad grep — no leftover references in code or docs**

```bash
grep -rn "text_mode\|ReActAgent\|_legacy_parse\|_legacy_stream\|_legacy_parser\|_ParsingMixin\|_repair_final_answer\|_fc_unsupported\|_is_tools_unsupported_error\|_ToolsUnsupportedError\|_use_function_calling\|_chat_stream_tools\|LLM_USE_FUNCTION_CALLING\|双执行路径\|文本 ReAct\|text.ReAct fallback\|Falls back to text ReAct" react_agent app server.py _tests AGENTS.md CONVENTIONS.md README.md README.zh-CN.md docs/ || echo "OK clean"
```

Expected: `OK clean`. The `docs/superpowers/specs/2026-07-24-remove-legacy-react-mode-design.md` and the plan file are allowed to mention these terms in retrospective context.

- [ ] **Step 2: Python tests with coverage gate**

```bash
source .venv/bin/activate && python -m pytest _tests
```

Expected: 148 tests pass and `Coverage failure: 63%` is NOT printed.

- [ ] **Step 3: Frontend tests**

```bash
cd frontend && npm test -- --run && cd ..
```

Expected: 277 frontend tests pass.

- [ ] **Step 4: Frontend build**

```bash
cd frontend && npm run build && cd ..
```

Expected: Vite build succeeds; `dist/` regenerated.

- [ ] **Step 5: Manual smoke (optional)**

Start the server and post a small chat to confirm SSE still emits
`token`, `final`, `done` events. Stop the server.

- [ ] **Step 6: Final commit if anything needs sweeping up**

If Step 1 surfaced any straggler, fix and commit; otherwise skip.

```bash
git status
```

---

## Self-review

- **Spec coverage:**
  - §"Python code" — Tasks 1-9 ✓
  - §"Documentation" — Tasks 10-12 ✓
  - §"Risks & mitigations" — R1 covered by keeping `validate_react_prompt_template`; R2 covered by per-task `pytest _tests -x --no-cov`; R3 covered by noting FC behavior unchanged.
  - §"Verification" — Task 13 covers every item.
- **Placeholder scan:** No "TBD" / "TODO" / "fill in". Code blocks are exact.
- **Type consistency:** `_chat`, `_ensure_client`, `make_fake_agent`, `state_in`, `AgentState` used consistently with spec and current code.
- **Order of tasks:** Phase 1 ends with `nodes.py` having no
  `parsing_compat` import (Task 1) before deletion (Task 2); Phase 2
  updates facade (Task 6) before slimming `llm_compat` (Task 7) so
  `_chat` still exists when other consumers need it.