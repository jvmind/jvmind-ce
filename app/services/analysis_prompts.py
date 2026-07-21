from __future__ import annotations

from typing import Optional


REFERENCE_KEYS = {
    "gc": "reference_content_gc",
    "jstack": "reference_content_jstack",
    "thread": "reference_content_thread",
}

PROMPT_KEYS = {
    ("gc", "zh"): "prompt_gc_zh",
    ("gc", "en"): "prompt_gc_en",
    ("jstack", "zh"): "prompt_jstack_zh",
    ("jstack", "en"): "prompt_jstack_en",
    ("thread", "zh"): "prompt_thread_zh",
    ("thread", "en"): "prompt_thread_en",
}

DEFAULT_GC_PROMPT_ZH = (
    "你是一位资深 JVM 性能调优专家。\n"
    "你必须用中文回答。\n"
    "我会给你一份 GC 日志的统计摘要，请基于数据给出专业的诊断结论与可执行的优化建议。\n\n"
    "回答要求：\n"
    "1. 使用结构化的 Markdown（## 小标题、要点列表）。\n"
    "2. 至少包含：『整体健康度评估』『关键问题』『参数调优建议』『后续观察指标』四个小节。\n"
    "3. 结论必须基于给出的数据，不要编造数字。\n"
    "4. 如停顿超过 200ms、Full GC 频繁、堆使用率持续偏高，需要明确指出风险。\n"
    "5. 如果统计分析中包含「内存诊断」部分（Memory Diagnosis），请重点关注并分析内存泄漏风险和 OOM 风险。\n"
    "   - 泄漏风险高时，分析可能的原因（静态集合、ThreadLocal、连接未关闭等）\n"
    "   - OOM 风险高时，分析堆配置是否合理，评估扩容需求\n"
    "6. 如果提到 JVM 参数调优建议，请务必遵守：\n"
    "   - 只建议你**确定存在**的参数，不确定的标注「请查阅官方文档」\n"
    "   - 注意参数适用的 JDK 版本（基于本次分析检测到的版本），不要建议已废弃/移除的参数\n"
    "   - 给出具体的参数值建议时，说明合理范围"
)

DEFAULT_GC_PROMPT_EN = (
    "You are an expert JVM performance tuning engineer.\n"
    "You MUST respond in English.\n"
    "I will provide a GC log statistical summary. Provide professional diagnosis "
    "and actionable optimization suggestions based on the data.\n\n"
    "Response Requirements:\n"
    "1. Structured Markdown with ## headings and bullet lists.\n"
    "2. Must include at least: Overall Health Assessment, Key Issues, "
    "Tuning Recommendations, Monitoring Metrics.\n"
    "3. Conclusions must be data-driven. Do not fabricate numbers.\n"
    "4. Call out risks explicitly: pauses >200ms, frequent Full GC, "
    "sustained high heap usage.\n"
    "5. If the statistical summary includes a 'Memory Diagnosis' section, "
    "pay special attention and analyze memory leak risk and OOM risk:\n"
    "   - High leak risk: analyze possible causes (static collections, ThreadLocal, unclosed connections, etc.)\n"
    "   - High OOM risk: analyze whether heap configuration is reasonable, assess expansion needs\n"
    "6. For JVM parameter tuning suggestions:\n"
    "   - Only suggest parameters you are certain about; mark uncertain ones as "
    "'consult official documentation'\n"
    "   - Be aware of JDK version applicability (based on detected version), "
    "do not suggest deprecated/removed parameters\n"
    "   - Provide reasonable value ranges when suggesting specific parameter values"
)

DEFAULT_JSTACK_PROMPT_ZH = (
    "你是一位资深 JVM 性能调优专家，擅长分析 Java 线程转储。\n"
    "你必须用中文回答。\n"
    "我会给你一份 jstack 线程转储的统计摘要，请基于数据给出专业的诊断结论与可执行的优化建议。\n\n"
    "回答要求：\n"
    "1. 使用结构化的 Markdown（## 小标题、要点列表）。\n"
    "2. 至少包含：『整体健康度评估』『关键问题』『优化建议』『后续观察指标』四个小节。\n"
    "3. 结论必须基于给出的数据，不要编造数字。\n"
    "4. 如存在大量 BLOCKED 线程、死锁、或大量 WAITING 线程需要明确指出风险。\n"
    "5. 如果统计摘要包含「Thread Diagnosis」段，请重点结合其中的规则诊断结论（整体健康度、各项 finding 与建议）展开分析，并给出可执行的排查步骤。\n"
    "6. 如果摘要显示使用了虚拟线程（uses_virtual_threads=true），注意：标准 jstack 只打印挂载中的虚拟线程，未挂载的不可见；carrier 线程被 pin（栈中出现 synchronized 或原生阻塞）会阻碍调度。如怀疑虚拟线程过多或 pin 问题，建议用户以 jcmd <pid> Thread.dump_to_file -format=json 获取全量转储再评估。\n"
    "7. 如果提到 JVM 参数调优建议，请务必遵守：\n"
    "   - 只建议你**确定存在**的参数，不确定的标注「请查阅官方文档」\n"
    "   - 注意参数适用的 JDK 版本（基于本次分析检测到的版本），不要建议已废弃/移除的参数\n"
    "   - 给出具体的参数值建议时，说明合理范围"
)

