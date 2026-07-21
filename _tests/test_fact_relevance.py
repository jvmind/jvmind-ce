"""Tests for scored + token-budgeted fact selection."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from react_agent.memory.facts import _score, scored_facts, CATEGORY_PRIORITY
from react_agent.memory.token_budget import compute_tokens
from react_agent.models import FactModel, SessionModel, UserModel
from react_agent.db import SessionLocal


def _seed(db, sid: str = "sid") -> None:
    """Seed a user + session row so FactModel.session_id FK passes."""
    user = UserModel(
        id="u_test",
        username="u_test@example.com",
        password_hash="x",
    )
    db.add(user)
    sess = SessionModel(id=sid, user_id="u_test", title="t")
    db.add(sess)
    db.commit()


def _seed_facts(db, sid: str = "sid") -> None:
    for i in range(50):
        db.add(FactModel(
            session_id=sid,
            content=("x" * 50) + f" fact {i}",
            category="system_context" if i % 2 == 0 else "user_remembered",
            last_accessed_at=(datetime.utcnow() - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
            access_count=i % 5,
            created_at="2026-07-15 00:00:00",
        ))
    db.commit()


class _StubMemory:
    def __init__(self, db):
        self.db = db

    def get_db(self):
        return self.db


def test_category_priority_values():
    assert CATEGORY_PRIORITY["user_remembered"] > CATEGORY_PRIORITY["system_context"]
    assert CATEGORY_PRIORITY["user_preference"] > CATEGORY_PRIORITY["report_index"]


def test_scored_facts_caps_to_budget(db_clean):
    db = SessionLocal()
    try:
        _seed(db)
        _seed_facts(db)
        mem = _StubMemory(db)
        facts = scored_facts(mem, "sid", model="deepseek-chat", budget_tokens=400)
    finally:
        db.close()

    assert 0 < len(facts) <= 50
    assert all("text" in f and "category" in f and "id" in f for f in facts)
    total_tokens = sum(compute_tokens(f["text"], "deepseek-chat") for f in facts)
    assert total_tokens <= 400


def test_scored_facts_prefers_recent_remembered(db_clean):
    db = SessionLocal()
    try:
        _seed(db)
        _seed_facts(db)
        mem = _StubMemory(db)
        facts = scored_facts(mem, "sid", model="deepseek-chat", budget_tokens=300)
    finally:
        db.close()

    assert facts, "expected at least one fact selected"
    top = facts[0]
    assert top["category"] == "user_remembered", (
        f"expected top fact to be user_remembered, got {top['category']}"
    )


def test_brand_new_fact_scores_above_zero(db_clean):
    """A brand-new fact (access_count=0, last_accessed_at=now) must score
    STRICTLY higher than an older system_context fact with the same
    access_count=0. Regression guard for the log(0+1)=0 bug that made
    brand-new facts invisible on first selection."""
    now = datetime.utcnow()
    fresh = SimpleNamespace(
        access_count=0,
        last_accessed_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        category="user_remembered",
    )
    stale = SimpleNamespace(
        access_count=0,
        last_accessed_at=(now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),
        category="system_context",
    )

    fresh_score = _score(fresh, now)
    stale_score = _score(stale, now)

    assert fresh_score > 0, (
        f"brand-new fact scored 0 (bug: log(0+1)=0); got {fresh_score}"
    )
    assert fresh_score > stale_score, (
        f"fresh user_remembered ({fresh_score}) should outrank stale "
        f"system_context ({stale_score}) even with access_count=0"
    )


def test_scored_facts_updates_access_count(db_clean):
    db = SessionLocal()
    try:
        _seed(db)
        _seed_facts(db)
        mem = _StubMemory(db)
        before = {f.id: (f.access_count or 0) for f in db.query(FactModel).all()}
        facts = scored_facts(mem, "sid", model="deepseek-chat", budget_tokens=300)
        selected_ids = {f["id"] for f in facts}
    finally:
        db.close()

    db2 = SessionLocal()
    try:
        for fid in selected_ids:
            row = db2.query(FactModel).filter(FactModel.id == fid).first()
            assert (row.access_count or 0) == before[fid] + 1
            assert row.last_accessed_at is not None
    finally:
        db2.close()