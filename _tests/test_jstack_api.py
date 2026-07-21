"""End-to-end tests for the jstack reports API + analyzer unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent
_SAMPLE = _FIXTURES / "sample_jstack.txt"

_JDK_HEADER = "Full thread dump OpenJDK 64-Bit Server VM (25.402-b08 mixed mode):\n\n"


def _conn_pool_thread(i):
    return (
        f'"http-nio-8080-exec-{i}" #{i} daemon prio=5 os_prio=0 tid=0x00007f8e0c00a{i:03x} nid=0x3a{i:02x} waiting on condition [0x00007f8df7bfb000]\n'
        "   java.lang.Thread.State: TIMED_WAITING (parking)\n"
        "    at sun.misc.Unsafe.park(Native Method)\n"
        "    at java.util.concurrent.locks.LockSupport.parkNanos(LockSupport.java:215)\n"
        "    at com.zaxxer.hikari.util.ConcurrentBag.borrow(ConcurrentBag.java:151)\n"
        "    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:158)\n"
        "    at com.example.dao.UserDao.query(UserDao.java:33)\n"
        "    at java.lang.Thread.run(Thread.java:745)\n\n"
    )


def _cpu_thread(i):
    return (
        f'"worker-{i}" #{i} prio=5 os_prio=0 tid=0x00007f8e0c00b{i:03x} nid=0x3b{i:02x} runnable [0x00007f8df79f8000]\n'
        "   java.lang.Thread.State: RUNNABLE\n"
        "    at com.example.compute.MatrixEngine.multiply(MatrixEngine.java:88)\n"
        "    at com.example.compute.Task.run(Task.java:20)\n"
        "    at java.lang.Thread.run(Thread.java:745)\n\n"
    )


def _io_runnable_thread(i):
    return (
        f'"io-{i}" #{i} prio=5 os_prio=0 tid=0x00007f8e0c00c{i:03x} nid=0x3c{i:02x} runnable [0x00007f8df78f7000]\n'
        "   java.lang.Thread.State: RUNNABLE\n"
        "    at java.net.SocketInputStream.socketRead0(Native Method)\n"
        "    at com.example.net.Client.read(Client.java:50)\n"
        "    at java.lang.Thread.run(Thread.java:745)\n\n"
    )


def _io_wait_thread(i):
    # WAITING/TIMED_WAITING 且栈顶为 IO 读取（非连接池）
    return (
        f'"io-wait-{i}" #{i} prio=5 os_prio=0 tid=0x00007f8e0c00d{i:03x} nid=0x3d{i:02x} runnable [0x00007f8df78f7000]\n'
        "   java.lang.Thread.State: TIMED_WAITING (on object monitor)\n"
        "    at java.net.SocketInputStream.socketRead0(Native Method)\n"
        "    at com.example.rpc.Downstream.call(Downstream.java:77)\n"
        "    at java.lang.Thread.run(Thread.java:745)\n\n"
    )


def _pool_thread(pool, i):
    # 指定线程池名的空闲 worker（栈顶为 park，非 IO / 非借连接）
    return (
        f'"{pool}-{i}" #{i} daemon prio=5 os_prio=0 tid=0x00007f8e0c00e{i:04x} nid=0x3e{i:03x} waiting on condition [0x00007f8df78f7000]\n'
        "   java.lang.Thread.State: WAITING (parking)\n"
        "    at sun.misc.Unsafe.park(Native Method)\n"
        "    at java.util.concurrent.locks.LockSupport.park(LockSupport.java:175)\n"
        "    at java.util.concurrent.LinkedBlockingQueue.take(LinkedBlockingQueue.java:442)\n"
        "    at java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1067)\n"
        "    at java.lang.Thread.run(Thread.java:745)\n\n"
    )


# ---------- Pure-function unit tests (no app/db) ----------

class TestParse:
    def test_parse_sample(self):
        from react_agent.jstack_analyzer import parse_jstack
        text = _SAMPLE.read_text(encoding="utf-8")
        parsed = parse_jstack(text)
        assert parsed["total_threads"] == 8
        assert len(parsed["deadlocks"]) == 2

    def test_parse_threads(self):
        from react_agent.jstack_analyzer import parse_jstack
        parsed = parse_jstack(_SAMPLE.read_text(encoding="utf-8"))
        states = {t["state"] for t in parsed["threads"]}
        assert "WAITING" in states
        assert "RUNNABLE" in states
        assert "BLOCKED" in states
        assert any(t["daemon"] for t in parsed["threads"])

    def test_deadlock_detection(self):
        from react_agent.jstack_analyzer import parse_jstack
        parsed = parse_jstack(_SAMPLE.read_text(encoding="utf-8"))
        deadlocks = parsed["deadlocks"]
        assert deadlocks[0]["thread"] == "pool-1-thread-1"
        assert deadlocks[0]["held_by"] == "pool-1-thread-2"
        assert deadlocks[1]["thread"] == "pool-1-thread-2"
        assert deadlocks[1]["held_by"] == "pool-1-thread-1"

    def test_empty_input(self):
        from react_agent.jstack_analyzer import parse_jstack
        parsed = parse_jstack("")
        assert parsed["total_threads"] == 0

    def test_no_deadlock(self):
        from react_agent.jstack_analyzer import parse_jstack
        text = _SAMPLE.read_text(encoding="utf-8")
        idx = text.find("Found one Java-level deadlock")
        if idx > 0:
            text = text[:idx]
        parsed = parse_jstack(text)
        assert len(parsed["deadlocks"]) == 0
        assert parsed["total_threads"] == 8

class TestStats:
    def test_compute_stats(self):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack
        parsed = parse_jstack(_SAMPLE.read_text(encoding="utf-8"))
        stats = compute_stats(parsed)
        assert stats["total_threads"] == 8
        assert stats["deadlock_count"] == 2
        assert stats["by_state"].get("BLOCKED") == 1
        assert stats["by_state"].get("RUNNABLE") == 3
        assert stats["avg_stack_depth"] > 0
        assert stats["max_stack_depth"] > 0

    def test_flamegraph_data(self):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack
        parsed = parse_jstack(_SAMPLE.read_text(encoding="utf-8"))
        stats = compute_stats(parsed)
        fg = stats["flamegraph"]
        assert fg["name"] == "root"
        assert fg["value"] == 3  # 3 RUNNABLE threads
        assert len(fg["children"]) > 0

        def check(n):
            assert "name" in n
            assert "value" in n
            assert isinstance(n["children"], list)
            for c in n["children"]:
                assert c["value"] <= n["value"]
                check(c)

        check(fg)
        total = sum(c["value"] for c in fg["children"])
        assert total == fg["value"]

    def test_summary_for_llm(self):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack, summary_for_llm
        parsed = parse_jstack(_SAMPLE.read_text(encoding="utf-8"))
        summary = summary_for_llm(compute_stats(parsed))
        assert "Total Threads" in summary
        assert "Deadlock" in summary
        assert "pool-1-thread-1" in summary
        assert len(summary) > 100

class TestExtractThread:
    def test_extract_by_name(self):
        from react_agent.jstack_analyzer import _extract_thread_text
        result = _extract_thread_text(_SAMPLE.read_text(encoding="utf-8"), "pool-1-thread-2")
        assert result is not None
        assert "BLOCKED" in result
        assert "AccountService.transfer" in result

    def test_extract_by_nid(self):
        from react_agent.jstack_analyzer import _extract_thread_text
        result = _extract_thread_text(_SAMPLE.read_text(encoding="utf-8"), "0x3a2b")
        assert result is not None
        assert "WAITING" in result

    def test_extract_not_found(self):
        from react_agent.jstack_analyzer import _extract_thread_text
        result = _extract_thread_text(_SAMPLE.read_text(encoding="utf-8"), "nonexistent-thread")
        assert result is None

# ---------- Rule diagnosis unit tests ----------
class TestDiagnosis:
    def _diag(self, text):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack
        return compute_stats(parse_jstack(text))["diagnosis"]

    def test_diagnosis_present_in_stats(self):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack
        stats = compute_stats(parse_jstack(_SAMPLE.read_text(encoding="utf-8")))
        assert "diagnosis" in stats
        d = stats["diagnosis"]
        assert set(d.keys()) == {"overall", "findings", "recommendations_zh", "recommendations_en"}

    def test_deadlock_finding(self):
        d = self._diag(_SAMPLE.read_text(encoding="utf-8"))
        rules = {f["rule"] for f in d["findings"]}
        assert "deadlock" in rules
        dl = next(f for f in d["findings"] if f["rule"] == "deadlock")
        assert dl["severity"] == "high"
        assert d["overall"] == "critical"

    def test_empty_dump_healthy(self):
        d = self._diag("")
        assert d["overall"] == "health"
        assert d["findings"] == []

    def test_conn_pool_exhaustion(self):
        text = _JDK_HEADER + "".join(_conn_pool_thread(i) for i in range(1, 7))
        d = self._diag(text)
        f = next(f for f in d["findings"] if f["rule"] == "conn_pool_exhaustion")
        assert f["severity"] == "high"  # 6 threads >= CONN_POOL_CRIT_COUNT

    def test_conn_pool_medium(self):
        text = _JDK_HEADER + "".join(_conn_pool_thread(i) for i in range(1, 3)) + "".join(_cpu_thread(i) for i in range(10, 30))
        d = self._diag(text)
        f = next(f for f in d["findings"] if f["rule"] == "conn_pool_exhaustion")
        assert f["severity"] == "medium"  # 2 waiters, ratio < 10%

    def test_cpu_intensive(self):
        text = _JDK_HEADER + "".join(_cpu_thread(i) for i in range(1, 8))
        d = self._diag(text)
        rules = {f["rule"] for f in d["findings"]}
        assert "cpu_intensive" in rules

    def test_pseudo_runnable_not_cpu(self):
        # 全部是栈顶 socketRead0 的伪 RUNNABLE，不应报 cpu_intensive
        text = _JDK_HEADER + "".join(_io_runnable_thread(i) for i in range(1, 8))
        d = self._diag(text)
        rules = {f["rule"] for f in d["findings"]}
        assert "cpu_intensive" not in rules

    def test_summary_includes_diagnosis(self):
        from react_agent.jstack_analyzer import compute_stats, parse_jstack, summary_for_llm
        summary = summary_for_llm(compute_stats(parse_jstack(_SAMPLE.read_text(encoding="utf-8"))))
        assert "Thread Diagnosis" in summary
        assert "Deadlock detected" in summary

    def test_thread_count_high(self):
        # >2000 → high; 用轻量 pool 线程堆到 2001
        text = _JDK_HEADER + "".join(_pool_thread("bulk", i) for i in range(1, 2002))
        d = self._diag(text)
        f = next(f for f in d["findings"] if f["rule"] == "thread_count_high")
        assert f["severity"] == "high"

    def test_thread_count_medium(self):
        # >1000 且 <=2000 → medium
        text = _JDK_HEADER + "".join(_pool_thread("bulk", i) for i in range(1, 1002))
        d = self._diag(text)
        f = next(f for f in d["findings"] if f["rule"] == "thread_count_high")
        assert f["severity"] == "medium"

    def test_thread_pool_bloat(self):
        # 单池 501 个线程 + 少量其它线程，占比 >50% 且 >500 → medium
        text = _JDK_HEADER + "".join(_pool_thread("leaky-pool", i) for i in range(1, 502)) \
            + "".join(_cpu_thread(i) for i in range(600, 610))
        d = self._diag(text)
        f = next(f for f in d["findings"] if f["rule"] == "thread_pool_bloat")
        assert f["severity"] == "medium"
        assert "leaky-pool" in f["detail_zh"]

    def test_io_wait_concentration(self):
        text = _JDK_HEADER + "".join(_io_wait_thread(i) for i in range(1, 5))
        d = self._diag(text)
        rules = {f["rule"] for f in d["findings"]}
        assert "io_wait_concentration" in rules


# ---------- API end-to-end ----------

def _create_session(client, title="jstack-api"):
    r = client.post("/api/sessions", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]

def _upload(client, sid, name="threads.txt", payload: bytes | None = None):
    payload = payload if payload is not None else _SAMPLE.read_bytes()
    return client.post(
        f"/api/sessions/{sid}/jstack/upload",
        files={"file": (name, payload, "text/plain")},
    )


class TestAPI:
    def test_upload_jstack(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        r = _upload(client, sid)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "report_id" in data
        assert "file_id" in data
        assert data["stats"]["total_threads"] == 8

    def test_list_reports(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        _upload(client, sid)
        r = client.get(f"/api/sessions/{sid}/jstack/reports")
        assert r.status_code == 200
        assert len(r.json()["reports"]) >= 1

    def test_get_report(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        rid = _upload(client, sid).json()["report_id"]
        r = client.get(f"/api/sessions/{sid}/jstack/reports/{rid}")
        assert r.status_code == 200
        assert r.json()["stats"]["total_threads"] == 8

    def test_delete_report(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        rid = _upload(client, sid).json()["report_id"]
        r = client.delete(f"/api/sessions/{sid}/jstack/reports/{rid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_unsupported_extension(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        r = client.post(
            f"/api/sessions/{sid}/jstack/upload",
            files={"file": ("bad.exe", b"not a dump", "application/octet-stream")},
        )
        assert r.status_code == 400
        assert "不支持" in r.text

    def test_empty_file(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        r = _upload(client, sid, "empty.txt", b"")
        assert r.status_code == 422
        assert "未能解析" in r.text

    def test_get_non_existent_report(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        r = client.get(f"/api/sessions/{sid}/jstack/reports/non_existent")
        assert r.status_code == 404
        assert "not found" in r.text.lower()

    def test_upload_file_too_large(self, auth_client, monkeypatch):
        client, _ = auth_client
        sid = _create_session(client)
        from app.core import helpers
        def mock_get_plan(user_id):
            return {"file_size_limit_mb": 0.001}  # 1KB limit
        monkeypatch.setattr(helpers, "_get_user_plan", mock_get_plan)

        large_data = b"X" * (2 * 1024)
        r = _upload(client, sid, "large.txt", large_data)
        assert r.status_code == 413
        assert "当前套餐" in r.text


    def test_delete_nonexistent_report(self, auth_client):
        client, _ = auth_client
        sid = _create_session(client)
        r = client.delete(f"/api/sessions/{sid}/jstack/reports/non_existent")
        # delete returns {"deleted": False} for non-existent, not 404
        assert r.status_code == 200
        assert r.json()["deleted"] is False
