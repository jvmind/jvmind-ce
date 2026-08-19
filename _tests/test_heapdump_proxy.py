"""Heapdump 反向代理（query-service）端到端单测。

策略：
- monkeypatch 共享 httpx client 为一个指向本地 fake server 的客户端；
- 先上传一份 DONE 报告 (通过直接 DB 写入避免跑真实 Worker)；
- 覆盖：同步代理、异步 submit + poll + cancel、409 未就绪、403 跨用户、
- 错误映射、异步任务所有权。heapdump 页面内查询不扣 api_call_count。
  403 跨用户、504 超时、MAT_QUERY_OOM 识别、overview 缓存命中。
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def _make_hprof(size: int = 4096) -> bytes:
    header = b"JAVA PROFILE 1.0.2\x00"
    return header + b"\x00" * max(0, size - len(header))


def _upload_full(client, isolated_storage, hprof_bytes: bytes, session_id: str):
    r = client.post("/api/heapdump-reports/uploads")
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    chunk_size = 4096
    for i in range(0, len(hprof_bytes), chunk_size):
        block = hprof_bytes[i : i + chunk_size]
        md5 = hashlib.md5(block).hexdigest()
        r = client.put(
            f"/api/heapdump-reports/uploads/{uid}/chunks/{i // chunk_size}",
            content=block, headers={"Content-MD5": md5},
        )
        assert r.status_code == 200
    r = client.post(f"/api/heapdump-reports/uploads/{uid}/complete",
                    json={"filename": "app.hprof", "session_id": session_id})
    assert r.status_code == 200, r.text
    return r.json()["report_id"]


def _set_report_done(report_id: str, dump_dir: str, stats: dict | None = None):
    from react_agent.db import SessionLocal
    from react_agent.models import HeapdumpReportModel
    db = SessionLocal()
    try:
        r = db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id).first()
        r.status = "DONE"
        r.progress = 1.0
        r.dump_dir = dump_dir
        r.error = None
        if stats is not None:
            r.stats = json.dumps(stats, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


def _set_report_done(report_id: str, dump_dir: str, stats: dict | None = None):
    from react_agent.db import SessionLocal
    from react_agent.models import HeapdumpReportModel
    db = SessionLocal()
    try:
        r = db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id).first()
        r.status = "DONE"
        r.progress = 1.0
        r.dump_dir = dump_dir
        r.error = None
        if stats is not None:
            r.stats = json.dumps(stats, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


# ---------- Fake Java query-service ----------

class _FakeJava:
    """In-process fake HTTP server that mimics the MAT query-service."""
    def __init__(self):
        self.calls = []  # list of (method, path, qs, body)
        self.handlers = {}
        self._server = None
        self._thread = None
        self._resp_overrides = {}  # path -> (status, json) or exception class
        self.dump_dir_seen = None

    def start(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send(self, status, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                qs = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(u.query).items()}
                fake.calls.append((method, u.path, qs, body))
                fake.dump_dir_seen = qs.get("dumpDir")
                override = fake._resp_overrides.get(u.path)
                if override is not None:
                    status, obj = override
                    self._send(status, obj)
                    return
                handler = fake.handlers.get((method, u.path))
                if handler:
                    status, obj = handler(qs, body)
                    self._send(status, obj)
                    return
                self._send(404, {"error": f"no handler for {method} {u.path}"})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def override(self, path: str, status: int, obj):
        self._resp_overrides[path] = (status, obj)

    def clear_overrides(self):
        self._resp_overrides.clear()


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    from app.routes import heapdump_upload
    tmp_chunks = tmp_path / "chunks"
    storage = tmp_path / "storage"
    tmp_chunks.mkdir()
    storage.mkdir()
    monkeypatch.setattr(heapdump_upload, "_UPLOAD_TMP_ROOT", tmp_chunks)
    monkeypatch.setattr(heapdump_upload, "_STORAGE_ROOT", storage)
    return {"chunks": tmp_chunks, "storage": storage}


@pytest.fixture
def fake_java(monkeypatch):
    from app.routes import heapdump_proxy
    fake = _FakeJava()
    url = fake.start()
    monkeypatch.setattr(heapdump_proxy, "_BASE", url)
    # Force-close the shared client so the new _BASE takes effect
    asyncio.get_event_loop().run_until_complete(heapdump_proxy.close_mat_client())
    yield fake
    asyncio.get_event_loop().run_until_complete(heapdump_proxy.close_mat_client())
    fake.stop()


@pytest.fixture
def done_report(auth_client, isolated_storage, tmp_path):
    from app.routes import heapdump_upload
    client, user = auth_client
    sid_resp = client.post("/api/sessions", json={})
    assert sid_resp.status_code == 200, sid_resp.text
    sid = sid_resp.json()["id"]
    payload = _make_hprof(8192)
    rid = _upload_full(client, isolated_storage, payload, sid)
    # P1 (2026-07-09 code review): dump_dir 必须落在 _STORAGE_ROOT
    # 之下（生产路径就是这样，测试要保持一致）。原测试用 tmp_path/"dumps"
    # 是偶然绕过了这个 invariant 的 bug。
    dump_dir = isolated_storage["storage"] / rid
    # 上传端点已创建 dump_dir；这里仅写 hprof 文件
    (dump_dir / "app.hprof").write_bytes(payload)
    _set_report_done(rid, str(dump_dir), stats={"usedHeap": 123456, "jvmInfo": {"javaVersion": "17"}})
    return rid, str(dump_dir)


# ---------- Pre-checks ----------

def test_proxy_404_on_missing_report(auth_client, fake_java):
    client, _ = auth_client
    r = client.get("/api/heapdump-reports/hd_nope/overview")
    assert r.status_code == 404


def test_proxy_409_when_parsing(auth_client, isolated_storage, fake_java):
    client, _ = auth_client
    sid = client.post("/api/sessions", json={}).json()["id"]
    rid = _upload_full(client, isolated_storage, _make_hprof(8192), sid)
    # status stays UPLOADED -> not DONE
    r = client.get(f"/api/heapdump-reports/{rid}/overview")
    assert r.status_code == 409
    body = r.json()
    assert "Parsing" in body["detail"] or "解析" in body["detail"]


def test_proxy_403_other_user(auth_client, admin_client, isolated_storage, fake_java, tmp_path):
    client, _ = auth_client
    other_client, other_user = admin_client
    # Other user creates their own session + uploads a file
    sid2 = other_client.post("/api/sessions", json={}).json()["id"]
    rid2 = _upload_full(other_client, isolated_storage, _make_hprof(8192), sid2)
    dd2 = isolated_storage["storage"] / rid2
    (dd2 / "app.hprof").write_bytes(_make_hprof(8192))
    _set_report_done(rid2, str(dd2))
    # First user tries to access other's report -> 403 or 404
    r = client.get(f"/api/heapdump-reports/{rid2}/overview")
    assert r.status_code in (403, 404)


# ---------- Sync proxy ----------

def test_overview_cached_stats(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.get(f"/api/heapdump-reports/{rid}/overview?full=true")
    assert r.status_code == 200, r.text
    # No HTTP calls should have been made to Java (served from DB stats)
    assert fake_java.calls == []
    assert r.json()["usedHeap"] == 123456


def test_overview_proxies_full_false(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/overview", 200, {"heapUsed": 999})
    r = client.get(f"/api/heapdump-reports/{rid}/overview")
    assert r.status_code == 200, r.text
    assert r.json() == {"heapUsed": 999}
    assert fake_java.dump_dir_seen == dd
    assert "full" not in fake_java.calls[-1][2]


def test_sync_endpoints_inject_dumpdir_and_params(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/histogram", 200, [{"className": "byte[]", "count": 1}])
    r = client.get(f"/api/heapdump-reports/{rid}/histogram?top=10&sort=retained")
    assert r.status_code == 200
    qs = fake_java.calls[-1][2]
    assert qs["dumpDir"] == dd
    assert qs["top"] == "10"
    assert qs["sort"] == "retained"


def test_sync_object_requires_id(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.get(f"/api/heapdump-reports/{rid}/object")
    # FastAPI Query validation -> 422
    assert r.status_code == 422


def test_oql_requires_q(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.post(f"/api/heapdump-reports/{rid}/oql", json={"limit": 50})
    assert r.status_code == 400


def test_list_objects_forwards_direction_and_object_id(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/list-objects", 200, {"resultSetId": "rs-42", "rows": []})
    r = client.post(
        f"/api/heapdump-reports/{rid}/list-objects",
        json={"direction": "out", "objectId": 1234},
    )
    assert r.status_code == 200, r.text
    assert r.json()["resultSetId"] == "rs-42"
    qs = fake_java.calls[-1][2]
    assert qs["dumpDir"] == dd
    sent = json.loads(fake_java.calls[-1][3])
    assert sent == {"direction": "out", "objectId": 1234}


def test_list_objects_rejects_bad_body(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    # Missing objectId
    r = client.post(f"/api/heapdump-reports/{rid}/list-objects", json={"direction": "out"})
    assert r.status_code == 400
    # Bad direction
    r = client.post(f"/api/heapdump-reports/{rid}/list-objects",
                    json={"direction": "sideways", "objectId": 1})
    assert r.status_code == 400
    # Non-int objectId
    r = client.post(f"/api/heapdump-reports/{rid}/list-objects",
                    json={"direction": "in", "objectId": "abc"})
    assert r.status_code == 400


def test_array_elements_forwards_id_top_offset(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/array-elements", 200, {
        "kind": "array-elements", "totalRows": 16, "returned": 5,
        "rows": [{"objectId": 6218, "label": "x", "className": "y"}],
    })
    r = client.get(f"/api/heapdump-reports/{rid}/array-elements?id=6208&top=5&offset=0")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "array-elements"
    qs = fake_java.calls[-1][2]
    assert qs["dumpDir"] == dd
    assert qs["id"] == "6208"
    assert qs["top"] == "5"
    assert qs["offset"] == "0"


def test_array_elements_requires_id(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.get(f"/api/heapdump-reports/{rid}/array-elements")
    assert r.status_code == 422  # FastAPI Query(...) validation


def test_collection_entries_forwards_and_returns_map_shape(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/collection-entries", 200, {
        "kind": "map-entries", "totalRows": 2, "returned": 2,
        "rows": [
            {"key": {"objectId": 117, "label": "k1"}, "value": {"objectId": 6219, "label": "v1"}},
        ],
    })
    r = client.get(f"/api/heapdump-reports/{rid}/collection-entries?id=6207&top=25&offset=0")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "map-entries"
    qs = fake_java.calls[-1][2]
    assert qs["dumpDir"] == dd
    assert qs["id"] == "6207"
    assert qs["top"] == "25"
    assert qs["offset"] == "0"


def test_collection_entries_requires_id(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.get(f"/api/heapdump-reports/{rid}/collection-entries")
    assert r.status_code == 422
    # Negative objectId
    r = client.post(f"/api/heapdump-reports/{rid}/list-objects",
                    json={"direction": "in", "objectId": -1})
    assert r.status_code == 400
    # No Java call should have happened for any rejected request
    assert fake_java.calls == []


def test_references_forwards_top_offset_and_body(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, dd = done_report
    fake_java.override("/references", 200, {
        "rows": [{"objectId": 7, "label": "x", "fieldName": "f"}],
        "totalRows": 1, "returned": 1,
    })
    r = client.post(
        f"/api/heapdump-reports/{rid}/references?top=50&offset=25",
        json={"direction": "in", "objectId": 99},
    )
    assert r.status_code == 200, r.text
    assert r.json()["totalRows"] == 1
    qs = fake_java.calls[-1][2]
    assert qs["dumpDir"] == dd
    assert qs["top"] == "50"
    assert qs["offset"] == "25"
    sent = json.loads(fake_java.calls[-1][3])
    assert sent == {"direction": "in", "objectId": 99}


def test_references_clamps_top_query_param(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    fake_java.override("/references", 200, {"rows": [], "totalRows": 0, "returned": 0})
    # top too large → FastAPI Query le=1000 → 422
    r = client.post(f"/api/heapdump-reports/{rid}/references?top=9999",
                    json={"direction": "out", "objectId": 1})
    assert r.status_code == 422
    # top negative → 422
    r = client.post(f"/api/heapdump-reports/{rid}/references?top=-1",
                    json={"direction": "out", "objectId": 1})
    assert r.status_code == 422


def test_references_rejects_bad_body(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    r = client.post(f"/api/heapdump-reports/{rid}/references", json={"direction": "in"})
    assert r.status_code == 400
    r = client.post(f"/api/heapdump-reports/{rid}/references",
                    json={"direction": "nope", "objectId": 1})
    assert r.status_code == 400
    r = client.post(f"/api/heapdump-reports/{rid}/references", json={"direction": "out", "objectId": 1.5})
    assert r.status_code == 400
    assert fake_java.calls == []


def test_oql_caps_limit(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    fake_java.override("/oql", 200, {"rows": [], "resultSetId": "rs-1"})
    r = client.post(f"/api/heapdump-reports/{rid}/oql",
                    json={"q": "SELECT * FROM java.lang.String s", "limit": 99999})
    assert r.status_code == 200
    body_sent = json.loads(fake_java.calls[-1][3])
    assert body_sent["limit"] == 500


def test_proxy_504_on_connect_error(auth_client, monkeypatch, done_report):
    """When Java is unreachable, we map to 504 MAT_UNAVAILABLE."""
    from app.routes import heapdump_proxy
    client, _ = auth_client
    rid, _ = done_report
    # Point at a non-routable port (hopefully nothing listens)
    monkeypatch.setattr(heapdump_proxy, "_BASE", "http://127.0.0.1:1")
    asyncio.get_event_loop().run_until_complete(heapdump_proxy.close_mat_client())
    r = client.get(f"/api/heapdump-reports/{rid}/histogram?top=5")
    assert r.status_code == 504
    asyncio.get_event_loop().run_until_complete(heapdump_proxy.close_mat_client())


def test_proxy_maps_java_oom(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    fake_java.override("/histogram", 500, {"error": "java.lang.OutOfMemoryError: Java heap space"})
    r = client.get(f"/api/heapdump-reports/{rid}/histogram?top=5")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "MAT_QUERY_OOM"


def test_proxy_maps_java_result_expired(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    fake_java.override("/histogram", 409, {"error": "resultSet not found (expired?)"})
    r = client.get(f"/api/heapdump-reports/{rid}/histogram?top=5&objectSet=rs-xxx")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MAT_RESULT_EXPIRED"


# ---------- Async submit + poll + cancel ----------

def test_async_submit_then_poll_and_cancel(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report

    # leak-suspects returns 202
    fake_java.override("/leak-suspects", 202, {"taskId": "t-1", "status": "RUNNING"})
    r = client.post(f"/api/heapdump-reports/{rid}/leak-suspects")
    assert r.status_code == 202, r.text
    assert r.json()["taskId"] == "t-1"
    # dumpDir was injected
    assert fake_java.dump_dir_seen is not None

    # poll returns RUNNING then DONE
    poll_responses = iter([
        (200, {"taskId": "t-1", "status": "RUNNING", "progress": 0.5}),
        (200, {"taskId": "t-1", "status": "DONE", "result": {"suspects": []}}),
    ])
    def poll_handler(qs, body):
        return next(poll_responses)
    fake_java.handlers[("GET", "/tasks/t-1")] = poll_handler
    r = client.get(f"/api/heapdump-reports/{rid}/tasks/t-1")
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    r = client.get(f"/api/heapdump-reports/{rid}/tasks/t-1")
    assert r.status_code == 200 and r.json()["status"] == "DONE"

    # After DONE the task owner is evicted; re-poll of unknown should be allowed but Java returns 404
    fake_java.clear_overrides()
    fake_java.override("/tasks/t-1", 404, {"error": "task not found"})
    r = client.get(f"/api/heapdump-reports/{rid}/tasks/t-1")
    # task id is not owned anymore -> 404 from Java mapped to 404
    assert r.status_code == 404


def test_async_task_ownership_cross_user(auth_client, admin_client, fake_java, done_report, isolated_storage, tmp_path):
    client, _ = auth_client
    other_client, other_user = admin_client
    rid, _ = done_report
    fake_java.override("/leak-suspects", 202, {"taskId": "t-2", "status": "RUNNING"})
    r = client.post(f"/api/heapdump-reports/{rid}/leak-suspects")
    assert r.status_code == 202
    # Other user's own report
    sid2 = other_client.post("/api/sessions", json={}).json()["id"]
    rid2 = _upload_full(other_client, isolated_storage, _make_hprof(8192), sid2)
    dd2 = isolated_storage["storage"] / rid2
    (dd2 / "app.hprof").write_bytes(_make_hprof(8192))
    _set_report_done(rid2, str(dd2))
    # Other user trying to poll the first user's task_id against their own report
    fake_java.override("/tasks/t-2", 200, {"taskId": "t-2", "status": "RUNNING"})
    r = other_client.get(f"/api/heapdump-reports/{rid2}/tasks/t-2")
    # _check_task_owner fires (task belongs to userA + rid, polled by userB + rid2) -> 403
    assert r.status_code == 403


def test_async_cancel(auth_client, fake_java, done_report):
    client, _ = auth_client
    rid, _ = done_report
    fake_java.override("/leak-suspects", 202, {"taskId": "t-3", "status": "RUNNING"})
    client.post(f"/api/heapdump-reports/{rid}/leak-suspects")
    fake_java.override("/tasks/t-3", 200, {"cancelled": True})
    r = client.delete(f"/api/heapdump-reports/{rid}/tasks/t-3")
    assert r.status_code == 200
    assert r.json()["cancelled"] is True


# ---------- Regression: P1 dumpDir containment (2026-07-09 code review) ----------
# Java query-service 接收 dumpDir 后会打开 hprof + 索引。如果 DB
# 损坏或 migration bug 让 dump_dir 落到 _STORAGE_ROOT 之外，旧的代码
# 会原样转发到 Java → 任意文件读取。新的 _assert_dump_dir_within_storage
# 在 _load_report 时做 resolve + is_relative_to 校验，越界直接 500。

def test_proxy_500_when_dump_dir_outside_storage(auth_client, isolated_storage, fake_java, tmp_path):
    client, user = auth_client
    sid = client.post("/api/sessions", json={}).json()["id"]
    rid = _upload_full(client, isolated_storage, _make_hprof(8192), sid)
    # 模拟 dump_dir 被 DB / migration 错写到 _STORAGE_ROOT 之外
    outside = tmp_path / "outside" / rid
    outside.mkdir(parents=True)
    (outside / "app.hprof").write_bytes(_make_hprof(8192))
    _set_report_done(rid, str(outside))
    # 应被 containment check 拦下，不转发到 Java
    r = client.get(f"/api/heapdump-reports/{rid}/overview")
    assert r.status_code == 500
    assert "dump_dir" in r.text or "storage" in r.text.lower() or "路径异常" in r.text or "outside" in r.text.lower()


def test_proxy_500_when_dump_dir_traversal_escape(auth_client, isolated_storage, fake_java, tmp_path):
    """P1: 路径穿越 `../` 攻击也要被拦下。"""
    client, user = auth_client
    sid = client.post("/api/sessions", json={}).json()["id"]
    rid = _upload_full(client, isolated_storage, _make_hprof(8192), sid)
    # 尝试通过相对路径逃出 storage root
    storage = isolated_storage["storage"]
    escape_path = str(storage / ".." / "etc" / "passwd")
    _set_report_done(rid, escape_path)
    r = client.get(f"/api/heapdump-reports/{rid}/overview")
    assert r.status_code == 500
