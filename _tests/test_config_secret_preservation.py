"""Regression tests for /api/config PUT — secret preservation.

Reproduces the bug: saving config without re-entering the API key wiped
the previously saved key because the frontend sends an empty string
when the input is left blank (placeholder shown instead of the value).
"""
from __future__ import annotations

import pytest


@pytest.mark.db
def test_put_config_empty_api_key_preserves_saved_key(client, monkeypatch):
    monkeypatch.setattr("react_agent.config._master_key", lambda: b"x" * 32, raising=False)
    cfg_get = client.get("/api/config").json()
    payload = {
        "openai_base_url": "https://api.example.com/v1",
        "openai_api_key": "sk-original-secret-1234",
        "openai_model": "test-model",
        "use_built_in": False,
        "temperature": 0.3,
        "max_iterations": 10,
    }
    res = client.put("/api/config", json=payload)
    assert res.status_code == 200, res.text
    cfg_get = client.get("/api/config").json()
    assert cfg_get.get("openai_api_key_set") is True, cfg_get

    res = client.put("/api/config", json={
        "openai_base_url": "https://api.example.com/v1",
        "openai_api_key": "",
        "openai_model": "test-model",
        "use_built_in": False,
    })
    assert res.status_code == 200, res.text

    cfg_get = client.get("/api/config").json()
    assert cfg_get.get("openai_api_key_set") is True, (
        f"API key was wiped by empty-string save: {cfg_get}"
    )
    assert cfg_get.get("openai_model") == "test-model"


@pytest.mark.db
def test_put_config_explicit_new_key_replaces_old(client, monkeypatch):
    monkeypatch.setattr("react_agent.config._master_key", lambda: b"x" * 32, raising=False)
    client.put("/api/config", json={
        "openai_base_url": "https://api.example.com/v1",
        "openai_api_key": "sk-old-key-aaaa",
        "openai_model": "test-model",
        "use_built_in": False,
    })
    res = client.put("/api/config", json={
        "openai_base_url": "https://api.example.com/v1",
        "openai_api_key": "sk-new-key-bbbb",
        "openai_model": "test-model",
        "use_built_in": False,
    })
    assert res.status_code == 200, res.text
    cfg_get = client.get("/api/config").json()
    assert cfg_get.get("openai_api_key_set") is True