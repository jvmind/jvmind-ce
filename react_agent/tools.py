"""工具系统：定义 Tool 抽象 + 注册表 + 内置基础工具"""
from __future__ import annotations

import os
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


def _default_parameters() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input to the tool / 传给工具的输入"}
        },
        "required": ["input"],
    }


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[[str], str]
    args_hint: str = "input: str"
    # OpenAI function-calling JSON Schema for this tool's arguments.
    parameters: Dict[str, Any] = field(default_factory=_default_parameters)

    def run(self, arg: str) -> str:
        try:
            return str(self.func(arg))
        except Exception as e:  # noqa: BLE001
            return f"[Tool Error] {type(e).__name__}: {e}"

    def arg_from_call(self, args: Dict[str, Any]) -> str:
        """Convert a parsed function-call arguments dict into the single string
        argument the underlying tool func expects.

        - single-property schema -> that property's value
        - multi-property schema  -> values joined by ',' in declared order
        """
        props = list((self.parameters or {}).get("properties", {}).keys())
        if not props:
            return ""
        if len(props) == 1:
            v = (args or {}).get(props[0], "")
            return "" if v is None else str(v)
        parts = []
        for k in props:
            v = (args or {}).get(k)
            if v is not None and str(v) != "":
                parts.append(str(v))
        return ",".join(parts)


@dataclass
class ToolRegistry:
    tools: Dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def list(self) -> List[Tool]:
        return list(self.tools.values())

    def describe(self) -> str:
        """以 ReAct prompt 风格描述所有工具"""
        lines = []
        for t in self.tools.values():
            lines.append(f"- {t.name}({t.args_hint}): {t.description}")
        return "\n".join(lines)

    def names(self) -> List[str]:
        return list(self.tools.keys())

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """Export all tools (plus the built-in `remember`) as OpenAI
        function-calling tool specs."""
        specs: List[Dict[str, Any]] = []
        for t in self.tools.values():
            specs.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or _default_parameters(),
                },
            })
        specs.append({
            "type": "function",
            "function": {
                "name": "remember",
                "description": (
                    "Save an important user fact or preference to long-term memory "
                    "(name, likes, long-term goals, etc.). / "
                    "把重要的用户事实或偏好（姓名、喜好、长期目标等）写入长期记忆。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact to remember / 要记住的事实",
                        }
                    },
                    "required": ["fact"],
                },
            },
        })
        return specs


# ---------- 内置工具实现 ----------

def current_time(_: str = "") -> str:
    """返回当前 UTC 时间（显式标记 UTC，避免跨时区歧义）"""
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M:%S UTC (%A)")


