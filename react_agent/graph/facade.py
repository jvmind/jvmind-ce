"""LangGraphAgent — public facade matching legacy ReActAgent API (Stage 1)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import BaseMessage
from openai import OpenAI

from ..agent.llm import _LLMMixin
from .graph_builder import build_graph
from .llm import build_llm
from .sse_adapter import SSEAdapter
from .tools import build_all_tools


class LangGraphAgent(_LLMMixin):
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        temperature: float = 0.3,
        max_iterations: int = 10,
        system_prompt_template: str = "",
        system_prompt_extra: str = "",
        memory=None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self.system_prompt_template = system_prompt_template or ""
        self.system_prompt_extra = system_prompt_extra or ""
        self.memory = memory
        self._skill_defs: List[dict] = []
        self._fc_unsupported: bool = False
        self._llm = None
        self._tools: List = []
        self._tools_describe: str = ""
        self._tool_names: List[str] = []
        self._graph = None
        self.client: Optional[OpenAI] = None
        from concurrent.futures import ThreadPoolExecutor
        self._executor: Optional[ThreadPoolExecutor] = None
        self._build()

    def _build(self) -> None:
        if not self.api_key or not self.base_url or not self.model:
            self._llm = None
            self._tools = []
            self._graph = None
            return
        self._llm = build_llm(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
        )
        self._tools = build_all_tools(self.memory, self._skill_defs)
        self._tool_names = [t.name for t in self._tools]
        self._tools_describe = self._describe_tools_raw(self._tools)
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(
                max_workers=8, thread_name_prefix=f"lg-{self.model}-",
            )
        self._graph = build_graph(
            llm=self._llm,
            tools=self._tools,
            memory=self.memory,
            tools_describe=self._tools_describe,
            tool_names=self._tool_names,
            max_iterations=self.max_iterations,
            executor=self._executor,
        )

    @staticmethod
    def _describe_tools(tools, lang: str = "") -> str:
        lines = []
        for t in tools:
            args_hint = _tool_args_hint(t)
            desc = _filter_desc(t.description, lang)
            lines.append(f"- {t.name}({args_hint}): {desc}")
        return "\n".join(lines)

    @staticmethod
    def _describe_tools_raw(tools) -> str:
        """Fallback for graph-level descriptions (no lang context)."""
        return LangGraphAgent._describe_tools(tools, "")

    def reconfigure(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_iterations: Optional[int] = None,
        system_prompt_template: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ) -> None:
        changed = False
        if api_key is not None and api_key != self.api_key:
            self.api_key = api_key
            changed = True
        if base_url is not None and base_url != self.base_url:
            self.base_url = base_url
            changed = True
        if model is not None and model != self.model:
            self.model = model
            changed = True
        if temperature is not None and temperature != self.temperature:
            self.temperature = temperature
            changed = True
        if max_iterations is not None and max_iterations != self.max_iterations:
            self.max_iterations = max_iterations
            changed = True
        if system_prompt_template is not None:
            self.system_prompt_template = system_prompt_template
        if system_prompt_extra is not None:
            self.system_prompt_extra = system_prompt_extra
        if changed:
            self._build()

    def load_skills(self, skills: List[dict]) -> None:
        self._skill_defs = list(skills or [])
        self._build()

    def _build_system_prompt(self, session_id: str, lang: str) -> str:
        from ..prompts import build_system_prompt
        facts = self.memory.get_prompt_facts(session_id) if self.memory is not None else []
        tools_describe = self._describe_tools(self._tools, lang)
        return build_system_prompt(
            tool_names=self._tool_names,
            tool_descriptions=tools_describe,
            facts=facts,
            template=self.system_prompt_template or None,
            extra=self.system_prompt_extra,
            lang=lang,
            function_calling=not self._fc_unsupported,
        )

    def run(self, session_id: str, user_input: str, lang: str = "") -> Tuple[str, List[Dict[str, Any]]]:
        steps: List[Dict[str, Any]] = []
        final_text = ""
        for event in self.run_stream(session_id, user_input, lang=lang):
            if event.get("type") == "step":
                steps.append(event)
            elif event.get("type") == "final":
                final_text = event.get("content", "")
        return final_text, steps

    def run_stream(
        self,
        session_id: str,
        user_input: str,
        llm_input: Optional[str] = None,
        lang: str = "",
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        if self._fc_unsupported or self._graph is None:
            yield from self._legacy_stream(session_id, user_input, llm_input, lang, should_stop=should_stop)
            return
        token_usage = None
        summary_triggered = False

        def _trigger_summary() -> None:
            """Best-effort: call maybe_summarize, swallow any failure.
            Guarded by the outer `summary_triggered` flag to guarantee idempotency."""
            nonlocal summary_triggered
            if summary_triggered:
                return
            summary_triggered = True
            try:
                from ..summarizer import maybe_summarize
                maybe_summarize(session_id, self.memory, self._llm_for_summary())
            except Exception:
                # summarizer.maybe_summarize already swallows internally; this is
                # belt-and-suspenders for anything outside its contract.
                pass

        try:
            history: List[Dict[str, str]] = []
            if self.memory is not None:
                try:
                    history = self.memory.get_messages(session_id)
                except Exception:
                    history = []
            from ..memory.token_budget import assemble_with_budget, get_context_window
            try:
                from ..memory.facts import scored_facts
                facts = scored_facts(self.memory, session_id, model=self.model, budget_tokens=800)
                facts_block = "\n".join(f["text"] for f in facts) if facts else ""
            except Exception:
                facts_block = ""
            try:
                summary_text = self.memory.get_context_fact(session_id, "summary") or ""
            except Exception:
                summary_text = ""
            text_input = llm_input if llm_input is not None else user_input
            assembled = assemble_with_budget(
                system_prompt=self.system_prompt_template or "",
                facts_block=facts_block,
                summary=summary_text,
                history=history,
                model=self.model,
                context_window=get_context_window(self.model),
                reserve_tokens=2000,
                keep_last_turns=10,
            )
            initial_msgs = self._sse_adapter_build_initial(
                assembled["messages"], text_input, session_id=session_id,
            )
            adapter = SSEAdapter(self._graph, self.memory)
            for event in adapter.run_stream(
                session_id=session_id,
                user_input=user_input,
                llm_input=llm_input,
                lang=lang,
                initial_messages=initial_msgs,
                max_iterations=self.max_iterations,
                should_stop=should_stop,
            ):
                if event.get("type") == "done":
                    token_usage = event.get("tokens")
                yield event
                if event.get("type") == "done":
                    _trigger_summary()
        except Exception as e:
            msg = str(e)
            if self._is_tools_unsupported_error(msg):
                self._fc_unsupported = True
                yield from self._legacy_stream(session_id, user_input, llm_input, lang, should_stop=should_stop)
                return
            yield {"type": "error", "content": f"{type(e).__name__}: {e}"}
            yield {"type": "done", "message_id": None}
        finally:
            # Safety net: if `done` was never emitted (consumer disconnect,
            # BaseException, hard adapter failure before yield), still run
            # summarization exactly once.
            _trigger_summary()
            if token_usage and token_usage.get("total_tokens", 0) > 0:
                _save_token_usage(token_usage, session_id, self.model, self.memory)

    def _sse_adapter_build_initial(
        self,
        assembled: List[Dict[str, Any]],
        text_input: str,
        *,
        session_id: str = "",
    ) -> List[BaseMessage]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        msgs: List[BaseMessage] = []
        for m in assembled:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                msgs.append(SystemMessage(content=content))
            elif role == "user":
                msgs.append(HumanMessage(content=content))
            elif role == "assistant":
                msgs.append(AIMessage(content=content))
        if not msgs or not isinstance(msgs[-1], HumanMessage):
            msgs.append(HumanMessage(content=text_input))
        else:
            msgs[-1] = HumanMessage(content=text_input)
        return msgs

    def _legacy_stream(self, session_id, user_input, llm_input, lang, should_stop=None):
        from ..agent import ReActAgent as LegacyAgent
        legacy = LegacyAgent(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_iterations=self.max_iterations,
            system_prompt_template=self.system_prompt_template,
            system_prompt_extra=self.system_prompt_extra,
            memory=self.memory,
        )
        legacy._fc_unsupported = self._fc_unsupported
        yield from legacy.run_stream(session_id, user_input, llm_input=llm_input, lang=lang, should_stop=should_stop)

    @staticmethod
    def _is_tools_unsupported_error(msg: str) -> bool:
        keywords = ["does not support", "tools is not supported", "unsupported parameter: tools",
                    "unrecognized parameter", "invalid_request_error", "tool_choice"]
        m = msg.lower()
        return any(k in m for k in keywords)

    def _chat_stream(self, *args, **kwargs):
        raise NotImplementedError("_chat_stream is a stub for monkeypatch compatibility; use run_stream instead")

    def _ensure_client(self) -> OpenAI:
        """Lazily build the OpenAI client used by ``_LLMMixin._chat``.

        Mirrors ``ReActAgent._ensure_client`` so the same mixin's helpers
        (``_chat``, ``_chat_stream_tools``) work uniformly on both agents.
        The client is built once and cached on ``self.client``.
        """
        if self.client is None:
            if not self.api_key:
                raise ValueError(
                    "请先配置 API Key / Please configure API Key first"
                )
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self.client

    def _llm_for_summary(self) -> Callable[[List[Dict[str, str]]], str]:
        """Return a non-streaming chat-completion callable for the summarizer.

        Delegates to the existing ``_LLMMixin._chat`` so model / temperature /
        timeout stay in one place. Raises immediately if ``api_key`` is empty
        (the caller's ``except Exception: pass`` swallows the failure, so
        external behavior is unchanged — but the contract becomes "returns a
        working callable or raises", never returns a fake that raises later).
        """
        if not self.api_key:
            raise RuntimeError("api_key not configured; cannot call summary LLM")
        return self._chat


def _tool_args_hint(t) -> str:
    """Derive args hint from a StructuredTool's args_schema fields."""
    schema = getattr(t, "args_schema", None)
    if schema is not None:
        try:
            # Filter out injected state field
            names = [k for k in schema.model_fields if k != "state"]
            if names:
                return ",".join(names)
        except Exception:
            pass
    return "input"


_ZH_SEP = " / "


def _filter_desc(desc: str, lang: str) -> str:
    """Keep only the relevant language portion of a bilingual description."""
    desc = (desc or "").strip()
    if not desc or lang == "":
        return desc
    if lang == "zh":
        return _extract_zh(desc)
    else:
        return _extract_en(desc)


def _extract_zh(desc: str) -> str:
    """Extract Chinese portion from a bilingual description string."""
    # Try explicit "EN / ZH" pattern first
    parts = desc.split(_ZH_SEP, 1)
    if len(parts) == 2 and _has_cjk(parts[1]) and not _has_cjk(parts[0]):
        return parts[1].strip()
    # Try paragraph-level split: find where CJK starts, return from there
    lines = desc.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and _starts_cjk(stripped) and i > 0:
            return "\n".join(lines[i:]).strip()
    # If first meaningful line is already CJK, return everything
    if lines and _starts_cjk(lines[0].strip()):
        return desc
    return desc


def _extract_en(desc: str) -> str:
    """Extract English portion from a bilingual description string."""
    # Try explicit "EN / ZH" pattern first
    idx = desc.rfind(_ZH_SEP)
    if idx > 0:
        en_part = desc[:idx].strip()
        zh_part = desc[idx + len(_ZH_SEP):].strip()
        if _has_cjk(zh_part) and not _has_cjk(en_part):
            return en_part
    # Try paragraph-level split: return lines before first CJK paragraph
    lines = desc.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped and _starts_cjk(stripped):
            break
        result.append(line)
    joined = "\n".join(result).strip()
    return joined if joined else desc


def _starts_cjk(s: str) -> bool:
    """Check if string starts with CJK characters."""
    if not s:
        return False
    ch = s[0]
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF)


def _save_token_usage(token_usage: dict, session_id: str, model: str, memory) -> None:
    """Persist token usage to DB. Fire-and-forget, never raises."""
    try:
        from ..models import TokenUsageModel
        from ..db import SessionLocal
        user_id = ""
        try:
            user_id = getattr(memory, "_user_id", "")
        except Exception:
            pass
        db = SessionLocal()
        try:
            db.add(TokenUsageModel(
                user_id=user_id,
                session_id=session_id,
                model=model,
                input_tokens=token_usage.get("input_tokens", 0),
                output_tokens=token_usage.get("output_tokens", 0),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def _has_cjk(s: str) -> bool:
    """Check if string contains CJK characters."""
    for ch in s:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF):
            return True
    return False
