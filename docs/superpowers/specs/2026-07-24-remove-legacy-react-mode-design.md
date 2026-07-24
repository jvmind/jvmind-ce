# Remove legacy text-ReAct mode from JVMind CE

Status: Approved 2026-07-24
Project: JVMind CE (`/home/pan/jvmind-ce`)

## Problem

JVMind CE has fully migrated to the LangGraph state machine
(`react_agent/graph/`) as its only production path, but the code and docs
still carry a full "text-ReAct fallback" branch no live path can reach.
The leftovers span 8 Python files + 4 docs, and cause:

- Newcomers believe a fallback exists; in reality it raises `RuntimeError`
- Dead code blunts reading and refactoring
- `prompts.py` maintains both FC and text protocol templates, leaving
  an interface that callers can still reach for

## Goal

Remove the "old react mode" — the text-protocol ReAct loop — from code
and docs, leaving CE with exactly one execution path: LangGraph + native
OpenAI function-calling.

## Non-goals

- No changes to the LangGraph main loop itself
- No changes to tool registration, `summarizer`, memory, route layer
- No new dependencies

## Decisions (confirmed with user)

### Scope: code + docs full cleanup

We explicitly **do not** take the narrower options:

- Docs-only sync → leaves dead code, hurts readability
- Drop text-mode node only → `parsing_compat`/`llm_compat` dead helpers
  still linger

### File organization

- `parsing_compat.py` **deleted entirely** (only referenced by
  `text_mode_agent_node`)
- `llm_compat.py` **simplified in place** (keeps `_LLMMixin._chat`)

## Changes

### Python code (7 files + 1 docstring touch-up)

#### 1. `react_agent/graph/parsing_compat.py` — DELETE

Whole file. `_ParsingMixin._parse` and `_repair_final_answer` are only
called by `nodes._legacy_parse` / `text_mode_agent_node`; both die with
the text-mode node.

#### 2. `react_agent/graph/llm_compat.py` — slim down

Keep: `_LLMMixin._chat` (called by `app/routes/skills.py:113` and by
`facade._llm_for_summary`).

Delete:
- `_ToolsUnsupportedError` class
- `_is_tools_unsupported_error()` function
- `_use_function_calling()` function (the `LLM_USE_FUNCTION_CALLING` env
  var loses its only consumer)
- `_LLMMixin._chat_stream()` (no production caller; the matching stub in
  facade goes too)
- `_LLMMixin._chat_stream_tools()` (no production caller; only a
  docstring mention in `facade.py:290`)

#### 3. `react_agent/graph/facade.py` — drop the dead fallback path

Delete:
- `self._fc_unsupported: bool = False` field (line 37)
- Two branches in `run_stream()`: `if self._fc_unsupported or
  self._graph is None` (lines 161-163) and the `except` arm that sets
  `self._fc_unsupported = True` then falls back to `_legacy_stream`
  (lines 229-234)
- `_legacy_stream()` method (lines 269-274)
- `_is_tools_unsupported_error()` static method (lines 276-281)
- `_chat_stream()` stub method (lines 283-284) — its docstring claims
  "for monkeypatch compatibility", but the
  `monkeypatch.setattr(ReActAgent, "_chat_stream", ...)` pattern from
  `_tests/README-tests.md` no longer matches the test code, which uses
  `make_fake_agent` at the agent level

Modify:
- Strip `legacy ReActAgent` mentions from all docstrings
- After deletion, the `run_stream` `except` arm becomes: yield `error`
  + `done` and stop. No retry, no fallback.
- `_llm_for_summary` docstring: drop `legacy ReActAgent` wording
- Keep `_ensure_client()`'s `is_local` heuristic (still needed to feed
  `noop` key to local Ollama)

#### 4. `react_agent/graph/nodes.py` — drop the text-mode node

Delete:
- `import uuid` (line 4, only used by `text_mode_agent_node`)
- `from .parsing_compat import _ParsingMixin` (line 16)
- `_legacy_parser = _ParsingMixin()` (line 24)
- `_legacy_parse = _legacy_parser._parse` (line 25)
- `text_mode_agent_node()` (lines 121-148)

Modify:
- `_render_system_prompt()`: replace
  `function_calling=not state.get("text_mode", False)` with
  `function_calling=True`

#### 5. `react_agent/graph/graph_builder.py` — drop `text_agent` routing

Delete:
- `text_agent = partial(nodes.text_mode_agent_node, ...)` (lines 38-42)
- `entry_route` closure (lines 77-78) — `START` connects directly to
  `agent`
