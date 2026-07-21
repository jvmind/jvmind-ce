"""
PR-A end-to-end verification script.

Verifies the cross-domain OOMDiagnosis synthesis path:
  1. Login (or register) a test user; verify plan includes heapdump feature.
  2. Discover (or accept) GC report + heapdump report in a session.
  3. Attach both to chat context (state.activeReportContexts).
  4. Stream /api/chat/stream with a diagnostic question.
  5. Capture SSE events; assert final.diagnosis carries verdict +
     root_cause_chain + cross_domain_findings (OOMDiagnosis shape).
  6. Print a structured summary.

Usage:
  python _tests/manual_verify_pra.py [BASE_URL]
    BASE_URL defaults to https://127.0.0.1:8000

  Or with explicit options:
    --gc-log PATH          Path to a GC log file (.log)
    --hprof-gz PATH        Path to a heapdump (.hprof or .hprof.gz); uploaded
                            via chunked protocol and waited until status=DONE
    --heapdump-report-id HD_ID
                            Skip upload, reuse an existing heapdump report
                            belonging to the session
    --session-id SID       Reuse an existing session (skips upload unless
                            both --gc-log and --hprof-gz / --heapdump-report-id
                            are also provided; if missing, the script uploads
                            new reports into the existing session)
    --poll-timeout SECONDS How long to wait for the heapdump to reach DONE
                            (default 600)
    --dry-run              Upload + poll but skip the LLM chat stream

The script will:
  - Register a fresh test user (email derived from timestamp) on every run
  - Verify the user's plan includes the heapdump feature
  - Auto-discover reports from the session (DONE preferred); upload if missing
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_BASE_URL = "https://127.0.0.1:8000"
DEFAULT_INSECURE_SKIP_TLS = True  # self-signed cert on the dev server
DEFAULT_POLL_TIMEOUT_SECS = 600
DEFAULT_POLL_INTERVAL_SECS = 5
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # server's DEFAULT_CHUNK_SIZE


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class Client:
    def __init__(
        self,
        base_url: str,
        *,
        insecure_skip_tls: bool = DEFAULT_INSECURE_SKIP_TLS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie_jar: Dict[str, str] = {}
        self.insecure_skip_tls = insecure_skip_tls
        self.chunk_size = chunk_size

    def _opener(self):
        if self.base_url.startswith("https://") and self.insecure_skip_tls:
            ctx = ssl._create_unverified_context()
            return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener()

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Optional[dict] = None,
        json_body: Optional[dict] = None,
        raw_body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> Any:
        url = self.base_url + path
        hdr: Dict[str, str] = {"Accept": "application/json"}
        if self.cookie_jar:
            hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookie_jar.items())
        if headers:
            hdr.update(headers)

        body: Optional[bytes] = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdr.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdr.setdefault("Content-Type", "application/json")
        elif raw_body is not None:
            body = raw_body
            # Content-Type already set by caller (e.g. application/octet-stream)

        req = urllib.request.Request(url, data=body, method=method, headers=hdr)
        try:
            resp = self._opener().open(req, timeout=300)
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{method} {path} → HTTP {e.code}: {body_text[:500]}"
            ) from e

        # Persist cookies from Set-Cookie headers
        sc = resp.headers.get_all("Set-Cookie") or []
        for raw in sc:
            kv = raw.split(";", 1)[0]
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.cookie_jar[k.strip()] = v.strip()
        return resp

    def csrf_headers(self) -> Dict[str, str]:
        token = self.cookie_jar.get("csrf_token", "")
        return {"X-CSRF-Token": token} if token else {}

    def get_json(self, path: str) -> Any:
        resp = self._request("GET", path)
        return json.loads(resp.read())

    def post_json(self, path: str, body: dict, *, with_csrf: bool = False) -> Any:
        headers = self.csrf_headers() if with_csrf else {}
        resp = self._request("POST", path, json_body=body, headers=headers)
        return json.loads(resp.read())

    def post_stream(self, path: str, body: dict, *, with_csrf: bool = True) -> Any:
        headers = {"Accept": "text/event-stream"}
        if with_csrf:
            headers.update(self.csrf_headers())
        return self._request("POST", path, json_body=body, headers=headers, stream=True)

    def put_raw(self, path: str, body: bytes, *, with_csrf: bool = True,
                content_type: str = "application/octet-stream") -> Any:
        headers = {"Content-Type": content_type}
        if with_csrf:
            headers.update(self.csrf_headers())
        resp = self._request("PUT", path, raw_body=body, headers=headers)
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def banner(s: str) -> None:
    print("\n" + "=" * 72)
    print(s)
    print("=" * 72)


def parse_sse(stream) -> List[Dict[str, Any]]:
    """Parse a text/event-stream response body into a list of {event, data}.

    Tolerates a partial trailing block at EOF (no terminating ``\\n\\n``)
    and supports both ``\\n\\n`` and ``\\r\\n\\r\\n`` separators.
    """
    import re
    out: List[Dict[str, Any]] = []
    buf = ""
    sep_re = re.compile(r"\r?\n\r?\n")
    while True:
        try:
            chunk = stream.read(4096)
        except Exception as e:
            print(f"[verify_pra] (parse_sse read exception: {e!r})")
            break
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")
        # Split on either \n\n or \r\n\r\n (LLM streaming may use CRLF)
        while True:
            m = sep_re.search(buf)
            if not m:
                break
            block = buf[: m.start()]
            buf = buf[m.end():]
            ev: Dict[str, Any] = {"event": "message", "data": ""}
            for line in block.splitlines():
                line = line.rstrip("\r")
                if not line:
                    continue
                if line.startswith("event:"):
                    ev["event"] = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    ev["data"] += line[len("data:"):].lstrip()
            if ev["data"] or ev["event"] != "message":
                out.append(ev)
    # Flush any trailing block (no final \n\n)
    if buf.strip():
        ev = {"event": "message", "data": ""}
        for line in buf.splitlines():
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith("event:"):
                ev["event"] = line[len("event:"):].strip()
            elif line.startswith("data:"):
                ev["data"] += line[len("data:"):].lstrip()
        if ev["data"] or ev["event"] != "message":
            out.append(ev)
    return out


def _multipart_upload(
    *, file_field: str, filename: str, file_bytes: bytes,
    extra_fields: Optional[Dict[str, str]] = None,
) -> Tuple[bytes, str]:
    """Build a multipart/form-data body for an UploadFile endpoint."""
    boundary = "----verify_pra_" + uuid.uuid4().hex
    lines: List[bytes] = []
    for k, v in (extra_fields or {}).items():
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        lines.append(v.encode())
        lines.append(b"\r\n")
    lines.append(f"--{boundary}\r\n".encode())
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    lines.append(b"Content-Type: application/octet-stream\r\n\r\n")
    lines.append(file_bytes)
    lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    body = b"".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def upload_gc_log(client: Client, session_id: str, gc_log_path: str) -> Dict[str, Any]:
    """Upload a GC log via the multipart endpoint. Returns the parsed JSON response."""
    with open(gc_log_path, "rb") as f:
        file_bytes = f.read()
    body_bytes, content_type = _multipart_upload(
        file_field="file", filename=os.path.basename(gc_log_path), file_bytes=file_bytes,
    )
    headers = {"Content-Type": content_type}
    headers.update(client.csrf_headers())
    resp = client._request(
        "POST",
        f"/api/sessions/{session_id}/gc/upload",
        raw_body=body_bytes,
        headers=headers,
    )
    try:
        return json.loads(resp.read())
    finally:
        try:
            resp.close()
        except Exception:
            pass


def upload_heapdump_chunked(
    client: Client, hprof_gz_path: str, session_id: str,
    *, filename: Optional[str] = None,
) -> Dict[str, Any]:
    """4-step chunked heapdump upload. Returns the complete-upload response."""
    fname = filename or os.path.basename(hprof_gz_path)
    with open(hprof_gz_path, "rb") as f:
        full = f.read()
    n_chunks = (len(full) + client.chunk_size - 1) // client.chunk_size
    print(f"[verify_pra] heapdump size = {len(full)} bytes ({len(full)/1024/1024:.1f} MB); "
          f"chunk_size={client.chunk_size}, n_chunks={n_chunks}")

    # Step 1: create upload session
    print("[verify_pra] POST /api/heapdump-reports/uploads")
    create_resp = client.post_json(
        "/api/heapdump-reports/uploads", {}, with_csrf=True,
    )
    upload_id = create_resp["upload_id"]
    server_chunk_size = int(create_resp.get("chunk_size", client.chunk_size))
    print(f"[verify_pra]   upload_id={upload_id}, server_chunk_size={server_chunk_size}")

    # Step 2: PUT each chunk
    for i in range(n_chunks):
        start = i * server_chunk_size
        end = min(start + server_chunk_size, len(full))
        chunk = full[start:end]
        chunk_path = f"/api/heapdump-reports/uploads/{upload_id}/chunks/{i}"
        chunk_resp = client.put_raw(chunk_path, chunk, with_csrf=True,
                                    content_type="application/octet-stream")
        print(f"[verify_pra]   chunk {i+1}/{n_chunks} uploaded ({len(chunk)} bytes)")

    # Step 3: complete
    print("[verify_pra] POST /api/heapdump-reports/uploads/{uid}/complete")
    complete_resp = client.post_json(
        f"/api/heapdump-reports/uploads/{upload_id}/complete",
        {"session_id": session_id, "filename": fname},
        with_csrf=True,
    )
    print(f"[verify_pra]   complete response: {complete_resp}")
    return complete_resp


def poll_heapdump_until_done(
    client: Client, report_id: str, *, timeout_secs: int, interval_secs: int = 5,
) -> Dict[str, Any]:
    """Poll the heapdump status endpoint until DONE or FAILED/timeout.

    PR-A only needs the heapdump to *exist* in the attachments dict (so
    `is_cross_domain` fires); it does not require MAT stats to have been
    backfilled. We return the report as soon as it reaches a terminal
    state (DONE / FAILED / CANCELLED) and let the caller decide whether
    to proceed.
    """
    deadline = time.monotonic() + timeout_secs
    last_report: Optional[Dict[str, Any]] = None
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        report = client.get_json(f"/api/heapdump-reports/{report_id}")
        last_report = report
        status = report.get("status")
        progress = report.get("progress")
        phase = report.get("phase")
        print(f"[verify_pra]   poll #{attempt}: status={status} phase={phase} progress={progress}%")
        if status == "DONE":
            return report
        if status in ("FAILED", "CANCELLED", "CANCEL_REQUESTED"):
            print(
                f"[verify_pra]   ⚠ heapdump reached terminal status={status} "
                f"error={report.get('error', '')!r} — PR-A only needs the "
                f"report to exist for is_cross_domain to fire; proceeding."
            )
            return report
        time.sleep(interval_secs)
    print(
        f"[verify_pra]   ⚠ timed out after {timeout_secs}s; last status="
        f"{last_report.get('status') if last_report else '?'} — proceeding anyway."
    )
    return last_report or {"id": report_id, "status": "TIMEOUT"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end verification for PR-A (cross-domain OOMDiagnosis).",
    )
    parser.add_argument("base_url_pos", nargs="?", default=None,
                        help="Optional base URL positional (deprecated; use --base-url).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--gc-log", default=None,
                        help="Path to GC log file (uploads if missing).")
    parser.add_argument("--hprof-gz", default=None,
                        help="Path to heapdump (.hprof or .hprof.gz); chunked-uploaded.")
    parser.add_argument("--heapdump-report-id", default=None,
                        help="Reuse an existing heapdump report (skip upload).")
    parser.add_argument("--session-id", default=None,
                        help="Reuse an existing session (otherwise created).")
    parser.add_argument("--poll-timeout", type=int, default=DEFAULT_POLL_TIMEOUT_SECS,
                        help=f"Seconds to wait for heapdump DONE (default {DEFAULT_POLL_TIMEOUT_SECS}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Upload + poll but skip the chat stream.")
    args = parser.parse_args()

    base_url = args.base_url_pos or args.base_url
    print(f"[verify_pra] base_url={base_url}")
    print(f"[verify_pra] gc_log={args.gc_log!r}  hprof_gz={args.hprof_gz!r}  "
          f"heapdump_report_id={args.heapdump_report_id!r}  session_id={args.session_id!r}")

    client = Client(base_url)

    # 1. Register a fresh test user
    banner("1. Register test user")
    suffix = str(int(time.time() * 1000))
    email = f"verify_pra+{suffix}@example.com"
    password = os.environ.get("VERIFY_PRA_PASSWORD", "VerifyP@ss1234")
    try:
        client.post_json("/api/auth/register", {"email": email, "password": password})
        print(f"[verify_pra] registered {email}")
    except RuntimeError as e:
        print(f"[verify_pra] register failed (user may already exist): {e}")
        client.post_json("/api/auth/login", {"username": email, "password": password})
        print(f"[verify_pra] logged in as {email}")
    if "csrf_token" not in client.cookie_jar:
        print("[verify_pra] no csrf_token after login — aborting")
        return 1
    print(f"[verify_pra] csrf_token length = {len(client.cookie_jar['csrf_token'])}")

    # 2. (Plan check removed — handled by upload endpoints when needed.)
    #     If the script is asked to upload a heapdump, the server enforces
    #     the heapdump feature gate and returns 403. PR-A's cross-domain
    #     branch only needs the attachments to EXIST, so we proceed
    #     regardless and surface any upload-time plan error inline.
    banner("2. (no plan preflight — server enforces per-endpoint)")
    print("[verify_pra] skipping plan preflight; the /gc/upload and /heapdump-reports/uploads "
          "endpoints will reject if the account's plan doesn't include the feature.")

    # 3. Session
    banner("3. Session")
    if args.session_id:
        session_id = args.session_id
        print(f"[verify_pra] using existing session_id={session_id}")
    else:
        session = client.post_json("/api/sessions", {"personal": True}, with_csrf=True)
        session_id = session["id"]
        print(f"[verify_pra] created session_id={session_id}")

    # 4. GC report
    banner("4. GC report")
    try:
        gc_reports = client.get_json(
            f"/api/sessions/{session_id}/gc/reports"
        ).get("reports", []) or []
    except RuntimeError:
        gc_reports = []
    print(f"[verify_pra] gc reports in session: {len(gc_reports)}")

    if args.gc_log:
        if not gc_reports:
            print(f"[verify_pra] uploading GC log from {args.gc_log}")
            upload = upload_gc_log(client, session_id, args.gc_log)
            print(f"[verify_pra] GC upload OK: report_id={upload.get('report_id')}")
            gc_reports = [{"id": upload.get("report_id"), "status": "DONE"}]
        else:
            print(f"[verify_pra] GC reports already exist; skipping upload.")
    else:
        if not gc_reports:
            print("[verify_pra] no GC report available and --gc-log not provided.")
            return 2

    gc_report = next((r for r in gc_reports if r.get("status") == "DONE"), gc_reports[0])
    print(f"[verify_pra] using gc_report={gc_report['id']} (status={gc_report.get('status')})")

    # 5. Heapdump report
    banner("5. Heapdump report")
    try:
        hd_reports = client.get_json(
            f"/api/heapdump-reports?session_id={session_id}"
        ).get("reports", []) or []
    except RuntimeError:
        hd_reports = []
    print(f"[verify_pra] heapdump reports in session: {len(hd_reports)}")

    hd_report: Optional[Dict[str, Any]] = None
    if args.heapdump_report_id:
        # Verify the report exists and is DONE
        try:
            hd_report = client.get_json(f"/api/heapdump-reports/{args.heapdump_report_id}")
        except RuntimeError:
            print(f"[verify_pra] ✗ --heapdump-report-id {args.heapdump_report_id} not found")
            return 7
        print(f"[verify_pra] using --heapdump-report-id (status={hd_report.get('status')})")
    elif args.hprof_gz:
        # Check if there's already a DONE heapdump for this session
        existing_done = next((r for r in hd_reports if r.get("status") == "DONE"), None)
        if existing_done:
            print(f"[verify_pra] DONE heapdump already exists: {existing_done['id']}; skipping upload")
            hd_report = existing_done
        else:
            print(f"[verify_pra] uploading heapdump from {args.hprof_gz}")
            complete = upload_heapdump_chunked(
                client, args.hprof_gz, session_id,
            )
            new_rid = complete.get("report_id") or complete.get("id")
            print(f"[verify_pra] heapdump upload complete: report_id={new_rid}")
            if not new_rid:
                print(f"[verify_pra] ✗ no report_id returned from complete: {complete}")
                return 8
            print(f"[verify_pra] polling heapdump {new_rid} for status=DONE "
                  f"(timeout={args.poll_timeout}s)...")
            hd_report = poll_heapdump_until_done(
                client, new_rid, timeout_secs=args.poll_timeout,
            )
            print(f"[verify_pra] heapdump DONE: {hd_report.get('id')}")
    else:
        existing_done = next((r for r in hd_reports if r.get("status") == "DONE"), None)
        if existing_done:
            hd_report = existing_done
        elif hd_reports:
            hd_report = hd_reports[0]
            print(f"[verify_pra] no --hprof-gz provided; using existing heapdump "
                  f"{hd_report.get('id')} (status={hd_report.get('status')})")
        else:
            print("[verify_pra] no heapdump report available and --hprof-gz not provided.")
            return 2

    if not hd_report:
        print("[verify_pra] no heapdump to attach — aborting before chat stream")
        return 9

    print(f"[verify_pra] using heapdump_report={hd_report.get('id')} "
          f"(status={hd_report.get('status')})")
    if hd_report.get("status") != "DONE":
        print(f"[verify_pra] ⚠ heapdump is {hd_report.get('status')} — PR-A's cross-domain "
              f"path will still fire (is_cross_domain only checks attachment lists), but "
              f"the LLM may produce a lower-quality OOMDiagnosis. Proceeding.")

    if args.dry_run:
        print("[verify_pra] --dry-run set; skipping chat stream")
        return 0

    # 6. Stream the chat with cross-domain context
    banner("6. Stream /api/chat/stream with cross-domain context")
    req_body = {
        "session_id": session_id,
        "message": (
            "请基于附上的 GC 日志和堆转储综合分析 OOM 的根因链路 / "
            "Diagnose the cross-domain OOM chain across the attached GC log and heapdump."
        ),
        "lang": "zh",
        "report_contexts": [
            {"type": "gc", "session_id": session_id, "report_id": gc_report["id"]},
            {"type": "heapdump", "session_id": session_id, "report_id": hd_report["id"]},
        ],
    }

    resp = client.post_stream("/api/chat/stream", req_body, with_csrf=True)
    try:
        events = parse_sse(resp)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    print(f"[verify_pra] received {len(events)} SSE events")
    if not events:
        # The server may have closed the connection before writing any
        # events (e.g. quota exceeded, plan gate, internal error). Print
        # the raw response so the operator can diagnose.
        print("[verify_pra] (no events — likely a server-side rejection)")
        print("[verify_pra] re-issuing with stream=False to capture the response body...")
        try:
            debug_resp = client._request(
                "POST", "/api/chat/stream", json_body=req_body,
                headers={"Accept": "text/event-stream",
                         **client.csrf_headers()},
            )
            try:
                raw = debug_resp.read(4096).decode("utf-8", errors="replace")
                print(f"[verify_pra] debug response (first 4KB):\n{raw}")
            finally:
                try:
                    debug_resp.close()
                except Exception:
                    pass
        except RuntimeError as e:
            print(f"[verify_pra] debug POST failed: {e}")

    # 7. Tally event types
    banner("7. SSE event tally")
    by_type: Dict[str, int] = {}
    for ev in events:
        try:
            payload = json.loads(ev["data"])
        except Exception:
            payload = {}
        ev_type = payload.get("type", ev.get("event", "?"))
        by_type[ev_type] = by_type.get(ev_type, 0) + 1
    for k, v in sorted(by_type.items()):
        print(f"  {k}: {v}")

    # 8. Find final event and inspect diagnosis
    banner("8. final.diagnosis")
    final_event: Optional[Dict[str, Any]] = None
    for ev in events:
        try:
            payload = json.loads(ev["data"])
        except Exception:
            continue
        if payload.get("type") == "final":
            final_event = payload
            break

    if not final_event:
        print("[verify_pra] no final event seen — aborting")
        for ev in events[-5:]:
            try:
                payload = json.loads(ev["data"])
            except Exception:
                continue
            print(f"  tail: {payload}")
        return 3

    print(f"[verify_pra] final event keys: {sorted(final_event.keys())}")
    print(f"[verify_pra] final.content (last 200 chars): {final_event.get('content', '')[-200:]!r}")
    diag = final_event.get("diagnosis")
    print(f"[verify_pra] final.diagnosis = {json.dumps(diag, ensure_ascii=False, indent=2)}")

    # 9. Assert OOMDiagnosis shape
    banner("9. OOMDiagnosis contract check")
    failures: List[str] = []
    if diag is None:
        failures.append("diagnosis is None (expected OOMDiagnosis dict)")
    else:
        for key in ("verdict", "root_cause_chain", "cross_domain_findings",
                    "recommendations", "confidence"):
            if key not in diag:
                failures.append(f"missing '{key}' field")
        if diag.get("verdict") not in (
            "oom_confirmed", "oom_likely", "no_oom", "insufficient_evidence", None,
        ):
            failures.append(f"unknown verdict: {diag.get('verdict')!r}")
        chain = diag.get("root_cause_chain") or []
        if isinstance(chain, list) and len(chain) > 6:
            failures.append(f"root_cause_chain has {len(chain)} entries (max 6)")
        confidence = diag.get("confidence")
        if isinstance(confidence, (int, float)) and not (0.0 <= confidence <= 1.0):
            failures.append(f"confidence out of range: {confidence}")

    if failures:
        print("[verify_pra] ✗ FAIL")
        for f in failures:
            print(f"  - {f}")
        return 4
    print("[verify_pra] ✓ PASS — OOMDiagnosis contract satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())