"""清空会话消息必须同时清掉系统上下文事实（[context:*]），
否则下一次对话仍会把清空前的摘要注入 system prompt，
模型会“记得”已清空的历史（回归：清空后模型带旧上下文回复）。
用户显式 remember 的长期记忆应保留。"""
from __future__ import annotations

from react_agent.memory_db import DatabaseMemory


def _new_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from react_agent import db as db_mod
    db_mod.Base.metadata.create_all(db_mod.engine)
    return DatabaseMemory(user_id="u_test_1", session_dir=str(tmp_path / "sessions"))


def _seed_context_and_facts(mem, sid):
    mem.append_message(sid, "user", "你好，帮我分析 GC 日志")
    mem.append_message(sid, "assistant", "好的，以下是分析结果……")
    mem.set_context_fact(sid, "summary", "用户询问 GC 日志分析，agent 给出了建议")
    mem.set_context_fact(sid, "last_tool", "Last tool observation: tool=analyze_gc_log")
    mem.set_context_fact(sid, "reports", "gc: report_id=gc_123 | 汇总")
    mem.add_fact(sid, "用户偏好：使用 DeepSeek 模型")
    return sid


def test_clear_messages_removes_context_facts_but_keeps_user_facts(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    sid = mem.create_session("GC 分析")
    _seed_context_and_facts(mem, sid)

    assert len(mem.get_messages(sid)) == 2
    assert mem.get_context_fact(sid, "summary") == "用户询问 GC 日志分析，agent 给出了建议"
    assert mem.get_context_fact(sid, "last_tool") != ""
    assert "用户偏好：使用 DeepSeek 模型" in mem.get_facts(sid)

    mem.clear_messages(sid)

    assert mem.get_messages(sid) == []
    assert mem.get_context_fact(sid, "summary") == ""
    assert mem.get_context_fact(sid, "last_tool") == ""
    assert mem.get_context_fact(sid, "reports") == ""
    # 用户长期记忆保留
    assert "用户偏好：使用 DeepSeek 模型" in mem.get_facts(sid)


def test_clear_messages_on_missing_session_is_noop(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    # 不应抛异常
    mem.clear_messages("sess_nonexistent")


def test_clear_messages_other_user_session_is_untouched(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    sid = mem.create_session("GC 分析")
    _seed_context_and_facts(mem, sid)

    # 换成别的 user 实例清同一会话：所有权校验应拒绝
    other = DatabaseMemory(user_id="u_other", session_dir=str(tmp_path / "sessions2"))
    other.clear_messages(sid)

    assert len(mem.get_messages(sid)) == 2
    assert mem.get_context_fact(sid, "summary") == "用户询问 GC 日志分析，agent 给出了建议"
