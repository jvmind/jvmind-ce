"""Tool builders wrapping legacy tools as LangChain StructuredTools with InjectedState."""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Literal, Optional

from langchain_core.tools import StructuredTool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Annotated

from ..tools import default_tools


# P0-3 fix (2026-07-09): 之前用 threading.local() 在 agent_node/tool_node 入口
# set session_id，tool 函数 fallback 读取。但 LangGraph ToolNode 可能在不同线程
# 上执行 tool 函数，thread-local 在 worker 线程上不可见；同样地，
# `set_tool_context(memory, sid)` 写入 gc_analyzer 的模块全局 dict，跨线程
# 也没有同步保护。两个并发请求的 tool 执行会发生 session A 用 session B 的
# memory/sid 的竞态。
#
# 新实现：完全依赖 InjectedState（ToolNode 在调用前注入 graph state 到 `state`
# 形参），`_sid(state)` 只从 state 读取。删除了 `_set_session_id` 与
# `set_tool_context` 调用。
def _sid(state: Any) -> str:
    if isinstance(state, dict):
        s = state.get("session_id", "")
        if s:
            return str(s)
    return ""


def _py_type(sch: Dict[str, Any]):
    return {"integer": int, "boolean": bool, "number": float}.get(sch.get("type", "string"), str)


# ---------------------------------------------------------------------------
# Schema builders
# ---------------------------------------------------------------------------

def _schema(fields: Dict[str, tuple], name: str) -> type[BaseModel]:
    """Build a Pydantic model with an InjectedState 'state' field (default=None so direct
    invocation without graph injection doesn't fail validation)."""
    f = dict(fields)
    f["state"] = (Annotated[Optional[dict], InjectedState], Field(default=None))
    # Build with module-level scope to avoid pickling issues
    annotations = {k: v[0] for k, v in f.items()}
    defaults = {}
    for k, v in f.items():
        if len(v) > 1:
            defaults[k] = v[1].default
    namespace = {"__annotations__": annotations, "model_config": ConfigDict(extra="allow")}
    namespace.update(defaults)
    m = type(name, (BaseModel,), namespace)
    return m


def _field(ptype, description: str, required: bool):
    if required:
        return (ptype, Field(description=description))
    return (Optional[ptype], Field(default=None, description=description))


# ---------------------------------------------------------------------------
# Tool builders
# ---------------------------------------------------------------------------

def _build_remember_tool(memory) -> StructuredTool:
    def remember(fact: str = "", state: dict = None) -> str:
        """Save an important user fact or preference to long-term memory."""
        memory.add_fact(_sid(state), fact)
        return f"已记入长期记忆: {fact}\nSaved to long-term memory: {fact}"
    schema = _schema({"fact": _field(str, "The fact to remember / 要记住的事实", True)}, "RememberArgs")
    return StructuredTool.from_function(
        func=remember, name="remember",
        description="Save an important user fact or preference to long-term memory. / 把重要的用户事实或偏好写入长期记忆。",
        args_schema=schema,
    )


def _build_current_time() -> StructuredTool:
    def current_time(state: dict = None) -> str:
        """Return current UTC time."""
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC (%A)")
    schema = _schema({}, "CurrentTimeArgs")
    return StructuredTool.from_function(
        func=current_time, name="current_time",
        description="Return current UTC time. / 返回当前 UTC 时间。",
        args_schema=schema,
    )


def _build_read_gc_report(memory) -> StructuredTool:
    def read_gc_report(query: str = "", state: dict = None) -> str:
        """Read existing GC analysis reports."""
        sid = _sid(state)
        from ..gc_analyzer import read_gc_report_tool
        # 直接传 memory/sid，不再依赖 set_tool_context 写入模块全局 dict
        # （P0-3 修复：避免跨线程 / 跨请求竞态）
        return read_gc_report_tool(memory, sid, str(query or ""))
    schema = _schema({"query": _field(str, "'list' or report_id", True)}, "ReadGCArgs")
    return StructuredTool.from_function(
        func=read_gc_report, name="read_gc_report",
        description="Read existing GC reports. 'list' or report_id. / 读取已有 GC 报告。",
        args_schema=schema,
    )


