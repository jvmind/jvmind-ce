"""Convenience wrapper for the DB-only uploaded-file read path."""
from __future__ import annotations

from typing import Any


def get_uploaded_text(memory: Any, file_id: str) -> str:
    """Delegate to ``memory.get_uploaded_text(file_id)``.

    Centralised so callers (tool functions, legacy paths) can be agnostic
    to whether the memory object is a ``DatabaseMemory`` or a test stub.
    """
    if memory is None:
        return ""
    fn = getattr(memory, "get_uploaded_text", None)
    if callable(fn):
        return fn(file_id) or ""
    return ""
