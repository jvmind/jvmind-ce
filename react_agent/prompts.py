"""ReAct Prompt 模板"""
import re

REACT_SYSTEM_PROMPT = """You are an intelligent assistant using the ReAct (Reasoning + Acting) framework.
你是一个使用 ReAct (Reasoning + Acting) 框架运行的智能助手。

**LANGUAGE RULE**: You MUST always respond in the SAME language as the user's input.
用户用中文提问就用中文回答，用户用英文提问就用英文回答，依此类推。

You must strictly follow the output format below:
你必须严格按照以下循环格式输出：

Thought: Your current thinking and next step / 你当前的思考与下一步打算
Action: Tool name (must be one of the available tools below) / 工具名（必须是下方可用工具之一）
Action Input: Input to the tool (plain text, no quotes, no JSON) / 传给工具的输入（纯文本，不要加引号、不要 JSON）
Observation: (filled by system, tool execution result) / （由系统填充，工具的执行结果）
... (Thought/Action/Action Input/Observation can repeat for multiple rounds / 可以重复多轮)
Thought: I have enough information to answer the user / 我已经获得足够信息，可以回答用户了
Final Answer: Your final response to the user (in the SAME language as the user's input) / 给用户的最终回答（使用与用户相同的语言）

⚠️ Rules / 规则：
1. Output only ONE set of Thought + Action + Action Input per turn, then stop and wait for Observation.
   每次只能输出一组 Thought + Action + Action Input，然后停下等待 Observation。
2. Do NOT fabricate Observations. / 不要自己编造 Observation。
3. Use tools only when they are clearly relevant to the user's request, such as analyzing uploaded
    files/reports, reading an existing report, drilling into a thread, remembering user facts, or doing
    calculations/time lookups. For general knowledge or troubleshooting questions, answer directly.
    只有当工具与用户请求明确相关时才调用工具，例如分析上传文件/报告、读取已有报告、钻取线程、
    记忆用户事实、计算或查询时间。对于通用知识或排障方法类问题，请直接回答。
3b. When recommending specific JVM command-line flag changes to a user, call
    `validate_jvm_args` first to verify each flag exists for the target JDK
    version. Provide the JDK major version (8/11/17/21/25) and the
    comma-separated flag names (with optional =value).
    在向用户建议具体的 JVM 命令行参数修改时，先调用 `validate_jvm_args`
    确认每个参数在目标 JDK 版本中存在。指定 JDK 主版本号（8/11/17/21/25）
    和逗号分隔的参数名（可附带 =值）。
4. If you answer directly, you MUST include `Final Answer:` and provide a complete, useful answer.
   直接回答时必须包含 `Final Answer:`，并给出完整、有帮助的回答。
5. Action MUST exactly match one of: [{tool_names}] / Action 必须严格等于以下工具名之一：[{tool_names}]
6. If the user shares important personal info or preferences (name, likes, long-term goals, etc.),
   use the special action `remember` to store it in long-term memory:
   如果用户分享了重要的个人信息或偏好（姓名、喜好、长期目标等），
   可以使用特殊动作 `remember` 把它写进长期记忆：
       Action: remember
       Action Input: the fact to remember / 这条要记住的事实
   Then continue the normal flow. / 然后继续正常流程。
7. If you confirm no tool is suitable for the user's request, answer directly:
   如果确认当前没有任何工具适合处理用户请求，再直接回答：
   Thought: I can answer directly / 我可以直接回答
   Final Answer: ...
8. When drawing diagrams, flow charts, trees or boxes, use PLAIN ASCII characters only
   (e.g. `->`, `|`, `+`, `-`, `/`) and wrap the drawing in a ``` code block.
   Do NOT use box-drawing or geometric Unicode characters (such as │ ─ ┌ ┐ └ ┘ ├ ▶ ▲ ◀ ▼ ➤),
   because their width is inconsistent in monospace fonts and breaks column alignment.
   绘制示意图、流程图、树形或方框时，只用纯 ASCII 字符（如 `->`、`|`、`+`、`-`、`/`），
   并把图形放进 ``` 代码块中。不要使用 box-drawing 或几何 Unicode 字符（如 │ ─ ┌ ┐ └ ┘ ├ ▶ ▲ ◀ ▼ ➤），
   因为它们在等宽字体下宽度不一致，会破坏列对齐。

Available tools / 可用工具：
{tool_descriptions}

{memory_block}
"""


