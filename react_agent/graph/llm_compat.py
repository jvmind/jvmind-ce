"""LLM I/O 层：非流式/流式 chat 调用与 function-calling 流。"""
from __future__ import annotations

import os
from typing import Any, Dict, Generator, List, Optional


class _ToolsUnsupportedError(Exception):
    """Raised when the provider/model rejects the `tools` parameter, signalling
    the caller to fall back to the legacy text ReAct path."""


def _is_tools_unsupported_error(e: Exception) -> bool:
    msg = str(e).lower()
    # Require both a tools-ish keyword and a support-ish keyword to avoid
    # misclassifying unrelated errors (timeouts, auth, quota, etc.).
    has_tool = any(k in msg for k in ("tool", "function"))
    has_support = any(k in msg for k in ("not support", "unsupported", "does not support", "no support", "invalid", "unrecognized", "unknown parameter"))
    return has_tool and has_support


def _use_function_calling() -> bool:
    """Whether to use native OpenAI function-calling instead of text ReAct.

    Enabled by default; set LLM_USE_FUNCTION_CALLING=0 to force the legacy
    text-protocol path. On a runtime "tools not supported" error the agent also
    falls back automatically for the rest of the process.
    """
    return os.getenv("LLM_USE_FUNCTION_CALLING", "1").lower() in ("1", "true", "yes")


class _LLMMixin:
    # ---------- 单次 LLM 调用 ----------
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

    def _chat_stream(
        self, messages: List[Dict[str, str]], stop: Optional[List[str]] = None
    ) -> Generator[str, None, str]:
        """流式 LLM 调用，yield 每个 token (delta)，返回完整文本。

        某些 OpenAI 兼容服务遇到 stop 不会真正截断，所以本地也做一遍软截断。
        """
        full = ""
        reason_buf = ""
        client = self._ensure_client()
        stream = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stop=stop,
            stream=True,
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        )
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
                reasoning = getattr(chunk.choices[0].delta, 'reasoning_content', None) or ""
            except (IndexError, AttributeError):
                delta = ""
                reasoning = ""

            # reasoning_content 始终累积，等 content 到了再合成发出
            if reasoning:
                reason_buf += reasoning
                if not delta:
                    continue
            if reason_buf:
                delta = f"<think>{reason_buf}</think>" + delta
                reason_buf = ""

            if not delta:
                continue
            full += delta
            yield delta
            # 软停止：检测到任意 stop token 立即结束
            if stop:
                for s in stop:
                    if s and s in full:
                        idx = full.find(s)
                        full = full[:idx]
                        return full
        return full

    def _chat_stream_tools(
        self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming function-calling call.

        Yields event dicts:
          {"kind": "reason", "text": str}   reasoning_content delta (thinking box)
          {"kind": "final",  "text": str}   content delta (answer bubble)
          {"kind": "tool_calls", "calls": [{"id","name","arguments"}]}  at stream end
          {"kind": "finish", "content": str}  full assistant content text

        Raises on provider errors (caller decides whether to fall back).
        """
        client = self._ensure_client()
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                tools=tools,
                tool_choice="auto",
                stream=True,
                timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
            )
        except Exception as e:  # noqa: BLE001
            if _is_tools_unsupported_error(e):
                raise _ToolsUnsupportedError(str(e)) from e
            raise
        content_full = ""
        # index -> {"id","name","arguments"}
        tc_acc: Dict[int, Dict[str, str]] = {}
        try:
            stream_iter = iter(stream)
        except TypeError:
            stream_iter = stream
        while True:
            try:
                chunk = next(stream_iter)
            except StopIteration:
                break
            except _ToolsUnsupportedError:
                raise
            except Exception as e:  # noqa: BLE001
                if _is_tools_unsupported_error(e):
                    raise _ToolsUnsupportedError(str(e)) from e
                raise
            try:
                choice = chunk.choices[0]
                delta = choice.delta
            except (IndexError, AttributeError):
                continue

            reasoning = getattr(delta, "reasoning_content", None) or ""
            if reasoning:
                yield {"kind": "reason", "text": reasoning}

            content = getattr(delta, "content", None) or ""
            if content:
                content_full += content
                yield {"kind": "final", "text": content}

            tcs = getattr(delta, "tool_calls", None) or []
            for tc in tcs:
                idx = getattr(tc, "index", 0) or 0
                slot = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments

        if tc_acc:
            calls = [tc_acc[i] for i in sorted(tc_acc.keys()) if tc_acc[i].get("name")]
            if calls:
                yield {"kind": "tool_calls", "calls": calls}
        yield {"kind": "finish", "content": content_full}