def _build_query_gc_events(memory) -> StructuredTool:
    def query_gc_events(
        report_id: str,
        gc_id: Optional[int] = None,
        category: Optional[str] = None,
        cause: Optional[str] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        duration_min: Optional[float] = None,
        limit: int = 20,
        offset: int = 0,
        state: dict = None,
    ) -> str:
        """Query specific GC events from a parsed report."""
        from ..gc_analyzer import query_events as _qev
        return _qev(
            memory, _sid(state), report_id=report_id,
            gc_id=gc_id, category=category, cause=cause,
            time_start=time_start, time_end=time_end,
            duration_min=duration_min, limit=limit, offset=offset,
        )
    schema = _schema({
        "report_id": _field(str, "Report id", True),
        "gc_id": _field(int, "Exact GC event id", False),
        "category": _field(
            Literal["Young", "Full", "Mixed", "Concurrent", "InitialMark",
                    "Remark", "Cleanup", "ZGC", "Shenandoah", "Other"],
            "Event category", False,
        ),
        "cause": _field(str, "Substring match against cause", False),
        "time_start": _field(float, "t >= this (seconds since JVM start)", False),
        "time_end": _field(float, "t <= this (seconds since JVM start)", False),
        "duration_min": _field(float, "dur >= this (ms)", False),
        "limit": _field(int, "Page size (default 20, max 100)", False),
        "offset": _field(int, "Skip N matched events", False),
    }, "QueryGCEventsArgs")
    return StructuredTool.from_function(
        func=query_gc_events, name="query_gc_events",
        description="Query specific GC events from a parsed report. "
                    "Filters: gc_id/category/cause/time_start/time_end/duration_min. "
                    "Returns compact event list."
                    " Use `read_gc_report(report_id)` for the high-level summary "
                    "of a report; use `query_gc_events` for event-level drill-down. / "
                    "查询已落库 GC 报告中的具体事件。"
                    "过滤器：gc_id/category/cause/time_start/time_end/duration_min。"
                    "返回紧凑事件列表。"
                    "想看报告整体摘要走 `read_gc_report(report_id)`，想看具体事件子集走 `query_gc_events`。",
        args_schema=schema,
    )


def _build_read_jstack_report(memory) -> StructuredTool:
    def read_jstack_report(query: str = "", state: dict = None) -> str:
        """Read existing jstack reports."""
        sid = _sid(state)
        from ..jstack_analyzer import read_jstack_report_tool
        return read_jstack_report_tool(memory, sid, str(query or ""))
    schema = _schema({"query": _field(str, "'list' or report_id", True)}, "ReadJStackArgs")
    return StructuredTool.from_function(
        func=read_jstack_report, name="read_jstack_report",
        description="Read existing jstack reports. 'list' or report_id. / 读取已有 jstack 报告。",
        args_schema=schema,
    )


def _build_analyze_specific_thread(memory) -> StructuredTool:
    def analyze_specific_thread(file_id_or_report_id: str = "", thread_name_or_nid: str = "",
                                state: dict = None) -> str:
        """Drill into a specific thread."""
        sid = _sid(state)
        from ..jstack_analyzer import analyze_specific_thread_tool
        arg = f"{file_id_or_report_id},{thread_name_or_nid}" if thread_name_or_nid else str(file_id_or_report_id or "")
        return analyze_specific_thread_tool(memory, sid, arg)
    schema = _schema({
        "file_id_or_report_id": _field(str, "file_id or report_id", True),
        "thread_name_or_nid": _field(str, "thread name or nid", True),
    }, "AnalyzeThreadArgs")
    return StructuredTool.from_function(
        func=analyze_specific_thread, name="analyze_specific_thread",
        description="Drill into a specific thread stack. / 分析指定线程堆栈。",
        args_schema=schema,
    )