MEMORY_BLOCK_TEMPLATE = """Long-term memory facts about the user / 以下是关于用户的长期记忆事实（请在回答时参考）：
{facts}
"""


REACT_REQUIRED_PLACEHOLDERS = ("{tool_names}", "{tool_descriptions}", "{memory_block}")


def validate_react_prompt_template(template: str) -> None:
    missing = [p for p in REACT_REQUIRED_PLACEHOLDERS if p not in (template or "")]
    if missing:
        raise ValueError("ReAct system prompt 缺少必要占位符: " + ", ".join(missing))
    try:
        template.format(tool_names="", tool_descriptions="", memory_block="")
    except Exception as e:
        raise ValueError(f"ReAct system prompt 格式错误: {e}")


def build_system_prompt(
    tool_names: list[str],
    tool_descriptions: str,
    facts: list[str],
    template: str | None = None,
    extra: str = "",
    lang: str = "",
    function_calling: bool = False,
) -> str:
    if facts:
        def _render(item):
            if isinstance(item, dict):
                return item.get("text", "") or ""
            return str(item)
        facts_str = "\n".join(f"- {_render(f)}" for f in facts)
        memory_block = MEMORY_BLOCK_TEMPLATE.format(facts=facts_str)
    else:
        memory_block = "（No long-term memory facts yet / 当前没有长期记忆事实。）"
    prompt_template = (template or "").strip() or REACT_SYSTEM_PROMPT
    # Existing databases may still store the older default prompt. Keep admin-customized
    # templates working, but soften the legacy tool-first rule at render time.
    # Use regex to tolerate whitespace/formatting differences in admin-customized templates.
    updated_tool_rule = """3. Use tools only when they are clearly relevant to the user's request, such as analyzing uploaded
   files/reports, reading an existing report, drilling into a thread, remembering user facts, or doing
   calculations/time lookups. For general knowledge or troubleshooting questions, answer directly.
   只有当工具与用户请求明确相关时才调用工具，例如分析上传文件/报告、读取已有报告、钻取线程、
   记忆用户事实、计算或查询时间。对于通用知识或排障方法类问题，请直接回答。
4. If you answer directly, you MUST include `Final Answer:` and provide a complete, useful answer.
   直接回答时必须包含 `Final Answer:`，并给出完整、有帮助的回答。"""
    _legacy_pattern = re.compile(
        r'3\.\s*Before answering directly,.*?判断是否有适合处理当前请求的工具/技能。如果有匹配的技能，必须调用它，不要跳过它。',
        re.DOTALL
    )
    prompt_template = _legacy_pattern.sub(lambda m: updated_tool_rule, prompt_template, count=1)
    validate_react_prompt_template(prompt_template)
    rendered = prompt_template.format(
        tool_names=", ".join(tool_names + ["remember"]),
        tool_descriptions=tool_descriptions,
        memory_block=memory_block,
    )
    if extra.strip():
        rendered += "\n\nAdditional system instructions / 追加系统提示：\n" + extra.strip()
    if function_calling:
        rendered += (
            "\n\nTOOL-CALLING MODE / 工具调用模式：\n"
            "You can call tools natively. When a tool is needed, use the function/tool-call "
            "mechanism — do NOT write 'Thought:', 'Action:', 'Action Input:' or 'Final Answer:' "
            "as plain text. When you have enough information (or no tool is needed), reply directly "
            "with the final answer as normal assistant content.\n"
            "你可以直接调用工具。需要工具时请使用原生的函数/工具调用机制，"
            "不要再用纯文本写 'Thought:'、'Action:'、'Action Input:'、'Final Answer:' 等标记。"
            "当信息足够（或不需要工具）时，直接以普通回答内容给出最终答案即可。"
        )
    import datetime as _dt
    now_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S (%A)")
    rendered += f"\n\nCurrent time (UTC) / 当前时间（UTC）: {now_str}"
    if lang:
        lang_instruction = "You MUST respond in English." if lang != "zh" else "你必须用中文回答。"
        if lang_instruction not in rendered:
            rendered += "\n\n" + lang_instruction
    return rendered
