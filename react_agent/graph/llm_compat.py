"""LLM I/O: non-streaming chat used by the summarizer and the skills route."""
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