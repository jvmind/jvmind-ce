from __future__ import annotations

import base64
import gzip
import hashlib
import os
from pathlib import Path

from .config import decrypt_secret, encrypt_secret, master_key_available


def _base_dir() -> Path:
    return Path(os.getenv("UPLOAD_DIR", "./data/uploads"))


def _safe_part(value: str) -> str:
    return "".join(c for c in (value or "") if c.isalnum() or c in ("_", "-"))[:80]


def _resolve_within_base(key: str) -> Path:
    """P1 (2026-07-09 code review): 路径穿越防护。

    ``load_uploaded_text`` / ``delete_uploaded_text`` 用 ``_base_dir() / key``
    拼路径。如果 DB 里的 ``storage_key`` 字段被注入 ``../../etc/passwd``，
    旧实现会越界读取 UPLOAD_DIR 之外的文件。

    这里把 key 解析成绝对路径并断言它必须落在 ``_base_dir().resolve()``
    之下，否则抛 ``ValueError``。caller 拿到 ValueError 后 fallback
    到空字符串 / False，安全 fail-close。
    """
    base = _base_dir().resolve()
    if not key:
        raise ValueError("empty storage key")
    # 关键：必须先 .resolve() 才能 is_relative_to，否则 ../ 没被规范化
    # 时 base 是 /a/b，候选是 /a/c，会被误判为非子路径。
    try:
        candidate = (base / key).resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"unresolvable storage key: {e}")
    if not (candidate == base or base in candidate.parents):
        raise ValueError(
            f"storage key escapes UPLOAD_DIR (base={base})"
        )
    return candidate


def storage_key(user_id: str, file_id: str, content_type: str) -> str:
    user = _safe_part(user_id) or "unknown"
    fid = _safe_part(file_id)
    ctype = _safe_part(content_type) or "file"
    return f"{user}/{ctype}/{fid}.txt.gz"


def save_uploaded_text(user_id: str, file_id: str, content_type: str, text: str) -> dict:
    key = storage_key(user_id, file_id, content_type)
    path = _base_dir() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8")
    size = len(raw)
    sha256 = hashlib.sha256(raw).hexdigest()
    if master_key_available():
        compressed = gzip.compress(raw)
        encrypted = encrypt_secret(base64.b64encode(compressed).decode())
        path.write_text(encrypted, encoding="utf-8")
        return {
            "storage_backend": "local_encrypted_gzip",
            "storage_key": key.replace("\\", "/"),
            "size": size,
            "sha256": sha256,
        }
    # 无 CONFIG_ENCRYPTION_KEY：回退明文 gzip（CE 单机本地场景）。
    # 此前 encrypt_secret 会抛 RuntimeError，被上传路由吞掉后原文从未持久化，
    # 导致 jstack 线程钻取 / GC 原文读取拿到空文本。load/delete 已支持该 backend。
    with gzip.open(path, "wb") as f:
        f.write(raw)
    return {
        "storage_backend": "local_gzip",
        "storage_key": key.replace("\\", "/"),
        "size": size,
        "sha256": sha256,
    }


def load_uploaded_text(storage_backend: str, key: str, fallback: str = "") -> str:
    if storage_backend == "local_encrypted_gzip" and key:
        try:
            path = _resolve_within_base(key)
        except ValueError:
            return fallback or ""
        try:
            encrypted = path.read_text(encoding="utf-8")
            compressed = base64.b64decode(decrypt_secret(encrypted).encode())
            return gzip.decompress(compressed).decode("utf-8", errors="replace")
        except FileNotFoundError:
            return fallback or ""
        except Exception:
            return fallback or ""
    if storage_backend == "local_gzip" and key:
        try:
            path = _resolve_within_base(key)
        except ValueError:
            return fallback or ""
        try:
            with gzip.open(path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return fallback or ""
    return fallback or ""


def delete_uploaded_text(storage_backend: str, key: str) -> bool:
    if storage_backend in ("local_gzip", "local_encrypted_gzip") and key:
        try:
            path = _resolve_within_base(key)
        except ValueError:
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
    return False
