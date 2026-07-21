"""LLM factory with reasoning_content support for DeepSeek-style models."""
from __future__ import annotations

from typing import Any, Iterator, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class _ReasoningOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that preserves reasoning_content from API deltas.

    langchain-openai's _convert_delta_to_message_chunk does not copy the
    reasoning_content field (DeepSeek-R1, o1 series) from the raw API delta
    into AIMessageChunk.additional_kwargs, silently dropping it. This
    subclass overrides the chunk conversion to inject it there so the SSE
    adapter can route it to token{phase:"reason"} events.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return None
        # Extract reasoning_content from raw delta before it's discarded
        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = (choices[0] or {}).get("delta") or {}
            rc = delta.get("reasoning_content")
            if rc and isinstance(result.message, AIMessageChunk):
                result.message.additional_kwargs["reasoning_content"] = rc
        return result

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        yield from super()._stream(*args, **kwargs)

    async def _astream(
        self, *args: Any, **kwargs: Any
    ) -> Any:
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk


class ReasoningContentCallbackHandler(BaseCallbackHandler):
    """Captures reasoning_content deltas (DeepSeek-R1 style) from streaming chunks."""

    def __init__(self) -> None:
        self.reasoning_buffer: List[str] = []

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.reasoning_buffer = []

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        chunk: Optional[AIMessageChunk] = kwargs.get("chunk")
        if chunk is None:
            return
        if hasattr(chunk, "additional_kwargs"):
            rc = chunk.additional_kwargs.get("reasoning_content")
            if rc:
                self.reasoning_buffer.append(rc)


def build_llm(
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.3,
) -> _ReasoningOpenAI:
    """Construct a ChatOpenAI instance with reasoning_content support."""
    return _ReasoningOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        streaming=True,
    )