DEFAULT_JSTACK_PROMPT_EN = (
    "You are an expert JVM performance engineer specializing in Java thread dump analysis.\n"
    "You MUST respond in English.\n"
    "I will provide a jstack thread dump statistical summary. Provide professional "
    "diagnosis and actionable optimization suggestions based on the data.\n\n"
    "Response Requirements:\n"
    "1. Structured Markdown with ## headings and bullet lists.\n"
    "2. Must include at least: Overall Health Assessment, Key Issues, "
    "Tuning Recommendations, Monitoring Metrics.\n"
    "3. Conclusions must be data-driven. Do not fabricate numbers.\n"
    "4. Call out risks explicitly: large number of BLOCKED threads, deadlocks, "
    "or excessive WAITING threads.\n"
    "5. If the summary contains a 'Thread Diagnosis' section, focus on its "
    "rule-based findings (overall health, each finding, and recommendations) "
    "and provide actionable investigation steps.\n"
    "6. If the summary indicates virtual threads are used (uses_virtual_threads=true), note: "
    "standard jstack only prints mounted virtual threads (unmounted ones are invisible), and "
    "pinned carrier threads (synchronized or native blocking in the stack) impede scheduling. "
    "If excessive virtual threads or pinning is suspected, recommend the user run "
    "jcmd <pid> Thread.dump_to_file -format=json for a full dump before concluding.\n"
    "7. For JVM parameter tuning suggestions:\n"
    "   - Only suggest parameters you are certain about; mark uncertain ones as "
    "'consult official documentation'\n"
    "   - Be aware of JDK version applicability (based on detected version), "
    "do not suggest deprecated/removed parameters\n"
    "   - Provide reasonable value ranges when suggesting specific parameter values"
)

DEFAULT_THREAD_PROMPT_ZH = (
    "你是一位资深 JVM 性能调优专家。\n"
    "你必须用中文回答。\n"
    "我会给你一个 Java 线程的完整栈帧信息，请分析：\n"
    "1. 该线程当前状态是否正常\n"
    "2. 它在执行什么操作\n"
    "3. 如果涉及锁等待/锁持有，分析锁链关系\n"
    "4. 是否存在异常或潜在风险：\n"
    "   - 如果是 JDK 21+ 的 carrier 线程且挂载了虚拟线程（Mounted virtual thread #N），\n"
    "     N 是全局线程 ID（累计创建数），大数值本身不必然说明虚拟线程过多，\n"
    "     但如果同时观察到大量 carrier 线程都处于 PCB（pinned）状态、\n"
    "     或摘要中 `uses_virtual_threads` 为 true、carrier_count 异常大，\n"
    "     需怀疑是否存在大量虚拟线程 pin 住 carrier 导致调度阻塞。\n"
    "   - 欲准确评估虚拟线程总量，应引导用户使用 jcmd <pid> Thread.dump_to_file -format=json 获取全量转储。\n"
    "5. 优化建议\n"
    "使用结构化的 Markdown 回答。"
)

DEFAULT_THREAD_PROMPT_EN = (
    "You are an expert JVM performance tuning engineer.\n"
    "You MUST respond in English.\n"
    "I will provide a complete Java thread stack trace. Please analyze:\n"
    "1. Whether the thread's current state is normal\n"
    "2. What operation it is performing\n"
    "3. If lock waiting/holding is involved, analyze the lock chain\n"
    "4. Whether there are anomalies or potential risks:\n"
    "   - For JDK 21+ carrier threads with a mounted virtual thread (\"Mounted virtual thread #N\"),\n"
    "     N is a global thread ID (cumulative creation count). A large N alone does NOT indicate\n"
    "     excessive virtual threads.\n"
    "   - However, if many carrier threads are pinned (PCB state: native blocking / synchronized),\n"
    "     or the summary shows uses_virtual_threads=true with an unusually high carrier_count,\n"
    "     suspect an excessive number of virtual threads pinning carriers and blocking scheduling.\n"
    "   - To accurately assess total virtual thread count, recommend the user to run\n"
    "     jcmd <pid> Thread.dump_to_file -format=json for a full dump of all (incl. unmounted) threads.\n"
    "5. Optimization suggestions\n"
    "Respond using structured Markdown."
)

