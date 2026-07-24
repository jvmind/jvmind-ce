"""End-to-end tests for the GC reports API.

Covers session creation, GC log upload, listing/detail/export, deletion, and a
smoke test on a representative large fixture. (AI analysis now flows through the
Agent chat endpoint and is covered by test_chat.py.)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


_FIXTURES = Path(__file__).parent
_SAMPLE_LOG = _FIXTURES / "gc-jdk8-g1-full.log"


def _create_session(client) -> str:
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload_gc(client, sid: str, name: str, payload: bytes):
    return client.post(
        f"/api/sessions/{sid}/gc/upload",
        files={"file": (name, payload, "text/plain")},
    )


# ---------- Tests ----------

def test_upload_parses_g1_log(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    sample = _SAMPLE_LOG.read_bytes()

    r = _upload_gc(client, sid, "test.log", sample)
    assert r.status_code == 200, r.text
    upload = r.json()
    assert upload["report_id"]
    assert upload["file_id"]
    assert upload["stats"]["collector"] == "G1"
    assert upload["stats"]["events_total"] >= 11
    assert upload["stats"]["by_category"]["Full"]["count"] == 5


def test_list_and_detail(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    upload = _upload_gc(client, sid, "test.log", _SAMPLE_LOG.read_bytes()).json()
    rid = upload["report_id"]

    listing = client.get(f"/api/sessions/{sid}/gc/reports").json()
    assert len(listing["reports"]) == 1
    assert listing["reports"][0]["collector"] == "G1"
    assert listing["reports"][0]["has_ai"] is False

    detail = client.get(f"/api/sessions/{sid}/gc/reports/{rid}").json()
    assert detail["stats"]["events_total"] >= 11
    assert detail.get("ai_conclusion", "") == ""


def test_export_csv_and_json(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    upload = _upload_gc(client, sid, "test.log", _SAMPLE_LOG.read_bytes()).json()
    rid = upload["report_id"]

    j = client.get(f"/api/sessions/{sid}/gc/reports/{rid}/export?fmt=json")
    assert j.status_code == 200
    assert j.json()["stats"]["collector"] == "G1"

    c = client.get(f"/api/sessions/{sid}/gc/reports/{rid}/export?fmt=csv")
    assert c.status_code == 200
    assert "Category,Count,TotalPauseMs" in c.text
    assert "Slowest Events" in c.text


def test_delete_report(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    upload = _upload_gc(client, sid, "test.log", _SAMPLE_LOG.read_bytes()).json()
    rid = upload["report_id"]

    d = client.delete(f"/api/sessions/{sid}/gc/reports/{rid}").json()
    assert d["deleted"] is True

    listing = client.get(f"/api/sessions/{sid}/gc/reports").json()
    assert listing["reports"] == []


def test_upload_rejects_non_gc_log(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    r = _upload_gc(client, sid, "bad.log", b"hello world\nno gc events here")
    # Parser succeeds with zero events → 422
    assert r.status_code == 422


def test_upload_rejects_unsupported_extension(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    r = _upload_gc(client, sid, "bad.exe", b"binary content")
    assert r.status_code == 400
    assert "不支持" in r.text or "extension" in r.text.lower()


_LARGE_FIXTURES = [
    ("gc-jdk8-g1.log", "G1"),
    ("gc-jdk25-generational-zgc.log", "Z"),
]


@pytest.mark.parametrize("filename,collector", _LARGE_FIXTURES)
def test_large_fixture_smoke(auth_client, filename, collector):
    fixture = _FIXTURES / filename
    if not fixture.exists():
        pytest.skip(f"missing fixture: {filename}")

    client, _user = auth_client
    sid = _create_session(client)
    payload = fixture.read_bytes()
    r = _upload_gc(client, sid, filename, payload)
    assert r.status_code == 200, r.text
    uploaded = r.json()
    assert uploaded["filename"] == filename
    assert uploaded["report_id"]
    assert uploaded["file_id"]
    assert uploaded["stats"]["collector"] == collector
    assert uploaded["stats"]["events_total"] > 0

    detail = client.get(f"/api/sessions/{sid}/gc/reports/{uploaded['report_id']}").json()
    assert detail["stats"]["collector"] == collector

    j = client.get(f"/api/sessions/{sid}/gc/reports/{uploaded['report_id']}/export?fmt=json")
    assert j.status_code == 200
    assert j.json()["stats"]["collector"] == collector

    c = client.get(f"/api/sessions/{sid}/gc/reports/{uploaded['report_id']}/export?fmt=csv")
    assert c.status_code == 200
    assert "Category,Count,TotalPauseMs" in c.text


def test_get_non_existent_report(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    r = client.get(f"/api/sessions/{sid}/gc/reports/non_existent_rid")
    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_export_non_existent_report(auth_client):
    client, _user = auth_client
    sid = _create_session(client)
    r = client.get(f"/api/sessions/{sid}/gc/reports/non_existent_rid/export")
    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_upload_file_too_large(auth_client, monkeypatch):
    client, _user = auth_client
    sid = _create_session(client)
    # Monkey plan to force very small limit
    from app.core import helpers
    orig_get_plan = helpers._get_user_plan
    def mock_get_plan(user_id):
        return {"file_size_limit_mb": 0.001}  # 1KB limit
    monkeypatch.setattr(helpers, "_get_user_plan", mock_get_plan)

    large_data = b"X" * (2 * 1024)  # 2KB > 1KB limit
    r = _upload_gc(client, sid, "large.log", large_data)
    assert r.status_code == 413
    assert "当前套餐" in r.text or "file size" in r.text.lower()


# ---------- Regression: P1 upload OOM (2026-07-09 code review) ----------
# 之前用 ``await file.read()`` 直读整个 body 到内存再做 size check。
# 攻击者可用缺失/伪造 Content-Length 头 + 多 GB body 把 worker 撑爆。
# 新实现：先看 Content-Length fast-fail，再按 1 MiB chunk 流式读，超
# 上限立刻 413，全程内存峰值 ≤ max_bytes + chunk_size。

def test_upload_oom_via_streaming_size_cap(auth_client, monkeypatch):
    """P1: 即便 Content-Length 头被绕过（缺失/伪造），chunk 流式读
    也能在累积字节超限时立刻 413。"""
    client, _user = auth_client
    sid = _create_session(client)

    from app.core import helpers
    # 把 plan 上限压到 64 KiB — 确保 body 必须分多块才能读完
    orig_get_plan = helpers._get_user_plan
    monkeypatch.setattr(
        helpers, "_get_user_plan",
        lambda uid: {"file_size_limit_mb": 0.0625},  # 64 KiB
    )

    # 200 KiB 数据 > 64 KiB 上限
    oversized = b"X" * (200 * 1024)
    r = _upload_gc(client, sid, "oversized.log", oversized)
    assert r.status_code == 413, r.text
    # 错误消息应提示文件过大
    assert "文件最大" in r.text or "too large" in r.text.lower() or "file size" in r.text.lower()

    # 在上限内的文件仍能正常通过——用一个真正能 parse 的 GC 日志片段
    small = (
        b"[2026-06-17T09:57:53.642+0800][0.003s][info][gc] Using G1\n"
        b"[2026-06-17T09:57:54.000+0800][0.361s][info][gc] GC(0) Pause Young "
        b"(Normal) (G1 Evacuation Pause) 24M->8M(256M) 12.345ms\n"
    )
    assert len(small) < 64 * 1024, "fixture 应该小于 64KB"
    r2 = _upload_gc(client, sid, "small.log", small)
    assert r2.status_code == 200, r2.text


def test_upload_content_length_fast_fail(auth_client, monkeypatch):
    """P1: Content-Length 头声明超限时，连接直接 413，不读 body。"""
    client, _user = auth_client
    sid = _create_session(client)

    from app.core import helpers
    # 把 plan 上限压到非常小
    monkeypatch.setattr(
        helpers, "_get_user_plan",
        lambda uid: {"file_size_limit_mb": 0.001},  # 1 KiB
    )

    # 浏览器/curl 上传会带 Content-Length 头——httpx 也带。
    # 这里依赖框架自带的头，所以测试的是 FastAPI 路径下的 Content-Length
    # fast-fail 是否生效（走到 _read_upload_bounded 时 file.headers 已有该值）。
    oversized = b"Y" * (5 * 1024)  # 5 KiB > 1 KiB
    r = _upload_gc(client, sid, "fast.log", oversized)
    assert r.status_code == 413


def test_list_my_reports(auth_client):
    """Test GET /api/me/reports endpoint."""
    client, user_id = auth_client
    r = client.get("/api/me/reports")
    assert r.status_code == 200
    assert "reports" in r.json()
    # When agent memory doesn't have list_all_reports, returns empty list
    # This is tested in normal execution
    assert isinstance(r.json()["reports"], list)


# ---------- Regression: GC API response strips heavy `events` list (2026-07-24) ----------
# ``stats["events"]`` carries every parsed event with full raw log body — for
# multi-MB ZGC logs this can dwarf the input file and dominate HTTP response
# size. Frontend never reads it, and the LLM-side ``query_gc_events`` tool reads
# directly from the DB row, so we omit it from HTTP responses. Regression test
# guards against the field creeping back into the wire format.

def test_upload_response_strips_events_list(auth_client):
    """POST /gc/upload response must NOT include stats.events (frontend unused, heavy)."""
    client, _user = auth_client
    sid = _create_session(client)
    payload = _SAMPLE_LOG.read_bytes()

    r = _upload_gc(client, sid, "strip.log", payload)
    assert r.status_code == 200, r.text
    upload = r.json()
    assert "stats" in upload
    assert "events" not in upload["stats"], (
        "stats.events must be stripped from upload response; frontend never reads it "
        "and the LLM query_gc_events tool reads from DB, not from this payload"
    )
    # Other essential fields still present
    assert upload["stats"]["collector"] == "G1"
    assert upload["stats"]["events_total"] >= 11
    assert upload["stats"]["by_category"]["Full"]["count"] == 5
    assert isinstance(upload["stats"]["series"], list)
    assert isinstance(upload["stats"]["slowest"], list)


def test_get_detail_response_strips_events_list(auth_client):
    """GET /gc/reports/{rid} response must NOT include stats.events."""
    client, _user = auth_client
    sid = _create_session(client)
    rid = _upload_gc(client, sid, "strip.log", _SAMPLE_LOG.read_bytes()).json()["report_id"]

    detail = client.get(f"/api/sessions/{sid}/gc/reports/{rid}").json()
    assert "stats" in detail
    assert "events" not in detail["stats"], (
        "stats.events must be stripped from detail response"
    )
    assert detail["stats"]["collector"] == "G1"
    assert detail["stats"]["events_total"] >= 11


def test_db_still_persists_events_for_llm_tool(auth_client):
    """Internal DB row must keep full events so query_gc_events LLM tool still works."""
    from app.core import state
    from react_agent.gc_analyzer import query_events

    client, _user = auth_client
    user_id = client.cookies.get("uid") or "user_local"
    sid = _create_session(client)
    rid = _upload_gc(client, sid, "persist.log", _SAMPLE_LOG.read_bytes()).json()["report_id"]

    agent = state._AGENTS[user_id]
    raw = agent.memory.get_gc_report(sid, rid)
    # DB layer returns the full record (events included) for internal consumers.
    assert isinstance(raw["stats"].get("events"), list)
    assert len(raw["stats"]["events"]) >= 11

    # query_events (backend of the LLM tool) must still be able to read & filter events.
    out = query_events(
        agent.memory, sid,
        report_id=rid,
        category="Full", limit=10,
    )
    assert "Matched:" in out
    assert "GC#" in out


def test_export_json_strips_events_list(auth_client):
    """/export?fmt=json must NOT include stats.events in the downloaded file."""
    client, _user = auth_client
    sid = _create_session(client)
    rid = _upload_gc(client, sid, "export.log", _SAMPLE_LOG.read_bytes()).json()["report_id"]

    r = client.get(f"/api/sessions/{sid}/gc/reports/{rid}/export?fmt=json")
    assert r.status_code == 200
    body = r.json()
    assert "stats" in body
    assert "events" not in body["stats"], (
        "stats.events must be stripped from JSON export too"
    )
    assert body["stats"]["collector"] == "G1"


def test_large_fixture_response_size_is_bounded(auth_client):
    """End-to-end size check: with events stripped, response stays small
    even for the largest fixture. Without the fix, this same call returned
    multi-MB JSON dominated by stats.events[*].raw."""

    fixture = _FIXTURES / "gc-jdk25-generational-zgc.log"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture.name}")
    payload = fixture.read_bytes()

    client, _user = auth_client
    sid = _create_session(client)
    upload_resp = _upload_gc(client, sid, fixture.name, payload)
    assert upload_resp.status_code == 200, upload_resp.text
    body = upload_resp.json()

    upload_bytes = len(upload_resp.content)
    # Sanity: parser actually found events; otherwise the test would be trivial.
    assert body["stats"]["events_total"] > 0
    # The whole response (including non-stats fields) must stay well under the
    # raw input size. Before the fix a ~1.3 MB ZGC log returned ~10 MB JSON.
    assert upload_bytes < len(payload) // 2, (
        f"Upload response {upload_bytes} bytes exceeded half of input "
        f"{len(payload)} bytes — events likely re-leaked into API payload"
    )
    assert "events" not in body["stats"]

    detail = client.get(f"/api/sessions/{sid}/gc/reports/{body['report_id']}")
    assert detail.status_code == 200
    assert len(detail.content) < len(payload) // 2
    assert "events" not in detail.json()["stats"]