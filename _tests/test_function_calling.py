"""Unit tests for native function-calling plumbing in ReActAgent.

These exercise the streaming tool_call accumulation, JSON-args -> single-string
conversion, the OpenAI tools schema export, and the provider-fallback detection
without hitting any network.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from react_agent.agent import ReActAgent, _ToolsUnsupportedError, _is_tools_unsupported_error
from react_agent.tools import default_tools


# ---------- fake streaming SDK objects ----------

def _chunk(content=None, reasoning=None, tool_calls=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tc(index, call_id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=fn)


class _FakeCompletions:
    def __init__(self, chunks=None, error=None):
        self._chunks = chunks or []
        self._error = error

    def create(self, **kwargs):
        if self._error is not None:
            raise self._error
        return iter(self._chunks)


class _FakeClient:
    def __init__(self, chunks=None, error=None):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks, error))


def _agent(client):
    a = ReActAgent(api_key="x", base_url="https://fake.local/v1", model="m")
    a.client = client
    return a


# ---------- tests ----------

def test_stream_tools_accumulates_split_tool_call():
    """A tool_call streamed across multiple chunks (split id/name/arguments)
    is reassembled into a single call."""
    chunks = [
        _chunk(reasoning="thinking..."),
        _chunk(tool_calls=[_tc(0, call_id="call_1", name="read_gc_report")]),
        _chunk(tool_calls=[_tc(0, arguments='{"query": ')]),
        _chunk(tool_calls=[_tc(0, arguments='"d660"}')]),
    ]
    agent = _agent(_FakeClient(chunks))
    events = list(agent._chat_stream_tools([], tools=[]))

    reasons = [e["text"] for e in events if e["kind"] == "reason"]
    assert reasons == ["thinking..."]

    tcs = [e for e in events if e["kind"] == "tool_calls"]
    assert len(tcs) == 1
    calls = tcs[0]["calls"]
    assert calls[0]["name"] == "read_gc_report"
    assert calls[0]["arguments"] == '{"query": "d660"}'
    assert calls[0]["id"] == "call_1"


def test_stream_tools_plain_content_no_calls():
    chunks = [_chunk(content="Hello "), _chunk(content="world")]
    agent = _agent(_FakeClient(chunks))
    events = list(agent._chat_stream_tools([], tools=[]))

    finals = [e["text"] for e in events if e["kind"] == "final"]
    assert "".join(finals) == "Hello world"
    assert not any(e["kind"] == "tool_calls" for e in events)
    finish = [e for e in events if e["kind"] == "finish"]
    assert finish and finish[0]["content"] == "Hello world"


def test_stream_tools_create_error_maps_to_unsupported():
    err = Exception("This model does not support tools / function calling")
    agent = _agent(_FakeClient(error=err))
    with pytest.raises(_ToolsUnsupportedError):
        list(agent._chat_stream_tools([], tools=[]))


def test_is_tools_unsupported_error_discriminates():
    assert _is_tools_unsupported_error(Exception("tools is not supported by this model"))
    assert _is_tools_unsupported_error(Exception("unknown parameter: tools"))
    # Unrelated errors must NOT be misclassified.
    assert not _is_tools_unsupported_error(Exception("connection timeout"))
    assert not _is_tools_unsupported_error(Exception("401 invalid api key"))


def test_toolcall_to_arg_single_and_multi():
    agent = _agent(_FakeClient())
    # remember -> fact
    assert agent._toolcall_to_arg("remember", '{"fact": "likes ZGC"}') == "likes ZGC"
    # read_gc_report -> query (single property)
    assert agent._toolcall_to_arg("read_gc_report", '{"query": "list"}') == "list"
    # analyze_specific_thread -> two props joined by comma in declared order
    arg = agent._toolcall_to_arg(
        "analyze_specific_thread",
        '{"file_id_or_report_id": "rid_x", "thread_name_or_nid": "http-3"}',
    )
    assert arg == "rid_x,http-3"


def test_toolcall_to_arg_bad_json_falls_back_to_raw():
    agent = _agent(_FakeClient())
    assert agent._toolcall_to_arg("read_gc_report", "list") == "list"


def test_to_openai_tools_includes_remember_and_builtins():
    specs = default_tools().to_openai_tools()
    names = {s["function"]["name"] for s in specs}
    assert "remember" in names
    assert "read_gc_report" in names
    assert "analyze_specific_thread" in names
    for s in specs:
        fn = s["function"]
        assert s["type"] == "function"
        assert isinstance(fn["parameters"], dict)
        assert fn["parameters"].get("type") == "object"
