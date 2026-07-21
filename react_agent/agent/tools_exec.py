"""工具调度：skill 加载、工具执行、function-call 参数转换、observation 记忆。"""
from __future__ import annotations

import json
from typing import Any, Dict, List


class _ToolsExecMixin:
    # ---------- 工具调度 ----------
    def load_skills(self, skills: List[dict]) -> None:
        """将用户 skill 注册为动态 Tool。"""
        from ..tools import Tool
        self._skill_names.clear()
        for s in skills:
            name = s.get("name", "").strip()
            if not name or self.tools.get(name):
                continue
            self._skill_names.add(name)
            instruction = s.get("instruction", "")
            desc = s.get("description", "")
            # 从 instruction 中提取关键用途描述，追加到 description 帮助 LLM 判断匹配
            if instruction:
                hint_text = instruction[:80].strip()
                if hint_text:
                    desc = f"{desc}\n  用途: {hint_text}" if desc else hint_text
            hint = s.get("args_hint", "input")
            self.tools.register(Tool(
                name=name,
                description=desc,
                args_hint=hint,
                func=lambda arg, instr=instruction, n=name: (
                    f"[Skill: {n}]\n{instr}\n\n---\n用户输入: {arg}"
                ),
            ))

    def _remember_tool_observation(self, session_id: str, name: str, arg: str, observation: str) -> None:
        if not hasattr(self.memory, "set_context_fact"):
            return
        text = str(observation or "").strip()
        if not text:
            return
        if len(text) > 1200:
            text = text[:1200] + "..."
        self.memory.set_context_fact(
            session_id,
            "last_tool",
            f"Last tool observation: tool={name}, input={arg}, observation={text}",
        )

    def _execute_tool(self, session_id: str, name: str, arg: str) -> str:
        if name == "remember":
            self.memory.add_fact(session_id, arg)
            return f"已记入长期记忆: {arg} / Saved to long-term memory: {arg}"
        if name == "query_gc_events":
            from ..gc_analyzer import query_events as _qev
            try:
                args = json.loads(arg) if arg else {}
            except Exception:
                args = {}
            return _qev(
                self.memory, session_id,
                report_id=str(args.get("report_id", "")).strip(),
                gc_id=args.get("gc_id"),
                category=args.get("category"),
                cause=args.get("cause"),
                time_start=args.get("time_start"),
                time_end=args.get("time_end"),
                duration_min=args.get("duration_min"),
                limit=int(args.get("limit") or 20),
                offset=int(args.get("offset") or 0),
            )
        if name == "read_gc_report":
            from ..gc_analyzer import read_gc_report_tool
            return read_gc_report_tool(self.memory, session_id, arg)
        if name == "read_jstack_report":
            from ..jstack_analyzer import read_jstack_report_tool
            return read_jstack_report_tool(self.memory, session_id, arg)
        if name == "analyze_specific_thread":
            from ..jstack_analyzer import analyze_specific_thread_tool
            return analyze_specific_thread_tool(self.memory, session_id, arg)
        if name == "analyze_gc_log":
            from ..gc_analyzer import analyze as _gc_analyze, summary_for_llm as _gc_summary
            from ..memory.uploads import get_uploaded_text
            fid = (arg or "").strip().split()[0].strip(".,;:`\"'")
            text = get_uploaded_text(self.memory, fid)
            if not text:
                return (f"No log found for file_id={fid} in this session. It may have expired; please re-upload.\n"
                        f"当前会话未找到 file_id={fid} 对应的日志，原始文件可能已过期，请重新上传。")
            try:
                stats = _gc_analyze(text)
            except Exception as e:
                return f"[Parse Error / 解析失败] {type(e).__name__}: {e}"
            return _gc_summary(stats)
        if name == "analyze_jstack":
            from ..jstack_analyzer import (
                parse_jstack as _js_parse,
                compute_stats as _js_stats,
                summary_for_llm as _js_summary,
            )
            from ..memory.uploads import get_uploaded_text
            fid = (arg or "").strip().split()[0].strip(".,;:`\"'")
            text = get_uploaded_text(self.memory, fid)
            if not text:
                return (f"No jstack file found for file_id={fid} in this session. It may have expired; please re-upload.\n"
                        f"当前会话未找到 file_id={fid} 对应的 jstack 文件，原始文件可能已过期，请重新上传。")
            try:
                parsed = _js_parse(text)
                stats = _js_stats(parsed)
            except Exception as e:
                return f"[Parse Error / 解析失败] {type(e).__name__}: {e}"
            return _js_summary(stats)
        if name.startswith("mat_"):
            from ..mat_tools import dispatch_mat_tool
            return dispatch_mat_tool(self.memory, session_id, name, arg)
        tool = self.tools.get(name)
        if not tool:
            return f"[错误] 未知工具 '{name}'，可用工具: {self.tools.names() + ['remember']} / [Error] Unknown tool '{name}', available: {self.tools.names() + ['remember']}"
        return tool.run(arg)

    def _toolcall_to_arg(self, name: str, arguments: str) -> str:
        """Convert a function-call's JSON arguments string into the single
        string argument the tool dispatcher (`_execute_tool`) expects."""
        try:
            data = json.loads(arguments) if arguments and arguments.strip() else {}
        except (ValueError, TypeError):
            # Model emitted a bare string instead of JSON; use it as-is.
            return (arguments or "").strip()
        if not isinstance(data, dict):
            return str(data)
        if name == "remember":
            v = data.get("fact", data.get("input", ""))
            return "" if v is None else str(v)
        if name == "query_gc_events":
            # Multi-key JSON object; pass through to _execute_tool for parsing
            return arguments if arguments else "{}"
        tool = self.tools.get(name)
        if tool is not None:
            return tool.arg_from_call(data)
        # Unknown tool: best-effort join of values.
        vals = [str(v) for v in data.values() if v is not None and str(v) != ""]
        return ",".join(vals)
