"""ToolException subclasses used by the LangGraph tool layer."""
from __future__ import annotations

from langchain_core.tools import ToolException


class ToolRetryableError(ToolException):
    """Raised by a tool for transient/network errors.

    ToolNode exponential-backoff retries this error up to 2 times before
    giving up and surfacing it as a ToolMessage to the LLM.
    """

    retryable: bool = True


class ToolValidationError(ToolException):
    """Raised by a tool for argument/permanent errors.

    Not retried. The error message is surfaced to the LLM as a ToolMessage
    so the model can correct its next tool_call.
    """

    retryable: bool = False