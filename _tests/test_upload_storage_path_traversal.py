"""P1 regression: upload_storage 路径穿越防护 (2026-07-09 code review)

之前 ``_base_dir() / key`` 直接拼接，DB 里 ``storage_key`` 字段被注入
``../../etc/passwd`` 时可越界读取。新 ``_resolve_within_base`` 在解析
绝对路径后断言必须落在 ``UPLOAD_DIR`` 之下，越界抛 ValueError，
caller fail-close 到空字符串 / False。
"""
from __future__ import annotations

import os
import pytest

from react_agent import upload_storage


def test_resolve_within_base_accepts_normal_key(tmp_path, monkeypatch):
    """合法 key 应被解析为 UPLOAD_DIR 下的子路径。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    p = upload_storage._resolve_within_base("user1/gc/abc.txt.gz")
    assert p == (tmp_path / "user1/gc/abc.txt.gz").resolve()


def test_resolve_within_base_blocks_relative_traversal(tmp_path, monkeypatch):
    """``../../etc/passwd`` 必须在 resolve 后落在 UPLOAD_DIR 之外 → 拒。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        upload_storage._resolve_within_base("../../etc/passwd")


def test_resolve_within_base_blocks_absolute_path(tmp_path, monkeypatch):
    """绝对路径在 base / 下不应被允许（即便 ``os.path.join`` 不替换）。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        upload_storage._resolve_within_base("/etc/passwd")


def test_resolve_within_base_blocks_mid_string_traversal(tmp_path, monkeypatch):
    """``user1/../../etc/passwd`` 中段有 ``../``，resolve 后会被规范掉 → 拒。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        upload_storage._resolve_within_base("user1/../../etc/passwd")


def test_resolve_within_base_blocks_empty_key(tmp_path, monkeypatch):
    """空 key → 拒（否则会命中 base 本身）。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        upload_storage._resolve_within_base("")


def test_load_uploaded_text_falls_back_on_traversal_key(tmp_path, monkeypatch):
    """load_uploaded_text 在 key 越界时 fail-close 到 fallback，不抛。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    # 即便传入恶意 key，也不应被越界读取
    out = upload_storage.load_uploaded_text(
        "local_encrypted_gzip", "../../etc/passwd", fallback="safe-fallback"
    )
    assert out == "safe-fallback"


def test_delete_uploaded_text_returns_false_on_traversal_key(tmp_path, monkeypatch):
    """delete_uploaded_text 在 key 越界时返回 False，不抛、不删。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    ok = upload_storage.delete_uploaded_text("local_encrypted_gzip", "../../etc/passwd")
    assert ok is False
