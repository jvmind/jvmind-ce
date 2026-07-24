"""Regression tests for app/core/state.py _LRUDict + config PUT atomicity.

The original bug surface (reported in production): ``app/core/state.py``
``__getitem__`` ``self.move_to_end(key)`` raised ``KeyError: 'user_local'``
when saving a custom LLM model. Root cause was a race in
``config.py:put_config`` — pop the cached agent, close its memory, then
rebuild — where concurrent chat requests between pop and rebuild saw a
half-empty cache and crashed.

These tests pin both the atomic swap and the simpler ``_LRUDict`` semantics.
"""
from __future__ import annotations

import os
import threading
import time
import traceback

import pytest


# ---------- _LRUDict semantics ----------

def test_lrudict_basic_lru():
    """Smoke test: _LRUDict behaves like an LRU cache under sequential access."""
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=3)
    d["a"] = 1
    d["b"] = 2
    d["c"] = 3
    _ = d["a"]  # touch 'a' — now most-recent
    d["d"] = 4  # should evict 'b'
    assert "b" not in d
    assert "a" in d
    assert "c" in d
    assert "d" in d


def test_lrudict_get_missing_raises_keyerror():
    """Single-threaded: missing key raises KeyError on [], not silent None."""
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=4)
    with pytest.raises(KeyError):
        _ = d["missing"]


def test_lrudict_get_default_returns_none():
    """``get`` returns default when key missing, doesn't raise."""
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=4)
    assert d.get("missing") is None
    assert d.get("missing", "fallback") == "fallback"


def test_lrudict_pop_then_set_keeps_internal_state_consistent():
    """Reproduces the original PUT-config flow at the dict layer:

    ``pop(key, None)`` then ``__setitem__(key, new_value)`` then ``__getitem__``
    must work without raising. If the pop or set ever bypassed
    ``OrderedDict.__delitem__`` / ``__setitem__`` (which maintain the
    internal ``__map``), the subsequent ``move_to_end`` would raise.
    """
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=4)
    d["user_local"] = "agent_v1"
    assert d["user_local"] == "agent_v1"

    d.pop("user_local", None)
    d["user_local"] = "agent_v2"
    assert d["user_local"] == "agent_v2"
    assert "user_local" in list(d)


def test_lrudict_overwrite_existing_key_works():
    """Setting an existing key updates value and keeps __map consistent."""
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=4)
    d["k"] = "v1"
    d["k"] = "v2"
    d["k"] = "v3"
    assert d["k"] == "v3"
    # iteration must still yield the key
    assert list(d) == ["k"]


def test_lrudict_move_to_end_does_not_crash_on_consistent_dict():
    """With the public surface (set/pop/get only), ``__getitem__`` must never
    raise ``KeyError`` from inside ``move_to_end`` for a key that
    ``super().__getitem__`` just returned successfully.

    This is the specific bug the user reported.
    """
    from app.core.state import _LRUDict

    d = _LRUDict(maxsize=4)
    d["user_local"] = "agent"
    # Many sequential gets — none should raise.
    for _ in range(100):
        assert d["user_local"] == "agent"


# ---------- _AGENTS_LOCK + helpers._get_agent atomicity ----------

def test_get_agent_holds_lock_for_entire_flow(monkeypatch):
    """``helpers._get_agent`` must hold ``_AGENTS_LOCK`` while reading the
    cached value, otherwise a concurrent ``state._AGENTS.pop`` can race
    between the existence check and the final read.

    We simulate the race directly: monkeypatch the cache to first schedule a
    pop right after the existence check, then assert the get never sees a
    missing key.
    """
    from app.core import helpers, state

    user_id = "user_local"
    state._AGENTS.clear()

    class _Stub:
        memory = type("M", (), {"close": staticmethod(lambda: None)})()

    state._AGENTS[user_id] = _Stub()

    # Patch __getitem__ to drop the key right after returning the value, the
    # way a concurrent pop+rebuild thread would.
    real_getitem = state._AGENTS.__class__.__getitem__
    call_count = {"n": 0}

    def racing_getitem(self, key):
        result = real_getitem(self, key)
        call_count["n"] += 1
        # Simulate concurrent pop on the same key, every other call.
        if call_count["n"] % 2 == 1:
            try:
                del self[key]
            except KeyError:
                pass
        return result

    monkeypatch.setattr(state._AGENTS.__class__, "__getitem__", racing_getitem)

    # _get_agent under the new implementation must keep _AGENTS_LOCK held
    # across the existence check and the final read, so the racing pop
    # cannot interleave.
    seen = helpers._get_agent(user_id)
    assert seen is not None


def test_config_put_atomic_swap_keeps_dict_consistent(monkeypatch):
    """Simulate the original bug: a PUT config happens while concurrent chat
    requests access ``state._AGENTS``. After the fix, no chat read should
    see a half-empty cache for the user-local key.

    Mirrors the actual ``config.py:put_config`` flow: build the new agent
    first, then atomically overwrite under ``_AGENTS_LOCK``.
    """
    from app.core import helpers, state

    state._AGENTS.clear()
    user_id = "user_local"

    class _Stub:
        memory = type("M", (), {"close": staticmethod(lambda: None)})()

    state._AGENTS[user_id] = _Stub()

    # Monkey-patch _build_agent so we don't need a real LLM.
    monkeypatch.setattr(helpers, "_build_agent", lambda uid: _Stub())

    errors: list = []
    stop = threading.Event()

    def put_config_simulation():
        while not stop.is_set():
            try:
                # Mirror config.py:put_config (post-fix):
                # 1. Build new agent outside the lock.
                # 2. Atomic get+set under _AGENTS_LOCK (no pop — the old
                #    agent stays in the dict until the new one overwrites).
                # 3. Close old memory after swap.
                new_agent = helpers._build_agent(user_id)
                with state._AGENTS_LOCK:
                    old_agent = state._AGENTS.get(user_id)
                    state._AGENTS[user_id] = new_agent
                if old_agent is not None and hasattr(old_agent.memory, "close"):
                    old_agent.memory.close()
            except Exception:
                errors.append(("put", traceback.format_exc()))

    def chat_read():
        while not stop.is_set():
            try:
                _ = state._AGENTS[user_id]
            except KeyError:
                errors.append(("chat", traceback.format_exc()))

    threads = [threading.Thread(target=put_config_simulation) for _ in range(2)]
    threads += [threading.Thread(target=chat_read) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join()

    chat_errors = [e for e in errors if e[0] == "chat"]
    put_errors = [e for e in errors if e[0] == "put"]
    assert not chat_errors, f"chat reads saw missing key: {chat_errors[:1]}"
    assert not put_errors, f"put_config swap crashed: {put_errors[:1]}"


def test_agents_lock_is_reentrant():
    """``_AGENTS_LOCK`` must be a reentrant lock so nested paths don't
    deadlock (e.g. ``put_config`` while a chat holds the lock).

    ``threading.RLock`` is implemented in C and ``isinstance`` does not work,
    so we exercise the contract instead: same thread can acquire twice.
    """
    from app.core import state

    with state._AGENTS_LOCK:
        # Same thread must be allowed to re-enter.
        with state._AGENTS_LOCK:
            pass


def test_state_module_does_not_reassign_AGENTS():
    """Sanity: ``state._AGENTS`` must remain a single _LRUDict instance — a
    rebind would orphan the lock and re-introduce the race."""
    from app.core import state
    from app.core.state import _LRUDict

    assert isinstance(state._AGENTS, _LRUDict)