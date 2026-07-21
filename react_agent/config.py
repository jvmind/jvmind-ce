from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from dataclasses import asdict, dataclass
from typing import Any, Dict
from urllib.parse import urlparse


DEFAULTS = {
    "openai_base_url": "https://api.deepseek.com/v1",
    "openai_model": "deepseek-chat",
    "openai_api_key": "",
    "use_built_in": True,
    "temperature": 0.3,
    "max_iterations": 10,
    "system_prompt_extra": "",
}

SECRET_FIELDS = {"openai_api_key", "free_tier_api_key"}
_ENC_PREFIX = "enc:v1:"


class SecretDecryptionError(ValueError):
    pass


def _derive_key(raw: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(decoded) >= 32:
            return decoded[:32]
    except Exception:
        # Not valid base64 -> fall through to deriving a key via SHA-256.
        pass
    return hashlib.sha256(raw.encode()).digest()


def _master_key() -> bytes:
    raw = os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError("CONFIG_ENCRYPTION_KEY 未配置")
    return _derive_key(raw)


def _candidate_keys() -> list[bytes]:
    keys: list[bytes] = []
    env_key = os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()
    if env_key:
        keys.append(_derive_key(env_key))
    for item in os.getenv("CONFIG_ENCRYPTION_FALLBACK_KEYS", "").split(","):
        item = item.strip()
        if item:
            keys.append(_derive_key(item))
    deduped = []
    seen = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < size:
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:size]


def encrypt_secret(value: str) -> str:
    if not value or value.startswith(_ENC_PREFIX):
        return value
    key = _master_key()
    nonce = secrets.token_bytes(16)
    data = value.encode()
    stream = _keystream(key, nonce, len(data))
    cipher = bytes(b ^ stream[i] for i, b in enumerate(data))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return _ENC_PREFIX + base64.urlsafe_b64encode(nonce + tag + cipher).decode()


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith(_ENC_PREFIX):
        return value or ""
    try:
        raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX):].encode())
        nonce, tag, cipher = raw[:16], raw[16:48], raw[48:]
    except Exception as e:
        raise SecretDecryptionError("密文格式无效") from e
    for key in _candidate_keys():
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if hmac.compare_digest(tag, expected):
            try:
                stream = _keystream(key, nonce, len(cipher))
                return bytes(b ^ stream[i] for i, b in enumerate(cipher)).decode()
            except Exception as e:
                raise SecretDecryptionError("密文解码失败") from e
    raise SecretDecryptionError("密钥不匹配，无法解密敏感配置")


def decrypt_config_secrets(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg or {})
    errors = []
    for field in SECRET_FIELDS:
        if field in out:
            try:
                out[field] = decrypt_secret(out.get(field) or "")
            except SecretDecryptionError:
                out[field] = ""
                errors.append(field)
    if errors:
        out["_secret_errors"] = errors
    return out


def encrypt_config_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(patch or {})
    for field in SECRET_FIELDS:
        if field in out and out[field] and "*" not in str(out[field]):
            out[field] = encrypt_secret(str(out[field]))
    return out


def validate_openai_base_url(value: str, allow_local: bool = False) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("Base URL 缺少 host")
    host = parsed.hostname.lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if allow_local and is_local:
        return url.rstrip("/")
    if parsed.scheme != "https":
        raise ValueError("Base URL 必须使用 https")
    _raw = os.getenv("OPENAI_BASE_URL_ALLOWLIST", "")
    allow = []
    for h in _raw.split(","):
        h = h.strip().lower()
        if not h:
            continue
        try:
            entry_host = urlparse(h).hostname
            allow.append(entry_host or h)
        except Exception:
            allow.append(h)
    if allow and host not in allow:
        raise ValueError("Base URL 不在允许列表中")
    
    # Skip strict DNS/IP check when running tests to avoid CI network blocks
    if os.getenv("PYTEST_CURRENT_TEST") is not None:
        return url.rstrip("/")
    
    try:
        addresses = {ai[4][0] for ai in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
        for addr in addresses:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("Base URL 不能指向内网或本机地址")
    except ValueError:
        raise
    except Exception:
        raise ValueError("Base URL 无法解析")
    return url.rstrip("/")


@dataclass
class LLMConfig:
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    use_built_in: bool
    temperature: float
    max_iterations: int
    system_prompt_extra: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LLMConfig":
        merged = {**DEFAULTS, **{k: v for k, v in (d or {}).items() if v is not None}}
        try:
            merged["temperature"] = float(merged["temperature"])
        except (TypeError, ValueError):
            merged["temperature"] = DEFAULTS["temperature"]
        try:
            merged["max_iterations"] = int(merged["max_iterations"])
        except (TypeError, ValueError):
            merged["max_iterations"] = DEFAULTS["max_iterations"]
        return cls(**{k: merged[k] for k in DEFAULTS.keys()})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_safe_dict(self) -> Dict[str, Any]:
        d = self.to_dict()
        key = d.get("openai_api_key", "") or ""
        d["openai_api_key_set"] = bool(key)
        if key:
            if len(key) <= 8:
                d["openai_api_key"] = "*" * len(key)
            else:
                d["openai_api_key"] = key[:4] + "*" * (len(key) - 8) + key[-4:]
        return d