- `after_tools` closure (lines 80-81) — `post_tools` connects directly
  to `agent`
- `workflow.add_node("text_agent", text_agent)` (line 84)
- `workflow.add_conditional_edges(START, entry_route, ...)` (line 89) —
  replace with `workflow.add_edge(START, "agent")`
- `workflow.add_conditional_edges("text_agent", nodes.should_continue, ...)`
  (lines 94-97)

Modify:
- `post_tools → agent` becomes `workflow.add_edge`

#### 6. `react_agent/graph/state.py` — drop state field

- Remove `text_mode: bool` from `AgentState` (line 21)

#### 7. `react_agent/graph/sse_adapter.py` — drop state init

- Remove `"text_mode": False` from `state_in` dict (line 68)

#### 8. `react_agent/prompts.py` — drop text branch

`build_system_prompt` current signature:

```python
def build_system_prompt(tool_names, tool_descriptions, facts, template=None, extra="", lang="", function_calling=False) -> str
```

Modify:
- Remove `function_calling` parameter
- Body always uses the FC branch — the TOOL-CALLING MODE suffix becomes
  the unconditional default
- Remove the `_legacy_pattern` regex substitution (lines 113-117) — its
  job ("soften legacy tool-first rule") only matters for the text-mode
  template. The placeholder validator (`validate_react_prompt_template`)
  is kept as the safety net for admin-customized templates that omit
  `{tool_names}` / `{tool_descriptions}` / `{memory_block}`

#### 9. Stragglers

- `react_agent/summarizer.py:94` — remove `legacy ReActAgent.run_stream
  injection site (and its function-calling fallback rebuild)` from the
  docstring

### Documentation (4 files)

#### `AGENTS.md`
- Line 117: `monkeypatch.setattr(ReActAgent, "_chat_stream", ...)` →
  describe the current `make_fake_agent(user_id, reply)` pattern
- Line 139: drop the "Falls back to text ReAct" sentence. Replace with
  "If the provider rejects tools, the agent surfaces a clear error."
- Drop mention of `LLM_USE_FUNCTION_CALLING=0`

#### `CONVENTIONS.md`
- Section 3 opening (lines 135-137): drop "双执行路径" line; rewrite as
  single-path description

#### `_tests/README-tests.md`
- Line 42: table row `react_agent/agent.py | 54 %` → `react_agent/graph/facade.py | ... (SSE via fake agent)`
- Line 106: `ReActAgent._chat_stream stubbed at the class level` → `LangGraphAgent stubbed via make_fake_agent`
- Lines 123-129: rewrite conventions item 3 to drop the
  `monkeypatch.setattr(ReActAgent, ...)` example block; point to
  `make_fake_agent`

#### `README.md` / `README.zh-CN.md`
- Lines around 153-155: confirm the existing "surfaces a clear error
  rather than silently degrading" wording already matches; no change
  needed
- LLM mode paragraph near the agent description: align with AGENTS.md
  ("single path, surfaces error")

## Risks & mitigations

### R1: Admin-customized system prompt without placeholders

Legacy DB rows may carry templates missing the three placeholders. The
existing `validate_react_prompt_template` validator already raises on
that, and we keep that safety net.

### R2: Removing the `_chat_stream` stub breaks a test

Current `_tests/` grep shows no `monkeypatch.setattr(*_chat_stream*)`
call site (only the README mentions it). If we miss something, `pytest
_tests` will catch it.

### R3: Prompt change user-visible behavior

FC mode already uses the same template; the text-mode-only rule
softening never applied on the FC path. Behavior unchanged for live
sessions.

## Verification

1. `grep -rn "text_mode\|ReActAgent\|_legacy_parse\|_legacy_stream\|_ToolsUnsupportedError\|_is_tools_unsupported_error\|_use_function_calling\|_ParsingMixin\|_chat_stream_tools"` against `react_agent app server.py _tests` → no production-code hits
2. `source .venv/bin/activate && python -m pytest _tests -x --no-cov` → 148 tests green
3. `python -m pytest _tests` → coverage ≥ 63% gate
4. `cd frontend && npm test -- --run` → 277 tests green
5. `cd frontend && npm run build` → Vite build clean
6. (Optional manual smoke): start `server.py`, run a chat and confirm SSE
   still emits token + final

## Out of scope

- LLM provider list, tool registration changes
- Memory / session / report layer
- Billing / Paddle / settings UI
- Any dependency upgrade

## Open questions

None.
