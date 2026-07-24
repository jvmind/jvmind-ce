"""LangGraph agent state definition."""
from __future__ import annotations

import queue
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


MAX_HISTORY_MESSAGES: int = 40


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    user_id: str
    lang: str
    max_iterations: int
    iteration: int
    scratchpad: str
    system_prompt: str
    system_prompt_extra: str
    progress_queue: Optional["queue.Queue"]
    finalize_structured: bool
    diagnostic_attachments: dict
