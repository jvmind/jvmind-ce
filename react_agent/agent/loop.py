"""ReAct 执行循环：run / run_stream 主入口与三条执行路径。"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from ..prompts import build_system_prompt
from ..summarizer import inject_summary_into_prompt, maybe_summarize
from .llm import _ToolsUnsupportedError, _use_function_calling
from .parsing import _RE_FINAL, AgentStep


class _LoopMixin:
    def reconfigure(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_iterations: Optional[int] = None,
        system_prompt_template: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ) -> None:
        """运行时热更新 LLM 参数。任何与连接相关的字段变了就重建 client。"""
        from openai import OpenAI
        rebuild = False
        if api_key is not None and api_key != self.api_key:
            self.api_key = api_key; rebuild = True
        if base_url is not None and base_url != self.base_url:
            self.base_url = base_url; rebuild = True
        if model is not None:
            self.model = model
        if temperature is not None:
            self.temperature = float(temperature)
        if max_iterations is not None:
            self.max_iterations = int(max_iterations)
        if system_prompt_template is not None:
            self.system_prompt_template = system_prompt_template
        if system_prompt_extra is not None:
            self.system_prompt_extra = system_prompt_extra
        if rebuild:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url) if self.api_key else None

    # ---------- 对外主入口 ----------
    def run(self, session_id: str, user_input: str, lang: str = "") -> Tuple[str, List[AgentStep]]:
        """跑一次完整的 ReAct 循环，返回 (最终回答, 轨迹)"""
        # 持久化用户消息
        self.memory.append_message(session_id, "user", user_input)

        use_fc = _use_function_calling() and not self._fc_unsupported

        get_prompt_facts = getattr(self.memory, "get_prompt_facts", self.memory.get_facts)
        facts = get_prompt_facts(session_id)
        system_prompt = build_system_prompt(
            tool_names=self.tools.names(),
            tool_descriptions=self.tools.describe(),
            facts=facts,
            template=self.system_prompt_template,
            extra=self.system_prompt_extra,
            lang=lang,
            function_calling=use_fc,
        )

        # 构造给 LLM 的对话历史（只取近 N 条以控制上下文）
        history = self.memory.get_messages(session_id)
        # 截断：保留最近 20 轮（user+assistant 共 40 条）
        history = history[-40:]

        if use_fc:
            try:
                return self._run_tools(session_id, system_prompt, history)
            except _ToolsUnsupportedError:
                self._fc_unsupported = True
                system_prompt = build_system_prompt(
                    tool_names=self.tools.names(),
                    tool_descriptions=self.tools.describe(),
                    facts=facts,
                    template=self.system_prompt_template,
                    extra=self.system_prompt_extra,
                    lang=lang,
                    function_calling=False,
                )

        scratchpad = ""  # ReAct 推理过程的草稿
        steps: List[AgentStep] = []

        for i in range(self.max_iterations):
            messages = [{"role": "system", "content": system_prompt}] + history
            if scratchpad:
                # 用 user 角色把当前的推理草稿喂回去
                messages.append({
                    "role": "user",
                    "content": (
                        f"以下是你已有的推理与观察，请继续下一步（必须以 'Thought:' 开头）：\n\n{scratchpad}"
                    ),
                })

            text = self._chat(messages, stop=["Observation:"])
            step = self._parse(text)

            # 最终回答
            if step.final_answer is not None:
                steps.append(step)
                self.memory.append_message(session_id, "assistant", step.final_answer)
                return step.final_answer, steps

            if not step.action:
                # 模型没给出 Action，也没 Final Answer → 提示检查技能
                hint = ""
                if self._skill_names:
                    hint = f"Please check if any skills are applicable / 请检查技能是否适用: {', '.join(sorted(self._skill_names))}"
                fallback = f"(No valid Action from model. {hint})" if hint else "(No valid Action from model.)"
                clean_fallback = self._strip_think(text)
                try:
                    step.final_answer = self._repair_final_answer(messages, text).strip() or clean_fallback or fallback
                except Exception:  # noqa: BLE001
                    step.final_answer = clean_fallback or fallback
                steps.append(step)
                self.memory.append_message(session_id, "assistant", step.final_answer)
                return step.final_answer, steps

            obs = self._execute_tool(session_id, step.action, step.action_input)
            if step.action != "remember":
                self._remember_tool_observation(session_id, step.action, step.action_input, obs)
            step.observation = obs
            steps.append(step)

            scratchpad += (
                f"Thought: {step.thought}\n"
                f"Action: {step.action}\n"
                f"Action Input: {step.action_input}\n"
                f"Observation: {obs}\n"
            )

        # 达到上限仍未给出 Final Answer
        forced = "(Max iterations reached, answering based on available information.)\n" + scratchpad
        # 让模型在不调用工具的情况下收尾
        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": f"Max iterations reached. Please give Final Answer based on the reasoning below / 已达到最大迭代次数，请基于以下推理直接给出 Final Answer：\n{scratchpad}"},
        ]
        final_text = self._chat(messages)
        m = _RE_FINAL.search(final_text)
        final = m.group(1).strip() if m else final_text.strip()
        self.memory.append_message(session_id, "assistant", final)
        steps.append(AgentStep(thought="(timeout)", final_answer=final))
        return final, steps

    def _run_tools(
        self, session_id: str, system_prompt: str, history: List[Dict[str, Any]]
    ) -> Tuple[str, List[AgentStep]]:
        """非流式 function-calling 循环（供 `run` 使用）。复用流式 tool 调用并聚合。"""
        tools = self.tools.to_openai_tools()
        convo: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}] + list(history)
        steps: List[AgentStep] = []

        for _iter in range(self.max_iterations):
            calls: List[Dict[str, str]] = []
            content_full = ""
            for ev in self._chat_stream_tools(convo, tools):
                if ev.get("kind") == "tool_calls":
                    calls = ev["calls"]
                elif ev.get("kind") == "finish":
                    content_full = ev.get("content", "")

            if not calls:
                final = (content_full or "").strip() or "(No reply from model.) / (模型未返回内容。)"
                self.memory.append_message(session_id, "assistant", final)
                steps.append(AgentStep(final_answer=final))
                return final, steps

            convo.append({
                "role": "assistant",
                "content": content_full or None,
                "tool_calls": [
                    {
                        "id": c.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c.get("arguments", "") or "{}"},
                    }
                    for i, c in enumerate(calls)
                ],
            })
            for i, c in enumerate(calls):
                name = c["name"]
                arg = self._toolcall_to_arg(name, c.get("arguments", ""))
                obs = self._execute_tool(session_id, name, arg)
                if name != "remember":
                    self._remember_tool_observation(session_id, name, arg, obs)
                steps.append(AgentStep(action=name, action_input=arg, observation=obs))
                convo.append({
                    "role": "tool",
                    "tool_call_id": c.get("id") or f"call_{i}",
                    "content": obs,
                })

        # 达到上限：不带 tools 强制收尾
        convo.append({
            "role": "user",
            "content": (
                "Max iterations reached. Please answer now based on the information above. / "
                "已达到最大迭代次数，请基于以上信息直接给出最终回答。"
            ),
        })
        final_text = self._chat(convo)
        final = self._strip_think(final_text) or "(No reply from model.) / (模型未返回内容。)"
        self.memory.append_message(session_id, "assistant", final)
        steps.append(AgentStep(thought="(timeout)", final_answer=final))
        return final, steps

    # ---------- 流式入口 ----------
    def run_stream(
        self, session_id: str, user_input: str, llm_input: Optional[str] = None, lang: str = "", should_stop: Optional[Callable[[], bool]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """流式版本，逐事件 yield：

        事件类型：
          {"type": "user",      "content": str}            刚记录的用户消息
          {"type": "token",     "phase": "reason"|"final", "content": str}
          {"type": "step",      "step": {...}}             一步完整 ReAct（含 observation）
          {"type": "final",     "content": str}            最终回答（已落盘）
          {"type": "fact_added","content": str}
          {"type": "error",     "content": str}
          {"type": "done"}

        优先使用原生 function-calling（结构化 tool_calls），彻底避免靠文本标记
        解析最终答案带来的脆弱性。若服务端不支持 tools，则自动降级到文本 ReAct。
        """
        self.memory.append_message(session_id, "user", user_input)
        yield {"type": "user", "content": user_input}

        use_fc = _use_function_calling() and not self._fc_unsupported

        get_prompt_facts = getattr(self.memory, "get_prompt_facts", self.memory.get_facts)
        facts = get_prompt_facts(session_id)
        system_prompt = build_system_prompt(
            tool_names=self.tools.names(),
            tool_descriptions=self.tools.describe(),
            facts=facts,
            template=self.system_prompt_template,
            extra=self.system_prompt_extra,
            lang=lang,
            function_calling=use_fc,
        )

        system_prompt = inject_summary_into_prompt(
            system_prompt, session_id, self.memory,
        )

        history = self.memory.get_messages(session_id)[-40:]
        if llm_input and history and history[-1].get("role") == "user":
            history[-1] = {"role": "user", "content": llm_input}

        try:
            if use_fc:
                try:
                    yield from self._run_stream_tools(session_id, system_prompt, history, should_stop=should_stop)
                    return
                except _ToolsUnsupportedError:
                    # 服务端不支持 tools：本进程后续都走文本路径，并立即降级本次请求。
                    self._fc_unsupported = True
                    # 重建不含工具调用指令的文本 ReAct 系统提示。
                    system_prompt = inject_summary_into_prompt(
                        build_system_prompt(
                            tool_names=self.tools.names(),
                            tool_descriptions=self.tools.describe(),
                            facts=facts,
                            template=self.system_prompt_template,
                            extra=self.system_prompt_extra,
                            lang=lang,
                            function_calling=False,
                        ),
                        session_id,
                        self.memory,
                    )
            yield from self._run_stream_text(session_id, system_prompt, history, should_stop=should_stop)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            yield {"type": "error", "content": err}
            yield {"type": "done"}
        finally:
            try:
                maybe_summarize(
                    session_id,
                    self.memory,
                    lambda msgs: self._chat(msgs),
                )
            except Exception:  # noqa: BLE001
                pass

    def _run_stream_tools(
        self, session_id: str, system_prompt: str, history: List[Dict[str, Any]], should_stop: Optional[Callable[[], bool]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """使用原生 function-calling 的流式循环。

        reasoning_content -> phase=reason（思考框）；content -> phase=final（气泡）；
        tool_calls -> 执行工具并把结果回填进对话，进入下一轮。模型不再返回 tool_calls
        时，content 即最终答案，无需任何文本标记。
        """
        tools = self.tools.to_openai_tools()
        convo: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}] + list(history)
        content_full = ""
        saved_partial = False

        def _save_partial():
            nonlocal saved_partial
            if saved_partial:
                return
            final = (content_full or "").strip()
            if final:
                try:
                    self.memory.append_message(session_id, "assistant", final)
                    saved_partial = True
                except Exception:
                    pass

        try:
            for _iter in range(self.max_iterations):
                if should_stop and should_stop():
                    final = content_full.strip() or "(Generation stopped by user.) / (生成已被用户停止。)"
                    _mid = self.memory.append_message(session_id, "assistant", final)
                    saved_partial = True
                    yield {"type": "final", "content": final, "message_id": _mid}
                    yield {"type": "done", "message_id": _mid}
                    return
                calls: List[Dict[str, str]] = []
                content_full = ""
                try:
                    for ev in self._chat_stream_tools(convo, tools):
                        kind = ev.get("kind")
                        if kind == "reason":
                            yield {"type": "token", "phase": "reason", "content": ev["text"]}
                        elif kind == "final":
                            content_full += ev.get("text", "")
                            yield {"type": "token", "phase": "final", "content": ev["text"]}
                        elif kind == "tool_calls":
                            calls = ev["calls"]
                        elif kind == "finish":
                            content_full = ev.get("content", "")
                except (GeneratorExit, BaseException):
                    _save_partial()
                    raise

                # ---- 无工具调用：content 即最终答案 ----
                if not calls:
                    final = (content_full or "").strip()
                    if not final:
                        final = "(No reply from model.) / (模型未返回内容。)"
                    _mid = self.memory.append_message(session_id, "assistant", final)
                    saved_partial = True
                    yield {"type": "final", "content": final, "message_id": _mid}
                    yield {"type": "done", "message_id": _mid}
                    return

                # ---- 有工具调用：执行并回填 ----
                convo.append({
                    "role": "assistant",
                    "content": content_full or None,
                    "tool_calls": [
                        {
                            "id": c.get("id") or f"call_{i}",
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c.get("arguments", "") or "{}"},
                        }
                        for i, c in enumerate(calls)
                    ],
                })
                for i, c in enumerate(calls):
                    name = c["name"]
                    arg = self._toolcall_to_arg(name, c.get("arguments", ""))
                    obs = self._execute_tool(session_id, name, arg)
                    if name != "remember":
                        self._remember_tool_observation(session_id, name, arg, obs)
                    try:
                        yield {"type": "step", "step": {
                            "thought": "",
                            "action": name,
                            "action_input": arg,
                            "observation": obs,
                            "final_answer": None,
                        }}
                    except (GeneratorExit, BaseException):
                        _save_partial()
                        raise
                    if name == "remember":
                        try:
                            yield {"type": "fact_added", "content": arg}
                        except (GeneratorExit, BaseException):
                            _save_partial()
                            raise
                    convo.append({
                        "role": "tool",
                        "tool_call_id": c.get("id") or f"call_{i}",
                        "content": obs,
                    })

            # ---- 达到迭代上限：不带 tools 强制收尾 ----
            convo.append({
                "role": "user",
                "content": (
                    "Max iterations reached. Please answer now based on the information above. / "
                    "已达到最大迭代次数，请基于以上信息直接给出最终回答。"
                ),
            })
            final_buf = ""
            for delta in self._chat_stream(convo):
                clean = re.sub(r'<think>.*?</think>', '', delta, flags=re.DOTALL)
                final_buf += clean
                if clean:
                    content_full += clean
                    try:
                        yield {"type": "token", "phase": "final", "content": clean}
                    except (GeneratorExit, BaseException):
                        _save_partial()
                        raise
            final = final_buf.strip() or "(No reply from model.) / (模型未返回内容。)"
            _mid = self.memory.append_message(session_id, "assistant", final)
            saved_partial = True
            yield {"type": "final", "content": final, "message_id": _mid}
            yield {"type": "done", "message_id": _mid}
        except (GeneratorExit, BaseException):
            _save_partial()
            raise

    def _run_stream_text(
        self, session_id: str, system_prompt: str, history: List[Dict[str, Any]], should_stop: Optional[Callable[[], bool]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """文本 ReAct 降级路径（服务端不支持 function-calling 时使用）。"""
        scratchpad = ""
        partial_final_buf = ""
        saved_partial = False

        def _save_partial():
            nonlocal saved_partial
            if saved_partial:
                return
            text = (partial_final_buf or "").strip()
            if text:
                try:
                    self.memory.append_message(session_id, "assistant", text)
                    saved_partial = True
                except Exception:
                    pass

        try:
            for _iter in range(self.max_iterations):
                if should_stop and should_stop():
                    final = partial_final_buf.strip() or scratchpad.strip() or "(Generation stopped by user.) / (生成已被用户停止。)"
                    _mid = self.memory.append_message(session_id, "assistant", final)
                    saved_partial = True
                    yield {"type": "final", "content": final, "message_id": _mid}
                    yield {"type": "done", "message_id": _mid}
                    return
                messages = [{"role": "system", "content": system_prompt}] + history
                if scratchpad:
                    messages.append({
                        "role": "user",
                        "content": (
                            "以下是你已有的推理与观察，请继续下一步"
                            "（必须以 'Thought:' 开头）：\n\n" + scratchpad
                        ),
                    })

                # ---- 流式接收本轮 LLM 输出 ----
                buf = ""
                final_content_pos = -1
                partial_final_buf = ""

                def _emit_delta(d: str, base: int):
                    pos = base
                    for part in re.split(r'(<think>|</think>)', d):
                        if part in ('<think>', '</think>'):
                            pos += len(part)
                            continue
                        if not part:
                            continue
                        seg_end = pos + len(part)
                        if final_content_pos < 0:
                            yield {"type": "token", "phase": "reason", "content": part}
                        elif seg_end <= final_content_pos:
                            yield {"type": "token", "phase": "reason", "content": part}
                        elif pos >= final_content_pos:
                            nonlocal partial_final_buf
                            partial_final_buf += part
                            yield {"type": "token", "phase": "final", "content": part}
                        else:
                            cut = final_content_pos - pos
                            if part[:cut]:
                                yield {"type": "token", "phase": "reason", "content": part[:cut]}
                            if part[cut:]:
                                partial_final_buf += part[cut:]
                                yield {"type": "token", "phase": "final", "content": part[cut:]}
                        pos = seg_end

                try:
                    for delta in self._chat_stream(messages, stop=["Observation:"]):
                        base = len(buf)
                        buf += delta
                        if final_content_pos < 0:
                            m = _RE_FINAL.search(buf)
                            if m:
                                final_content_pos = m.start(1)
                        yield from _emit_delta(delta, base)
                except (GeneratorExit, BaseException):
                    _save_partial()
                    raise

                step = self._parse(buf)

                if step.final_answer is not None:
                    _mid = self.memory.append_message(session_id, "assistant", step.final_answer)
                    saved_partial = True
                    try:
                        yield {"type": "step", "step": {
                            "thought": step.thought,
                            "action": "", "action_input": "", "observation": "",
                            "final_answer": step.final_answer,
                        }}
                        yield {"type": "final", "content": step.final_answer, "message_id": _mid}
                        yield {"type": "done", "message_id": _mid}
                    except (GeneratorExit, BaseException):
                        pass
                    return

                if not step.action:
                    hint = ""
                    if self._skill_names:
                        hint = f"Please check if any skills are applicable / 请检查技能是否适用: {', '.join(sorted(self._skill_names))}"
                    clean_buf = re.sub(r'<think>.*?</think>', '', buf, flags=re.DOTALL)
                    fallback = clean_buf.strip() or (f"(No valid Action from model. {hint})" if hint else "(No valid Action from model.)")
                    final = fallback
                    _mid = self.memory.append_message(session_id, "assistant", final)
                    saved_partial = True
                    try:
                        yield {"type": "final", "content": final, "message_id": _mid}
                        yield {"type": "done", "message_id": _mid}
                    except (GeneratorExit, BaseException):
                        pass
                    return

                obs = self._execute_tool(session_id, step.action, step.action_input)
                if step.action != "remember":
                    self._remember_tool_observation(session_id, step.action, step.action_input, obs)
                step.observation = obs
                try:
                    yield {"type": "step", "step": {
                        "thought": step.thought,
                        "action": step.action,
                        "action_input": step.action_input,
                        "observation": obs,
                        "final_answer": None,
                    }}
                except (GeneratorExit, BaseException):
                    _save_partial()
                    raise
                if step.action == "remember":
                    try:
                        yield {"type": "fact_added", "content": step.action_input}
                    except (GeneratorExit, BaseException):
                        _save_partial()
                        raise

                scratchpad += (
                    f"Thought: {step.thought}\n"
                    f"Action: {step.action}\n"
                    f"Action Input: {step.action_input}\n"
                    f"Observation: {obs}\n"
                )

            # ---- 达到迭代上限：让模型直接收尾 ----
            messages = [
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content":
                 "Max iterations reached. Please give Final Answer based on the reasoning below / 已达到最大迭代次数，请基于以下推理直接给出 Final Answer：\n" + scratchpad},
            ]
            buf = ""
            for delta in self._chat_stream(messages):
                buf += delta
                clean = re.sub(r'<think>.*?</think>', '', delta, flags=re.DOTALL)
                if clean:
                    partial_final_buf += clean
                    try:
                        yield {"type": "token", "phase": "final", "content": clean}
                    except (GeneratorExit, BaseException):
                        _save_partial()
                        raise
            final_clean = re.sub(r'<think>.*?</think>', '', buf, flags=re.DOTALL).strip()
            m = _RE_FINAL.search(final_clean)
            final = m.group(1).strip() if m else final_clean
            _mid = self.memory.append_message(session_id, "assistant", final)
            saved_partial = True
            yield {"type": "final", "content": final, "message_id": _mid}
            yield {"type": "done", "message_id": _mid}
        except (GeneratorExit, BaseException):
            _save_partial()
            raise