DEFAULT_ANALYSIS_PROMPTS = {
    "prompt_gc_zh": DEFAULT_GC_PROMPT_ZH,
    "prompt_gc_en": DEFAULT_GC_PROMPT_EN,
    "prompt_jstack_zh": DEFAULT_JSTACK_PROMPT_ZH,
    "prompt_jstack_en": DEFAULT_JSTACK_PROMPT_EN,
    "prompt_thread_zh": DEFAULT_THREAD_PROMPT_ZH,
    "prompt_thread_en": DEFAULT_THREAD_PROMPT_EN,
}

# ASCII 绘图约束：避免 box-drawing / 几何 Unicode 字符破坏 <pre> 列对齐。
# 追加到所有分析提示词末尾，确保图形输出在等宽字体下不错位。
_ASCII_DIAGRAM_RULE_ZH = (
    "\n\n绘图约束：如需绘制示意图、流程图、树形或方框，只用纯 ASCII 字符"
    "（如 -> 、| 、+ 、- 、/ 、\\），并放进 ``` 代码块。"
    "不要使用 box-drawing 或几何 Unicode 字符（如 │ ─ ┌ ┐ └ ┘ ├ ▶ ▲ ◀ ▼ ➤），"
    "它们在等宽字体下宽度不一致，会破坏列对齐。"
)
_ASCII_DIAGRAM_RULE_EN = (
    "\n\nDiagram constraint: when drawing diagrams, flow charts, trees or boxes, "
    "use PLAIN ASCII characters only (e.g. -> , | , + , - , / , \\) and wrap them in a ``` code block. "
    "Do NOT use box-drawing or geometric Unicode characters (such as │ ─ ┌ ┐ └ ┘ ├ ▶ ▲ ◀ ▼ ➤), "
    "because their width is inconsistent in monospace fonts and breaks column alignment."
)
for _k in ("prompt_gc_zh", "prompt_jstack_zh", "prompt_thread_zh"):
    DEFAULT_ANALYSIS_PROMPTS[_k] += _ASCII_DIAGRAM_RULE_ZH
for _k in ("prompt_gc_en", "prompt_jstack_en", "prompt_thread_en"):
    DEFAULT_ANALYSIS_PROMPTS[_k] += _ASCII_DIAGRAM_RULE_EN


def _detect_session_language(agent, sid: str) -> str:
    """从会话聊天历史中检测用户语言。返回 'zh' 或 'en'。"""
    try:
        msgs = agent.memory.get_messages(sid)
        user_msgs = [m["content"] for m in msgs if m.get("role") == "user"][-5:]
        for msg in reversed(user_msgs):
            if msg and msg.strip():
                cjk = sum(1 for c in msg if '\u4e00' <= c <= '\u9fff')
                if cjk > 0:
                    return "zh"
                return "en"
    except Exception:
        # Best-effort heuristic; any failure falls back to the default below.
        pass
    return "en"


def _resolve_lang(request_lang: Optional[str], agent, sid: str) -> str:
    """从请求参数或会话历史决定输出语言。"""
    if request_lang and request_lang in ("en", "zh"):
        return request_lang
    return _detect_session_language(agent, sid)


def _append_reference(sys_prompt: str, ref: str, lang: str) -> str:
    if not ref:
        return sys_prompt
    title = "参考资料（请先阅读）" if lang == "zh" else "Reference Material (read first)"
    return f"{sys_prompt}\n\n## {title}\n\n{ref}"


def _get_analysis_prompt(feature: str, lang: str) -> str:
    """读取后台配置的功能提示词；未配置时返回代码默认值。
    自动添加显式的语言指令，确保 AI 以正确语言输出。
    """
    lang = "zh" if lang == "zh" else "en"
    key = PROMPT_KEYS[(feature, lang)]
    default = DEFAULT_ANALYSIS_PROMPTS[key]
    try:
        from app.core import helpers
        val = (helpers._get_system_setting(key, "") or "").strip()
        if not val:
            return default
        prompt = val
    except Exception:
        prompt = default
    # Ensure explicit language instruction is present (critical for models with language bias)
    lang_instruction = "你必须用中文回答。" if lang == "zh" else "You MUST respond in English."
    if lang_instruction not in prompt:
        prompt = lang_instruction + "\n" + prompt
    return prompt


def _get_reference_content(feature: str) -> str:
    """按功能读取参考资料，兼容旧的全局 reference_content。"""
    try:
        from app.core import helpers
        if feature == "thread":
            ref = (helpers._get_system_setting(REFERENCE_KEYS["thread"], "") or "").strip()
            if ref:
                return ref
            ref = (helpers._get_system_setting(REFERENCE_KEYS["jstack"], "") or "").strip()
            if ref:
                return ref
        else:
            ref = (helpers._get_system_setting(REFERENCE_KEYS[feature], "") or "").strip()
            if ref:
                return ref
        return (helpers._get_system_setting("reference_content", "") or "").strip()
    except Exception:
        return ""