def default_tools() -> ToolRegistry:
    reg = ToolRegistry()
    # GC 日志读取工具（已删除 gc_tool / read_gc_report_standalone — DB-only 读取路径，
    # 见 react_agent/agent/tools_exec.py:_execute_tool 分支处理）。
    reg.register(Tool(
        name="read_gc_report",
        description=(
            "Read existing GC analysis reports in current session. "
            "Input 'list' to see all reports (with ID, filename, event count, total pause, etc.), "
            "or input report_id to see detailed stats (pause distribution by type, Top slow events) "
            "and AI diagnosis.\n"
            "读取当前会话中已有的 GC 分析报告。"
            "输入 'list' 查看所有报告的列表（含 ID、文件名、事件数、总停顿等），"
            "或输入 report_id 查看指定报告的详细统计（含按类型停顿分布、Top 慢事件）和 AI 诊断结论。"
        ),
        func=lambda _: "(Use format / 请使用: read_gc_report(<list|report_id>))",
        args_hint="list | report_id",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "'list' to list all reports, or a report_id for details. / 'list' 列出所有报告，或传 report_id 查看详情。",
                }
            },
            "required": ["query"],
        },
    ))
    reg.register(Tool(
        name="query_gc_events",
        description=(
            "Query specific GC events from a parsed report. "
            "Use this when the user asks about a particular subset of events "
            "(e.g. 'list all Full GC events', 'show GC events between minute 30 and 45', "
            "'events longer than 500ms'). "
            "Returns a compact list with event id, time, category, cause, duration, "
            "heap change. Filters: gc_id, category, cause (substring), "
            "time_start/time_end (seconds since JVM start), duration_min (ms). "
            "Default 20 events; pass offset/limit to paginate."
            " Use `read_gc_report(report_id)` for the high-level summary "
            "of a report; use `query_gc_events` for event-level drill-down.\n"
            "查询已落库 GC 报告中的具体事件。当用户想看某类事件子集时使用 "
            "（例如'列出所有 Full GC'、'第 30 到 45 分钟之间的事件'、"
            "'停顿超过 500ms 的事件'）。返回紧凑列表：事件 id/时间/类别/"
            "原因/停顿/堆变化。过滤器：gc_id, category, cause (子串), "
            "time_start/time_end (秒), duration_min (毫秒)。默认 20 条，"
            "可传 offset/limit 分页。"
            "想看报告整体摘要走 `read_gc_report(report_id)`，想看具体事件子集走 `query_gc_events`。"
        ),
        func=lambda _: "(Use format / 请使用: query_gc_events(<report_id>[, filters]))",
        args_hint="report_id[,gc_id][,category][,cause][,time_start][,time_end][,duration_min][,limit][,offset]",
        parameters={
            "type": "object",
            "properties": {
                "report_id": {"type": "string",
                              "description": "Report id from read_gc_report(list) / 从 read_gc_report(list) 拿到的报告 ID"},
                "gc_id": {"type": "integer",
                          "description": "Optional: exact GC event id / 可选：精确事件 ID"},
                "category": {"type": "string",
                             "enum": ["Young", "Full", "Mixed", "Concurrent",
                                      "InitialMark", "Remark", "Cleanup",
                                      "ZGC", "Shenandoah", "Other"],
                             "description": "Optional: filter by event category / 可选：按类别过滤"},
                "cause": {"type": "string",
                          "description": "Optional: substring match against cause / 可选：原因子串匹配"},
                "time_start": {"type": "number",
                               "description": "Optional: events with t >= this / 可选：t >= 此值"},
                "time_end": {"type": "number",
                             "description": "Optional: events with t <= this / 可选：t <= 此值"},
                "duration_min": {"type": "number",
                                 "description": "Optional: events with dur >= this (ms) / 可选：dur >= 此值（毫秒）"},
                "limit": {"type": "integer", "default": 20, "maximum": 100,
                          "description": "Page size (default 20, max 100) / 每页条数，默认 20，最大 100"},
                "offset": {"type": "integer", "default": 0,
                           "description": "Skip N matched events / 跳过前 N 条"},
            },
            "required": ["report_id"],
        },
    ))
    # jstack 工具（已删除 jstack_tool — DB-only 读取路径，见
    # react_agent/agent/tools_exec.py:_execute_tool 分支处理）
    reg.register(Tool(
        name="read_jstack_report",
        description=(
            "Read existing jstack analysis reports in current session. "
            "Input 'list' to see all reports, or input report_id to see detailed stats and AI diagnosis.\n"
            "读取当前会话中已有的 jstack 分析报告。"
            "输入 'list' 查看所有报告的列表，或输入 report_id 查看指定报告的详细统计和 AI 诊断结论。"
        ),
        func=lambda _: "(Use format / 请使用: read_jstack_report(<list|report_id>))",
        args_hint="list | report_id",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "'list' to list all reports, or a report_id for details. / 'list' 列出所有报告，或传 report_id 查看详情。",
                }
            },
            "required": ["query"],
        },
    ))
    reg.register(Tool(
        name="analyze_specific_thread",
        description=(
            "Analyze a specific thread's full stack trace from jstack, including its state, "
            "stack frames, lock holding/waiting relationships, etc. "
            "Input format: <file_id or report_id>,<thread_name or nid>. "
            "Example: 'fid_xxx,http-nio-8080-exec-3' or 'rid_xxx,http-nio-8080-exec-3'\n"
            "分析 jstack 中指定线程的完整堆栈，包括其状态、栈帧、锁持有/等待关系等。"
            "输入格式：<file_id 或 report_id>,<线程名或nid>。"
            "示例：'fid_xxx,http-nio-8080-exec-3' 或 'rid_xxx,http-nio-8080-exec-3'"
        ),
        func=lambda _: "(Use format / 请使用: analyze_specific_thread(<file_id>,<thread_id>))",
        args_hint="file_id_or_report_id,thread_name_or_nid",
        parameters={
            "type": "object",
            "properties": {
                "file_id_or_report_id": {
                    "type": "string",
                    "description": "file_id or report_id of the jstack dump / jstack 转储的 file_id 或 report_id",
                },
                "thread_name_or_nid": {
                    "type": "string",
                    "description": "thread name or nid to drill into / 要钻取的线程名或 nid",
                },
            },
            "required": ["file_id_or_report_id", "thread_name_or_nid"],
        },
    ))
    # ---- JVM 参数校验工具 ----
    from .jvm_flags import validate_jvm_args as _validate_jvm_args
    reg.register(Tool(
        name="validate_jvm_args",
        description=(
            "Validate JVM command-line flags against the official reference for a specific JDK version. "
            "Before recommending JVM parameters to a user, call this to verify each flag exists "
            "and matches the correct type. "
            "Input: JDK major version + comma-separated flag names, optionally with =value. "
            "Example: '17,G1HeapRegionSize=4m,MaxHeapSize=8g,UseG1GC'. / "
            "对照指定 JDK 版本的官方参考校验 JVM 命令行参数。"
            "在向用户推荐 JVM 参数之前调用此工具验证每个 flag 是否存在以及类型匹配。"
            "输入：JDK 主版本号 + 逗号分隔的参数名，可附带 =值。"
            "例如：'17,G1HeapRegionSize=4m,MaxHeapSize=8g,UseG1GC'。"
        ),
        func=_validate_jvm_args,
        args_hint="jdk_version,flag1[=value1][,flag2[=value2],...]",
        parameters={
            "type": "object",
            "properties": {
                "jdk_version": {
                    "type": "integer",
                    "description": "JDK major version (8, 11, 17, 21, 25) / JDK 主版本号",
                },
                "flags": {
                    "type": "string",
                    "description": "Comma-separated flag names, optionally with =value. / 逗号分隔的参数名，可附带 =值",
                },
            },
            "required": ["jdk_version", "flags"],
        },
    ))
    # ---- MAT heapdump tools (dispatched via _execute_tool mat_ prefix branch) ----
    def _mat_stub(_: str) -> str:
        return "(mat_* tools are dispatched by the agent / mat_* 工具由 Agent 内部调度)"
    _mat_common_report = {
        "type": "string",
        "description": "Heapdump report_id (e.g. hd_xxx) / 堆转储报告 ID",
    }
    mat_specs = [
        ("mat_overview",
         "Show heap overview (used/committed/live heap, object/class/classloader counts, JDK version, leak suspect count). Always call this first to understand a heapdump. / "
         "展示堆总览（已用/已提交/存活堆、对象/类/类加载器数量、JDK 版本、泄漏嫌疑数量）。分析 heapdump 时先调用本工具。",
         "report_id",
         {"report_id": _mat_common_report},
         ["report_id"]),
        ("mat_histogram",
         "Class histogram sorted by shallow/retained/count. Top-N by default; pass top to override. objectSet reuses a prior OQL/RS result. / "
         "按 shallow/retained/count 排序的类直方图，默认 Top-20。objectSet 可复用先前 OQL/RS 结果。",
         "report_id[,top,sort,objectSet]",
         {"report_id": _mat_common_report,
          "top": {"type": "integer", "description": "N to show (default 20, max 50) / 显示条数，默认 20，最大 50"},
          "sort": {"type": "string", "description": "shallow | retained | count (default retained)"},
          "objectSet": {"type": "string", "description": "rs-xxx result set id / 结果集 id"}},
         ["report_id"]),
        ("mat_dominator",
         "Dominator tree: list biggest retained-heap dominators, drill down by passing parent objectId. / "
         "支配树：列出 retained 最大的支配者，传 parent=objectId 可下钻。",
         "report_id[,parent,top]",
         {"report_id": _mat_common_report,
          "parent": {"type": "string", "description": "ROOT or objectId (default ROOT)"},
          "top": {"type": "integer", "description": "N (default 20, max 50)"}},
         ["report_id"]),
        ("mat_threads",
         "List threads (name/state/daemon/frameCount), or drill into a thread's stack frames (pass thread=id) or frame locals (pass frame=idx). / "
         "列出线程（名/状态/daemon/帧数），或下钻到某线程的栈帧（传 thread=id）、某帧的局部变量（再传 frame=idx）。",
         "report_id[,thread,frame]",
         {"report_id": _mat_common_report,
          "thread": {"type": "string", "description": "thread id for frames / 线程 id（看栈帧）"},
          "frame": {"type": "integer", "description": "frame index for locals / 帧下标（看局部变量）"}},
         ["report_id"]),
        ("mat_threadlocals",
         "Inspect thread locals grouped by valueClass (default) or thread. Useful for finding thread-local leaks. / "
         "按 valueClass（默认）或 thread 分组查看 ThreadLocal，常用于发现 ThreadLocal 泄漏。",
         "report_id[,groupBy]",
         {"report_id": _mat_common_report,
          "groupBy": {"type": "string", "description": "valueClass | thread (default valueClass)"}},
         ["report_id"]),
        ("mat_object",
         "Inspect a single object (class, fields, statics, in/out refs, GC root). Required: object id. / "
         "检查单个对象（类、字段、静态字段、入/出引用、GC Root）。必须传 object id。",
         "report_id,objectId",
         {"report_id": _mat_common_report,
          "id": {"type": "integer", "description": "objectId from histogram/dominator/path"}},
         ["report_id", "id"]),
        ("mat_path2gc",
         "Shortest path from the object to a GC root (shows why an object is reachable and not collected). excludeWeakSoft defaults true. / "
         "对象到 GC Root 的最短路径（说明对象为何可达未回收）。excludeWeakSoft 默认 true。",
         "report_id,objectId[,excludeWeakSoft]",
         {"report_id": _mat_common_report,
          "object": {"type": "integer", "description": "objectId"},
          "excludeWeakSoft": {"type": "boolean", "description": "exclude weak/soft refs (default true)"}},
         ["report_id", "object"]),
        ("mat_oql",
         "Run an OQL query against the heap (slow!). Returns rows + a reusable resultSetId. Examples: \"SELECT * FROM com.example.Foo s\", \"SELECT s.id.toString() FROM java.lang.String s\". / "
         "执行 OQL 查询（较慢）。返回行和可复用的 resultSetId。示例：SELECT * FROM com.example.Foo s",
         "report_id,q[,limit,view,sort]",
         {"report_id": _mat_common_report,
          "q": {"type": "string", "description": "OQL query string / OQL 查询语句"},
          "limit": {"type": "integer", "description": "max rows (default 50, max 200)"},
          "view": {"type": "string", "description": "list | histogram (default list)"},
          "sort": {"type": "string", "description": "shallow | retained | count"}},
         ["report_id", "q"]),
        ("mat_leak_suspects",
         "Run LeakHunter (async) — returns leak suspects with retained size and description. SLOW: may take a minute. / "
         "运行 LeakHunter（异步），返回疑似泄漏点及 retained 大小与描述。慢查询，可能耗时数十秒。",
         "report_id",
         {"report_id": _mat_common_report},
         ["report_id"]),
        ("mat_diagnose_oom",
         "Run comprehensive OOM diagnosis (async) combining leak suspects + SQL-in-threads + dominator analysis. / "
         "综合 OOM 诊断（异步），聚合泄漏嫌疑、线程中的 SQL、支配树分析。",
         "report_id[,culpritPct,onlyRisky]",
         {"report_id": _mat_common_report,
          "culpritPct": {"type": "integer", "description": "retained % threshold (default 30)"},
          "onlyRisky": {"type": "boolean", "description": "only risky SQL (default true)"}},
         ["report_id"]),
        ("mat_connection_pools",
         "Diagnose DB connection pools (active/idle counts + leaked connections held > thresholdMs). / "
         "诊断数据库连接池（活跃/空闲数 + 持有超过 thresholdMs 的泄漏连接）。",
         "report_id[,thresholdMs]",
         {"report_id": _mat_common_report,
          "thresholdMs": {"type": "integer", "description": "leak threshold in ms (default 300000 = 5min)"}},
         ["report_id"]),
        ("mat_top_consumers",
         "Top memory consumers (biggest objects/classes by retained heap). / "
         "内存占用 Top（按 retained 排序的最大对象/类）。",
         "report_id",
         {"report_id": _mat_common_report},
         ["report_id"]),
        ("mat_sql_in_threads",
         "Find SQL statements (JDBC PreparedStatement) in thread stacks — useful for finding long-running queries at OOM. / "
         "在线程栈中找 SQL（JDBC PreparedStatement），用于定位 OOM 时还在执行的慢查询。",
         "report_id[,onlyRisky]",
         {"report_id": _mat_common_report,
          "onlyRisky": {"type": "boolean", "description": "only risky/unclosed (default true)"}},
         ["report_id"]),
    ]
    for name, desc, hint, props, req in mat_specs:
        reg.register(Tool(
            name=name,
            description=desc,
            func=_mat_stub,
            args_hint=hint,
            parameters={"type": "object", "properties": props, "required": req},
        ))
    return reg