def _build_generic_tool(t) -> StructuredTool:
    """Wrap a single- or multi-param legacy Tool using a Pydantic args_schema.

    The wrapper function receives typed kwargs from LangChain (one per
    Pydantic field) and forwards them positionally to the legacy Tool.run
    callable, which historically accepted a comma-joined string for
    multi-arg tools.
    """
    props = (t.parameters or {}).get("properties", {})
    required = set((t.parameters or {}).get("required", []) or [])
    keys = list(props.keys())
    run = t.run
    fields = {
        k: _field(_py_type(props[k]), props[k].get("description", ""), k in required)
        for k in keys
    }
    schema = _schema(fields, f"Args_{t.name}")

    def _invoke(state: dict = None, **kw):
        if len(keys) == 1:
            v = kw.get(keys[0], "")
            return run("" if v is None else str(v))
        parts = [str(kw[k]) for k in keys if kw.get(k) not in (None, "")]
        return run(",".join(parts))

    _invoke.__name__ = f"_invoke_{t.name}"
    return StructuredTool.from_function(
        func=_invoke, name=t.name, description=t.description, args_schema=schema,
    )


def _build_mat_tool(memory, name: str, desc: str, props: Dict, required: List[str]) -> StructuredTool:
    from ..mat_tools import dispatch_mat_tool
    keys = list(props.keys())
    req = set(required or [])
    fields = {
        k: _field(_py_type(props[k]), props[k].get("description", ""), k in req)
        for k in keys
    }
    schema = _schema(fields, f"Args_{name}")

    def _invoke(state: dict = None, **kw):
        sid = _sid(state)
        parts = [str(kw[k]) for k in keys if kw.get(k) not in (None, "")]
        arg_str = ",".join(parts)
        return dispatch_mat_tool(memory, sid, name, arg_str, state=state)

    _invoke.__name__ = f"_invoke_{name}"
    return StructuredTool.from_function(
        func=_invoke, name=name, description=desc, args_schema=schema,
    )


def _build_skill_tool(s: dict) -> StructuredTool:
    name = (s.get("name") or "").strip()
    ins = s.get("instruction", "")
    desc = s.get("description", "") or ""
    if ins and ins[:80]:
        desc = f"{desc}\n  用途: {ins[:80].strip()}" if desc else ins[:80]
    def _skill(input: str = "", state: dict = None) -> str:
        """Invoke a user-defined skill."""
        return f"[Skill: {name}]\n{ins}\n\n---\n用户输入: {input}"
    schema = _schema({"input": _field(str, "Input to the skill / 输入", True)}, f"SkillArgs_{name}")
    return StructuredTool.from_function(func=_skill, name=name, description=desc, args_schema=schema)


# ---------------------------------------------------------------------------
# MAT specs cache
# ---------------------------------------------------------------------------
_MAT_CACHE: List[tuple] = []
_reg = default_tools()
for _n, _t in _reg.tools.items():
    if _n.startswith("mat_"):
        _MAT_CACHE.append((_n, _t.description, _t.parameters.get("properties", {}), _t.parameters.get("required", [])))


def build_all_tools(memory, skill_defs: Optional[List[dict]] = None):
    tools: List[StructuredTool] = []
    for t in _reg.list():
        if t.name.startswith("mat_"):
            continue
        if t.name == "read_gc_report":
            tools.append(_build_read_gc_report(memory))
        elif t.name == "read_jstack_report":
            tools.append(_build_read_jstack_report(memory))
        elif t.name == "analyze_specific_thread":
            tools.append(_build_analyze_specific_thread(memory))
        elif t.name == "query_gc_events":
            tools.append(_build_query_gc_events(memory))
        else:
            tools.append(_build_generic_tool(t))
    for name, desc, props, req in _MAT_CACHE:
        tools.append(_build_mat_tool(memory, name, desc, props, req))
    tools.append(_build_remember_tool(memory))
    tools.append(_build_current_time())
    existing = {t.name for t in tools}
    if skill_defs:
        for s in skill_defs:
            nm = (s.get("name") or "").strip()
            if nm and nm not in existing:
                tools.append(_build_skill_tool(s))
                existing.add(nm)
    return tools
