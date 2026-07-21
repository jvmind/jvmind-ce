"""ReAct 输出解析：正则、AgentStep、文本解析与修复。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional


# 用于解析模型输出的正则
_RE_THOUGHT = re.compile(r"Thought\s*[:：]\s*(.*?)(?=\n(?:Action|Final Answer)\s*[:：]|\Z)", re.S | re.I)
_RE_ACTION = re.compile(r"Action\s*[:：]\s*([^\n]+)", re.I)
_RE_ACTION_INPUT = re.compile(r"Action Input\s*[:：]\s*(.*?)(?=\nObservation\s*[:：]|\nThought\s*[:：]|\Z)", re.S | re.I)
_RE_FINAL = re.compile(r"(?:^|\n)\s*Final Answer\s*[:：]\s*(.*)", re.S | re.I)


@dataclass
class AgentStep:
    thought: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""
    final_answer: Optional[str] = None


class _ParsingMixin:
    # ---------- 解析 ----------
    def _parse(self, text: str) -> AgentStep:
        # 提取 <think> 内容（DeepSeek reasoning_content 可能包含 ReAct 输出）
        think_content = ""
        think_match = re.search(r'<think>(.*?)</think>', text, flags=re.DOTALL)
        if think_match:
            think_content = think_match.group(1)

        # 剥离 <think> 标签得到干净文本
        clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

        step = AgentStep()
        # Final Answer 优先
        m_final = _RE_FINAL.search(clean)
        m_thought = _RE_THOUGHT.search(clean)
        if m_thought:
            step.thought = m_thought.group(1).strip()
        if m_final:
            step.final_answer = m_final.group(1).strip()
            return step
        m_action = _RE_ACTION.search(clean)
        m_input = _RE_ACTION_INPUT.search(clean)
        if m_action:
            step.action = m_action.group(1).strip().strip("`").strip()
        if m_input:
            step.action_input = m_input.group(1).strip().strip("`").strip()

        # 回退：clean 中无 Final Answer 也无 Action，尝试从 <think> 内容解析
        if not m_final and not m_action and think_content:
            m_final2 = _RE_FINAL.search(think_content)
            m_thought2 = _RE_THOUGHT.search(think_content)
            if m_thought2:
                step.thought = step.thought or m_thought2.group(1).strip()
            if m_final2:
                step.final_answer = m_final2.group(1).strip()
                return step
            m_action2 = _RE_ACTION.search(think_content)
            m_input2 = _RE_ACTION_INPUT.search(think_content)
            if m_action2:
                step.action = m_action2.group(1).strip().strip("`").strip()
            if m_input2:
                step.action_input = m_input2.group(1).strip().strip("`").strip()

        return step

    def _strip_think(self, text: str) -> str:
        return re.sub(r'<think>.*?</think>', '', text or '', flags=re.DOTALL).strip()

    def _repair_final_answer(self, messages: List[Dict[str, str]], raw_text: str) -> str:
        """Ask the model to turn an unstructured reply into a complete Final Answer."""
        repair_messages = [
            *messages,
            {"role": "assistant", "content": raw_text or ""},
            {
                "role": "user",
                "content": (
                    "Your previous output did not include a valid `Action:` or `Final Answer:`.\n"
                    "If no tool is needed, provide a complete answer now using exactly this format:\n"
                    "Thought: I can answer directly\n"
                    "Final Answer: <complete answer in the user's language>\n\n"
                    "Do not return only a short summary. / 上一次输出没有包含有效的 `Action:` 或 `Final Answer:`。\n"
                    "如果不需要工具，请现在按上述格式给出完整答案，不要只返回一句简短摘要。"
                ),
            },
        ]
        repaired = self._chat(repair_messages)
        step = self._parse(repaired)
        if step.final_answer is not None:
            return step.final_answer
        return self._strip_think(repaired)
