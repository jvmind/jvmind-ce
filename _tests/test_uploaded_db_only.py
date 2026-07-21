"""Tests for the DB-only uploaded-file read path."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from react_agent.memory_db import DatabaseMemory
from react_agent.upload_storage import save_uploaded_text
from react_agent.memory.uploads import get_uploaded_text


def _new_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from react_agent import db as db_mod
    db_mod.Base.metadata.create_all(db_mod.engine)
    return DatabaseMemory(user_id="u_test_1", session_dir=str(tmp_path / "sessions"))


def test_get_uploaded_text_returns_text_from_db(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    meta = save_uploaded_text("u_test_1", "fid_abc", "gc", "hello gc log")
    from react_agent.models import UploadedFileModel
    from react_agent.db import SessionLocal
    db = SessionLocal()
    try:
        db.add(UploadedFileModel(
            file_id="fid_abc", user_id="u_test_1",
            content_type="gc",
            content="",
            storage_backend=meta["storage_backend"],
            storage_key=meta["storage_key"],
            size=meta["size"], sha256=meta["sha256"],
            created_at="2026-07-15 00:00:00",
        ))
        db.commit()
    finally:
        db.close()

    text = mem.get_uploaded_text("fid_abc")
    assert text == "hello gc log"


def test_get_uploaded_text_missing_file_returns_empty(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    assert mem.get_uploaded_text("fid_missing") == ""


def test_module_helper_get_uploaded_text(tmp_path, monkeypatch):
    mem = _new_memory(tmp_path, monkeypatch)
    meta = save_uploaded_text("u_test_1", "fid_xyz", "jstack", "thread dump body")
    from react_agent.models import UploadedFileModel
    from react_agent.db import SessionLocal
    db = SessionLocal()
    try:
        db.add(UploadedFileModel(
            file_id="fid_xyz", user_id="u_test_1",
            content_type="jstack",
            content="",
            storage_backend=meta["storage_backend"], storage_key=meta["storage_key"],
            size=meta["size"], sha256=meta["sha256"],
            created_at="2026-07-15 00:00:00",
        ))
        db.commit()
    finally:
        db.close()

    assert get_uploaded_text(mem, "fid_xyz") == "thread dump body"
