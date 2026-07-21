"""ReAct Agent 包。

由原单文件 ``react_agent/agent.py`` 拆分为分层模块：

- ``parsing``    — 文本解析、正则、AgentStep、修复
- ``llm``        — LLM I/O（_chat / _chat_stream / _chat_stream_tools）
- ``tools_exec`` — skill 加载与工具调度
- ``loop``       — run / run_stream 主循环与三条执行路径

``ReActAgent`` 通过组合上述 Mixin 保持单一类外观，确保现有
``monkeypatch.setattr(ReActAgent, "_chat_stream", ...)`` 等测试零改动。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai import OpenAI

from ..memory_db import DatabaseMemory
from ..tools import ToolRegistry, default_tools
from .llm import _LLMMixin, _ToolsUnsupportedError, _is_tools_unsupported_error, _use_function_calling
from .loop import _LoopMixin
from .parsing import _RE_ACTION, _RE_ACTION_INPUT, _RE_FINAL, _RE_THOUGHT, AgentStep, _ParsingMixin
from .tools_exec import _ToolsExecMixin


@dataclass
class ReActAgent(_LLMMixin, _ParsingMixin, _ToolsExecMixin, _LoopMixin):
    api_key: str
    base_url: str
    model: str
    tools: ToolRegistry = field(default_factory=default_tools)
    memory: DatabaseMemory = None
    max_iterations: int = 10
    temperature: float = 0.3
    system_prompt_template: str = ""
    system_prompt_extra: str = ""
    _skill_names: set = field(default_factory=set)
    # Set True at runtime once the provider rejects `tools` so we stop retrying.
    _fc_unsupported: bool = False

    def __post_init__(self) -> None:
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url) if self.api_key else None

    def _ensure_client(self):
        if not self.client:
            if not self.api_key:
                raise ValueError("请先配置 API Key / Please configure API Key first")
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self.client


def build_default_agent() -> ReActAgent:
    """从环境变量构建一个 Agent 实例（供 Gradio 本地开发使用）"""
    from dotenv import load_dotenv
    load_dotenv()
    session_dir = os.getenv("SESSION_DIR", "./sessions")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置。请在 .env 中设置。 / OPENAI_API_KEY not configured. Set it in .env.")
    # 项目已完全迁移到 DatabaseMemory，JSONMemory 已废弃
    from ..memory_db import DatabaseMemory
    return ReActAgent(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.3")),
        memory=DatabaseMemory(user_id="local", session_dir=session_dir),
        max_iterations=int(os.getenv("MAX_ITERATIONS", "10")),
    )


_LOAD_TEST_MODE = os.getenv("LOAD_TEST_MODE", "0").lower() in ("1", "true", "yes")
if _LOAD_TEST_MODE:
    _MOCK_TEXT = "Thought: load test\nFinal Answer: Mock response for load testing.\n"

    def _mock_chat_stream(self, messages, stop=None):
        yield _MOCK_TEXT
        return _MOCK_TEXT

    def _mock_chat(self, messages, stop=None):
        return _MOCK_TEXT

    ReActAgent._chat_stream = _mock_chat_stream
    ReActAgent._chat = _mock_chat


__all__ = [
    "ReActAgent",
    "AgentStep",
    "build_default_agent",
    "_ToolsUnsupportedError",
    "_is_tools_unsupported_error",
    "_use_function_calling",
    "_RE_FINAL",
    "_RE_THOUGHT",
    "_RE_ACTION",
    "_RE_ACTION_INPUT",
]


# LangGraph-backed replacement (Stage 1). Imported lazily to avoid hard dep on graph package.
try:
    from ..graph.facade import LangGraphAgent  # noqa: F401
    __all__.append("LangGraphAgent")
except Exception:  # pragma: no cover - graph package may not yet be fully built
    LangGraphAgent = None  # type: ignore
