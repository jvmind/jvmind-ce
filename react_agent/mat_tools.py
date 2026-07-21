"""MAT heapdump 分析工具集（给 ReAct Agent 使用）。

所有工具都以 (memory, session_id, arg) 三参形式被 _execute_tool 调用（mat_ 前缀分支）。
统一约定：
- 每个工具的第一参数是 report_id；
- 报告必须 status=DONE，否则返回"解析中"提示；
- 输出瘦身：原子类工具默认 Top-20 + 总数 + "还有 N 条"提示；数值带人类可读单位 (B/KB/MB/GB)；
- 异步类工具（leak-suspects、diagnose-oom）同步轮询 Java /tasks/{id} 直到终态。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

import httpx


_BASE = os.getenv("MAT_QUERY_SERVICE_URL", "http://127.0.0.1:8090").rstrip("/")
_HTTP_TIMEOUT = float(os.getenv("MAT_TOOL_HTTP_TIMEOUT", "60"))
_ASYNC_POLL_INTERVAL = float(os.getenv("MAT_TOOL_POLL_INTERVAL", "2"))
_ASYNC_POLL_MAX_SECS = float(os.getenv("MAT_TOOL_POLL_MAX_SECS", "120"))
_TOP_DEFAULT = 20
_TOP_MAX = 50


def _client() -> httpx.Client:
    # module-level singleton with connection pooling; thread-safe enough for
    # ReAct serial execution; short connect timeout guards against :8090 down.
    if not hasattr(_client, "_c") or _client._c is None:
        _client._c = httpx.Client(
            base_url=_BASE,
            timeout=httpx.Timeout(_HTTP_TIMEOUT, connect=5.0),
            follow_redirects=False,
        )
    return _client._c


def _close_client() -> None:
    c = getattr(_client, "_c", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _client._c = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_arg(arg: str) -> Dict[str, str]:
    """把 Tool.arg_from_call() 产出的逗号串解析成 kwarg dict。

    支持两种入参：
    - 单字符串: "hd_xxx" → {"report_id": "hd_xxx"}
    - k=v,k2=v2 / 位置串 "hd_xxx,20,retained" → 按工具各自的 _kw 顺序绑定
    工具自己决定如何解释位置参数。这里只做最基本的拆分与去空白。
    """
    s = (arg or "").strip()
    if not s:
        return {}
    # Detect k=v form
    if "=" in s and not s.startswith("hd_") and not s.startswith("{"):
        out: Dict[str, str] = {}
        for part in _split_top_level(s):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        if out:
            return out
    return {"__positional__": s}


def _split_top_level(s: str) -> list:
    """Split on commas that are not inside JSON/braces."""
    parts = []
    depth = 0
    cur = []
    in_str = False
    str_ch = ""
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == str_ch:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            str_ch = ch
            cur.append(ch)
            continue
        if ch in ("{", "[", "("):
            depth += 1
        elif ch in ("}", "]", ")"):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _positional(arg: str) -> list:
    return [p.strip() for p in _split_top_level(arg) if p.strip()]


def _fmt_bytes(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < 1024 or unit == "TB":
            return f"{v:,.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{v:,.1f} PB"


def _load_report(memory, session_id: str, report_id: str) -> Dict[str, Any]:
    """从 memory 取报告，校验 DONE + dump_dir；失败时返回带 "error" 键的 dict。"""
    if not report_id:
        return {"error": "缺少 report_id / report_id is required"}
    r = None
    if hasattr(memory, "get_heapdump_report"):
        r = memory.get_heapdump_report(session_id, report_id)
    if not r and hasattr(memory, "get_heapdump_report_by_id"):
        r = memory.get_heapdump_report_by_id(report_id)
    if not r:
        return {"error": f"报告 {report_id} 不存在 / report not found"}
    status = r.get("status")
    if status != "DONE":
        pct = r.get("progress") or 0
        if isinstance(pct, (int, float)) and pct > 1:
            pct = int(pct)
        else:
            pct = int(pct * 100) if pct else 0
        if pct > 100: pct = int(pct / 100)
        return {"error": f"解析中(进度 {pct}%)，稍后再试 / Parsing ({pct}%), try later"}
    if not r.get("dump_dir"):
        return {"error": "报告数据缺失 / report data missing on server"}
    err = r.get("error")
    if err:
        return {"error": f"报告解析失败 / report failed: {err}"}
    return r


def _http(method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
          json_body: Any = None, timeout: float = _HTTP_TIMEOUT) -> Dict[str, Any]:
    """调 query-service。返回 {"ok": bool, "status": int, "data": Any, "error": str}。"""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = _client().request(
            method, path, params=params, json=json_body,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )
    except httpx.TimeoutException:
        return {"ok": False, "status": 0, "data": None, "error": "查询超时 / query timed out"}
    except Exception as e:
        return {"ok": False, "status": 0, "data": None, "error": f"查询服务不可用 / query service unavailable: {e}"}
    try:
        data = resp.json()
    except Exception:
        data = resp.text
    if resp.status_code not in (200, 202):
        msg = ""
        if isinstance(data, dict):
            msg = data.get("error") or ""
        return {"ok": False, "status": resp.status_code, "data": data,
                "error": f"查询失败({resp.status_code}) / query failed: {msg}"}
    return {"ok": True, "status": resp.status_code, "data": data, "error": ""}


def _poll_task(task_id: str, max_secs: float = _ASYNC_POLL_MAX_SECS) -> Dict[str, Any]:
    """同步轮询 /tasks/{id} 直到 DONE/FAILED/CANCELLED/超时。"""
    deadline = time.monotonic() + max_secs
    last = None
    while time.monotonic() < deadline:
        r = _http("GET", f"/tasks/{task_id}", timeout=10.0)
        if not r["ok"]:
            return {"ok": False, "error": r["error"]}
        data = r["data"]
        last = data
        status = (data or {}).get("status")
        if status == "DONE":
            if data.get("resultEvicted"):
                return {"ok": False, "error": "结果已被回收，请重新查询 / result evicted, re-run query"}
            return {"ok": True, "data": data.get("result")}
        if status in ("FAILED", "CANCELLED"):
            return {"ok": False, "error": data.get("error") or f"任务 {status} / task {status}"}
        time.sleep(_ASYNC_POLL_INTERVAL)
    return {"ok": False, "error": f"等待任务超时（{int(max_secs)}s） / timed out waiting for task"}


_MAX_POLL_ATTEMPTS = 50


def _submit_query(memory, session_id: str, arg: str) -> Dict[str, Any]:
    """Submit the leak-suspects query and return the initial task state.

    The returned dict carries ``status``, ``progress`` and ``result`` fields
    that ``_poll_done`` / ``_poll_progress_pct`` / ``_format_*_result`` read on
    each loop iteration. On a submission error the dict carries
    ``status="FAILED"`` and an ``error`` field so the caller can surface it.
    """
    rid = (arg or "").strip().split(",")[0].strip()
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return {"status": "FAILED", "progress": 0.0, "result": None, "error": rep["error"]}
    r = _http("POST", "/leak-suspects", params={"dumpDir": rep["dump_dir"]}, timeout=10.0)
    if not r["ok"]:
        return {"status": "FAILED", "progress": 0.0, "result": None, "error": r["error"]}
    tid = (r["data"] or {}).get("taskId")
    if not tid:
        return {"status": "FAILED", "progress": 0.0, "result": None, "error": "未返回任务 ID / no taskId returned"}
    return {"taskId": tid, "status": "RUNNING", "progress": 0.0, "result": None, "filename": rep.get("filename", "")}


def _poll_done(task: Dict[str, Any]) -> bool:
    """Fetch latest task state from query-service; return True when terminal.

    Mutates ``task`` in place so subsequent reads via ``_poll_progress_pct``
    see the freshest progress / status. HTTP failures keep the loop alive
    (return False) so transient network blips don't kill the call.
    """
    tid = (task or {}).get("taskId")
    if not tid:
        return True
    r = _http("GET", f"/tasks/{tid}", timeout=10.0)
    if not r["ok"]:
        return False
    data = r["data"] or {}
    task["status"] = data.get("status", task.get("status", "RUNNING"))
    task["progress"] = data.get("progress", task.get("progress", 0.0))
    if "result" in data:
        task["result"] = data["result"]
    return task["status"] not in ("RUNNING", "", None)


def _poll_progress_pct(task: Any) -> int:
    """Return the current task progress as an integer percent in [0, 100]."""
    if isinstance(task, dict):
        pct = task.get("progress", 0) or 0
    else:
        pct = getattr(task, "progress", 0) or 0
    if isinstance(pct, float) and pct <= 1.0:
        pct = int(pct * 100)
    return max(0, min(100, int(pct)))


def _trim_list(items: list, top: int, label: str = "items") -> tuple:
    """截取 Top-N；返回 (sliced, total, more_text)."""
    if not isinstance(items, list):
        return items, 0, ""
    total = len(items)
    if top <= 0 or total <= top:
        return items, total, ""
    return items[:top], total, f"（共 {total} 项，仅展示前 {top} 项 / {total} {label}, showing top {top}）"


def _bilingual_lines(zh_lines: list, en_lines: list) -> str:
    """Combine ZH + EN lines side by side for the LLM (it understands either)."""
    out = []
    for z, e in zip(zh_lines, en_lines):
        out.append(f"{z} / {e}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Atomic tools
# ---------------------------------------------------------------------------

def mat_overview(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_overview", "Running overview..."):
        args = _parse_arg(arg)
        rid = args.get("report_id") or args.get("__positional__", "").split(",")[0]
        rep = _load_report(memory, session_id, rid)
        if rep.get("error"):
            return rep["error"]
        r = _http("GET", "/overview", params={"dumpDir": rep["dump_dir"], "full": "true"})
        if not r["ok"]:
            return r["error"]
        d = r["data"] or {}

        def pick(*keys, default=""):
            for k in keys:
                if k in d and d[k] not in (None, "", 0):
                    return d[k]
            return default

        used = pick("usedHeapSize", "usedHeap", "heapUsed", "used")
        cap = pick("committedHeapSize", "committedHeap", "heapCommitted", "committed", "maxHeap", default=None)
        live = pick("liveHeapSize", "liveHeap", "live")
        obj_count = pick("numObjects", "objectCount", "objects")
        class_count = pick("numClasses", "classCount", "classes")
        classloader = pick("numClassLoaders", "classLoaderCount", "classloaders")
        jvm = d.get("jvmInfo") or d.get("systemProperties") or {}
        jv = ""
        if isinstance(jvm, list):
            for prop in jvm:
                if isinstance(prop, dict) and prop.get("name") == "java.version":
                    jv = prop.get("value", "")
                    break
        elif isinstance(jvm, dict):
            jv = jvm.get("javaVersion", "") or jvm.get("java.version", "")
        lines_zh = [
            f"堆总览 · {rep.get('filename','')}",
            f"- 已用堆: {_fmt_bytes(used)}" + (f" / 已提交: {_fmt_bytes(cap)}" if cap else ""),
        ]
        lines_en = [
            f"Heap overview · {rep.get('filename','')}",
            f"- Used: {_fmt_bytes(used)}" + (f" / Committed: {_fmt_bytes(cap)}" if cap else ""),
        ]
        if live:
            lines_zh.append(f"- 存活对象占用: {_fmt_bytes(live)}")
            lines_en.append(f"- Live set: {_fmt_bytes(live)}")
        if obj_count:
            lines_zh.append(f"- 对象数: {obj_count:,}；类数: {class_count or '-'}；类加载器: {classloader or '-'}")
            lines_en.append(f"- Objects: {obj_count:,}; classes: {class_count or '-'}; classloaders: {classloader or '-'}")
        if jv:
            lines_zh.append(f"- JDK: {jv}")
            lines_en.append(f"- JDK: {jv}")
        leak_count = d.get("leakSuspectCount")
        if leak_count is not None:
            lines_zh.append(f"- 疑似泄漏: {leak_count} 处；可用 mat_leak_suspects({rid}) 详情")
            lines_en.append(f"- Leak suspects: {leak_count}; run mat_leak_suspects({rid}) for details")
        return _bilingual_lines(lines_zh, lines_en)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    return s not in ("0", "false", "no", "n", "f")


def _looks_like_address(s: str) -> bool:
    if not s:
        return False
    t = s.strip()
    if t.startswith(("0x", "0X")):
        rest = t[2:]
        return bool(rest) and all(c in "0123456789abcdefABCDEF" for c in rest)
    return (
        any(c in "abcdefABCDEF" for c in t)
        and all(c in "0123456789abcdefABCDEF" for c in t)
    )


def mat_histogram(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_histogram", "Running histogram..."):
        pos = _positional(arg)
        if not pos:
            return "用法 / usage: mat_histogram(<report_id>[,top,sort,objectSet])"
        rid = pos[0]
        rep = _load_report(memory, session_id, rid)
        if rep.get("error"):
            return rep["error"]
        top = _int(pos[1] if len(pos) > 1 else _TOP_DEFAULT, _TOP_DEFAULT)
        top = max(1, min(top, _TOP_MAX))
        sort = (pos[2] if len(pos) > 2 else "retained").strip() or "retained"
        if sort not in ("shallow", "retained", "count"):
            sort = "retained"
        obj_set = pos[3] if len(pos) > 3 else None
        kwargs = _parse_arg(arg)
        if "report_id" in kwargs:
            top = _int(kwargs.get("top"), top)
            sort = kwargs.get("sort", sort)
            obj_set = kwargs.get("objectSet", obj_set)
            top = max(1, min(top, _TOP_MAX))

        params = {"dumpDir": rep["dump_dir"], "top": top, "offset": 0, "sort": sort, "approx": "true"}
        if obj_set:
            params["objectSet"] = obj_set
        r = _http("GET", "/histogram", params=params)
        if not r["ok"]:
            return r["error"]
        items = (r["data"] or {}).get("rows", r["data"] or [])
        sliced, total, more = _trim_list(items, top, "classes")
        zh = [f"Histogram (sort={sort}, top={top}) · {rep.get('filename','')}"]
        en = [f"Histogram (sort={sort}, top={top}) · {rep.get('filename','')}"]
        if more:
            zh.append(more.split(" / ")[0])
            en.append(more.split(" / ")[1])
        for i, it in enumerate(sliced, 1):
            cn = it.get("label") or it.get("className") or "?"
            sc = it.get("shallowBytes", it.get("shallowHeap", it.get("shallow", 0)))
            rc = it.get("retainedBytes", it.get("retainedHeap", it.get("retained", 0)))
            c = it.get("count", it.get("objectCount", it.get("numberOfObjects", 0)))
            oid = it.get("objectId", it.get("id", ""))
            zh.append(f"{i:>2}. {cn}  objects={c:,}  shallow={_fmt_bytes(sc)}  retained={_fmt_bytes(rc)}" + (f"  id={oid}" if oid else ""))
            en.append(f"{i:>2}. {cn}  objects={c:,}  shallow={_fmt_bytes(sc)}  retained={_fmt_bytes(rc)}" + (f"  id={oid}" if oid else ""))
        return "\n".join(zh) + "\n\n" + "\n".join(en) if False else "\n".join(
            f"{z} / {e}" for z, e in zip(zh, en)
        ) if len(zh) == len(en) else str(items)[:2000]


def mat_dominator(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    if not pos:
        return "用法 / usage: mat_dominator(<report_id>[,parent,top])"
    rid = pos[0]
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    parent = pos[1] if len(pos) > 1 else "ROOT"
    top = _int(pos[2] if len(pos) > 2 else _TOP_DEFAULT, _TOP_DEFAULT)
    top = max(1, min(top, _TOP_MAX))
    params = {"dumpDir": rep["dump_dir"], "parent": parent, "top": top, "offset": 0}
    r = _http("GET", "/dominator", params=params)
    if not r["ok"]:
        return r["error"]
    items = (r["data"] or {}).get("rows", r["data"] or [])
    sliced, total, more = _trim_list(items if isinstance(items, list) else [], top, "children")
    zh = [f"Dominator tree (parent={parent}, top={top}) · {rep.get('filename','')}"]
    en = [f"Dominator tree (parent={parent}, top={top}) · {rep.get('filename','')}"]
    if more:
        zh.append(more.split(" / ")[0]); en.append(more.split(" / ")[1])
    for i, it in enumerate(sliced, 1):
        if not isinstance(it, dict):
            continue
        cn = it.get("label") or it.get("className") or it.get("type") or "?"
        rc = it.get("retainedBytes", it.get("retainedHeap", it.get("retained", 0)))
        sc = it.get("shallowBytes", it.get("shallowHeap", it.get("shallow", 0)))
        pct = it.get("retainedPercent", it.get("percent", ""))
        oid = it.get("objectId", it.get("id", ""))
        pct_s = f" ({pct}%)" if pct not in ("", None) else ""
        zh.append(f"{i:>2}. {cn}  retained={_fmt_bytes(rc)}{pct_s}" + (f"  id={oid}" if oid else ""))
        en.append(f"{i:>2}. {cn}  retained={_fmt_bytes(rc)}{pct_s}" + (f"  id={oid}" if oid else ""))
    return "\n".join(f"{z} / {e}" for z, e in zip(zh, en))


def mat_threads(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    if not pos:
        return "用法 / usage: mat_threads(<report_id>[,thread,frame[,raw]])"
    rid = pos[0]
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    thread = pos[1] if len(pos) > 1 else None
    if thread and _looks_like_address(thread):
        return (
            f"thread 参数 '{thread}' 看起来是对象地址（不是线程 id）。\n"
            f"thread param '{thread}' looks like an object address, not a thread id.\n"
            f"线程 id 必须是 mat_threads({rid}) 列表里每行的 objectId 字段（十进制整数）。\n"
            f"Use the decimal objectId shown in mat_threads({rid}) output.\n"
            f"重新列出线程后再选：mat_threads({rid})\n"
            f"Re-list threads then pick: mat_threads({rid})"
        )
    params: Dict[str, Any] = {"dumpDir": rep["dump_dir"], "top": _TOP_DEFAULT}
    frame = _int(pos[2] if len(pos) > 2 else None, -1)
    raw_mode = len(pos) > 3 and pos[3].lower() in ("raw", "debug", "1", "true")
    if thread:
        params["thread"] = thread
    if frame >= 0:
        params["frame"] = frame
    path = "/threads"
    r = _http("GET", path, params=params, timeout=60.0 if thread else 30.0)
    if not r["ok"]:
        return r["error"]
    d = r["data"]
    if raw_mode:
        return f"Raw response (thread={thread}, frame={frame}):\n" + json.dumps(d, ensure_ascii=False, indent=2)[:4000]
    if not thread:
        items = d if isinstance(d, list) else (d.get("rows") or d.get("threads") or [])
        sliced, total, more = _trim_list(items, _TOP_DEFAULT, "threads")
        zh = [f"Threads (top={_TOP_DEFAULT}) · {rep.get('filename','')}"]
        en = [f"Threads (top={_TOP_DEFAULT}) · {rep.get('filename','')}"]
        if more:
            zh.append(more.split(" / ")[0]); en.append(more.split(" / ")[1])
        for i, it in enumerate(sliced, 1):
            if not isinstance(it, dict): continue
            name = it.get("name") or "?"
            st = it.get("state") or it.get("status") or "?"
            daem = "daemon" if it.get("daemon") else ""
            frames_n = it.get("frameCount") or it.get("stackDepth") or ""
            oid = it.get("objectId", "")
            addr = it.get("address") or ""
            oid_part = f"  objId={oid} ← thread id" if oid not in ("", None) else ""
            addr_part = f"  addr={addr} (not a thread id)" if addr else ""
            zh.append(f"{i:>2}. [{st}] {name} {daem} frames={frames_n}{oid_part}{addr_part}")
            en.append(f"{i:>2}. [{st}] {name} {daem} frames={frames_n}{oid_part}{addr_part}")
        return "\n".join(f"{z} / {e}" for z, e in zip(zh, en))
    # drill-down: frames or locals
    if frame >= 0:
        return f"Locals @ frame={frame} · {thread}\n" + json.dumps(d, ensure_ascii=False, indent=2)[:3000]
    # Debug: return raw response structure if no frames found
    frames = d if isinstance(d, list) else (d.get("frames") or d.get("stack") or d.get("stackTrace") or [])
    if not frames and isinstance(d, dict):
        return f"Thread '{thread}' response (no frames found, keys: {list(d.keys())}):\n" + json.dumps(d, ensure_ascii=False, indent=2)[:2000]
    lines = [f"Stack of '{thread}' ({len(frames)} frames):",
             f"线程 '{thread}' 堆栈（共 {len(frames)} 帧）："]
    for i, f in enumerate(frames[:50]):
        if isinstance(f, dict):
            # Prefer preformatted text (Java-side ThreadsView already builds a human-readable
            # "at pkg.Class.method(sig) (File:line)" string). Fall back to assembling from
            # className/methodName/file/line for older responses or third-party adapters.
            txt = f.get("text")
            if txt:
                lines.append(f"  #{i} {txt}")
                continue
            cn = f.get("className") or f.get("class") or f.get("declaringClass") or ""
            mn = f.get("methodName") or f.get("method") or f.get("name") or "?"
            src = f.get("fileName") or f.get("source") or f.get("file") or ""
            ln = f.get("lineNumber") or f.get("line") or ""
            loc = f"{src}:{ln}" if src else "?:?"
            prefix = "at " if (cn or mn) else ""
            lines.append(f"  #{i} {prefix}{cn}.{mn}({loc})")
        elif isinstance(f, str):
            lines.append(f"  #{i} {f}")
        else:
            lines.append(f"  #{i} {f}")
    if len(frames) > 50:
        lines.append(f"... ({len(frames)-50} more / 还有 {len(frames)-50} 帧)")
    return "\n".join(lines)


def mat_oql(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_oql", "Running OQL query..."):
        pos = _positional(arg)
        if len(pos) < 2:
            return "用法 / usage: mat_oql(<report_id>,<query>[,limit,view,sort])"
        rid, q = pos[0], pos[1]
        rep = _load_report(memory, session_id, rid)
        if rep.get("error"):
            return rep["error"]
        limit = _int(pos[2] if len(pos) > 2 else 50, 50)
        limit = max(1, min(limit, 200))
        view = (pos[3] if len(pos) > 3 else "list").strip() or "list"
        sort = (pos[4] if len(pos) > 4 else "shallow").strip() or "shallow"
        params = {"dumpDir": rep["dump_dir"]}
        if view:
            params["view"] = view
        if sort:
            params["sort"] = sort
        body = {"q": q, "limit": limit, "offset": 0}
        r = _http("POST", "/oql", params=params, json_body=body, timeout=120.0)
        if not r["ok"]:
            return r["error"]
        d = r["data"] or {}
        rows = d.get("rows") if isinstance(d, dict) else d
        rsid = d.get("resultSetId") if isinstance(d, dict) else None
        if isinstance(rows, list):
            sliced, total, more = _trim_list(rows, limit, "rows")
            out = [f"OQL result (limit={limit}) · {rep.get('filename','')}",
                   f"OQL 查询结果（limit={limit}）· {rep.get('filename','')}"]
            if rsid:
                out.append(f"resultSetId: {rsid}（可用于 objectSet 二次查询 / usable as objectSet in follow-up queries）")
            if more:
                out.append(more)
            for i, row in enumerate(sliced, 1):
                out.append(f"{i:>3}. {json.dumps(row, ensure_ascii=False)[:300]}")
            return "\n".join(out)
        return json.dumps(d, ensure_ascii=False, indent=2)[:4000]


def mat_object(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    if len(pos) < 2:
        return "用法 / usage: mat_object(<report_id>,<objectId>)"
    rid, oid = pos[0], pos[1]
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    r = _http("GET", "/object", params={"dumpDir": rep["dump_dir"], "id": oid})
    if not r["ok"]:
        return r["error"]
    d = r["data"] or {}
    out = [f"Object {oid} · {rep.get('filename','')}",
           f"对象 {oid} · {rep.get('filename','')}",
           json.dumps(d, ensure_ascii=False, indent=2)[:4000]]
    return "\n".join(out)


def mat_path2gc(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    if len(pos) < 2:
        return "用法 / usage: mat_path2gc(<report_id>,<objectId>[,excludeWeakSoft])"
    rid, oid = pos[0], pos[1]
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    excl = _bool(pos[2] if len(pos) > 2 else "true", True)
    r = _http("GET", "/path2gc", params={
        "dumpDir": rep["dump_dir"], "object": oid,
        "excludeWeakSoft": "true" if excl else "false",
    }, timeout=60.0)
    if not r["ok"]:
        return r["error"]
    d = r["data"]
    if isinstance(d, dict) and "paths" in d:
        paths = d["paths"] or []
    elif isinstance(d, list):
        paths = d
    else:
        paths = [d]
    sliced, total, more = _trim_list(paths, _TOP_DEFAULT, "paths")
    out = [f"Paths to GC roots for {oid} · {rep.get('filename','')}",
           f"对象 {oid} 到 GC Root 的路径 · {rep.get('filename','')}"]
    if more:
        out.append(more)
    for i, p in enumerate(sliced, 1):
        out.append(f"--- path {i} ---")
        out.append(json.dumps(p, ensure_ascii=False, indent=2)[:2000])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Composite (async) tools
# ---------------------------------------------------------------------------

def mat_leak_suspects(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_leak_suspects", "Running leak suspects...") as em:
        task = _submit_query(memory, session_id, arg)
        attempts = 0
        timed_out = False
        while not _poll_done(task):
            em.update(_poll_progress_pct(task), "OQL query running")
            time.sleep(_ASYNC_POLL_INTERVAL)
            attempts += 1
            if attempts > _MAX_POLL_ATTEMPTS:
                timed_out = True
                break
        if timed_out:
            error_msg = (
                f"polling timed out after {_MAX_POLL_ATTEMPTS} attempts "
                f"({int(_MAX_POLL_ATTEMPTS * _ASYNC_POLL_INTERVAL)}s) / "
                f"轮询超时（{_MAX_POLL_ATTEMPTS} 次）"
            )
            if isinstance(task, dict):
                task["status"] = "FAILED"
                task["error"] = error_msg
            else:
                try:
                    task.status = "FAILED"
                    task.error = error_msg
                except Exception:
                    pass
        return _format_leak_suspects_result(task)


def _format_leak_suspects_result(task: Dict[str, Any]) -> str:
    if task.get("status") == "FAILED":
        return task.get("error") or "task failed"
    data = task.get("result") or {}
    suspects = data.get("suspects") if isinstance(data, dict) else data
    if not isinstance(suspects, list):
        suspects = [suspects] if suspects else []
    sliced, total, more = _trim_list(suspects, _TOP_DEFAULT, "suspects")
    rep_name = task.get("filename", "")
    zh = [f"Leak Suspects · {rep_name}",
          f"疑似内存泄漏 ({total} 项)" if total else "未发现明显泄漏 / No obvious leak suspects"]
    en = [f"Leak Suspects · {rep_name}",
          f"({total} suspects)" if total else "No obvious leak suspects"]
    if more:
        zh.append(more.split(" / ")[0]); en.append(more.split(" / ")[1])
    for i, s in enumerate(sliced, 1):
        if not isinstance(s, dict):
            zh.append(f"{i}. {s}"); en.append(f"{i}. {s}"); continue
        desc = s.get("description") or s.get("problem") or ""
        size = s.get("retainedBytes") or s.get("retainedHeapSize") or s.get("retainedHeapSize") or s.get("retained") or s.get("bytes")
        cls = s.get("className") or s.get("suspectClass") or ""
        line = f"{i}. "
        if cls:
            line += f"{cls} "
        if size:
            line += f"retained={_fmt_bytes(size)} "
        if desc:
            line += f"- {str(desc)[:200]}"
        zh.append(line); en.append(line)
    return "\n".join(f"{z} / {e}" for z, e in zip(zh, en))


def mat_diagnose_oom(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_diagnose_oom", "Running OOM diagnosis..."):
        pos = _positional(arg)
        rid = pos[0] if pos else ""
        rep = _load_report(memory, session_id, rid)
        if rep.get("error"):
            return rep["error"]
        culprit = _int(pos[1] if len(pos) > 1 else 30, 30)
        only_risky = _bool(pos[2] if len(pos) > 2 else "true", True)
        params: Dict[str, Any] = {"dumpDir": rep["dump_dir"], "culpritPct": culprit}
        if not only_risky:
            params["onlyRisky"] = "false"
        r = _http("GET", "/diagnose-oom", params=params, timeout=10.0)
        if not r["ok"]:
            return r["error"]
        tid = (r["data"] or {}).get("taskId")
        if not tid:
            return "未返回任务 ID / no taskId returned"
        pr = _poll_task(tid, max_secs=180.0)
        if not pr["ok"]:
            return pr["error"]
        data = pr["data"] or {}
        out = [f"OOM 诊断结论 · {rep.get('filename','')}",
               f"OOM Diagnosis · {rep.get('filename','')}",
               json.dumps(data, ensure_ascii=False, indent=2)[:5000]]
        return "\n".join(out)


def mat_connection_pools(memory, session_id: str, arg: str) -> str:
    """组合 /connection-pools + /pool-leaks。"""
    rid = (arg or "").strip().split(",")[0].strip()
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    r1 = _http("GET", "/connection-pools", params={"dumpDir": rep["dump_dir"]})
    r2 = _http("GET", "/pool-leaks", params={"dumpDir": rep["dump_dir"], "thresholdMs": 300000}, timeout=60.0)
    zh = [f"连接池诊断 · {rep.get('filename','')}"]
    en = [f"Connection Pool Diagnosis · {rep.get('filename','')}"]
    if r1["ok"]:
        zh.append(f"[连接池统计 / pools] {json.dumps(r1['data'], ensure_ascii=False)[:2000]}")
        en.append(zh[-1])
    else:
        zh.append(f"[连接池查询失败] {r1['error']}"); en.append(f"[pools failed] {r1['error']}")
    if r2["ok"]:
        leaks = r2["data"] or []
        if isinstance(leaks, list):
            sliced, total, more = _trim_list(leaks, _TOP_DEFAULT, "leaks")
            zh.append(f"[连接泄漏嫌疑 / leaks] {total} 项" + (f"（仅展示前 {_TOP_DEFAULT}）" if more else ""))
            en.append(f"[leaks] {total} found" + (f" (showing top {_TOP_DEFAULT})" if more else ""))
            for i, it in enumerate(sliced, 1):
                zh.append(f"  {i}. {json.dumps(it, ensure_ascii=False)[:300]}")
                en.append(f"  {i}. {json.dumps(it, ensure_ascii=False)[:300]}")
        else:
            zh.append(f"[pool-leaks] {json.dumps(r2['data'], ensure_ascii=False)[:2000]}")
            en.append(zh[-1])
    else:
        zh.append(f"[泄漏检测失败] {r2['error']}"); en.append(f"[leaks failed] {r2['error']}")
    return "\n".join(f"{z} / {e}" for z, e in zip(zh, en))


def mat_top_consumers(memory, session_id: str, arg: str, state: dict = None) -> str:
    from .graph.progress import _ProgressEmitter
    tcid = (state or {}).get("__current_tool_call_id__", "")
    with _ProgressEmitter(state or {}, tcid, "mat_top_consumers", "Computing top consumers..."):
        rid = (arg or "").strip().split(",")[0].strip()
        rep = _load_report(memory, session_id, rid)
        if rep.get("error"):
            return rep["error"]
        r = _http("GET", "/top-consumers", params={"dumpDir": rep["dump_dir"]}, timeout=60.0)
        if not r["ok"]:
            return r["error"]
        d = r["data"] or {}
        out = [f"Top Memory Consumers · {rep.get('filename','')}",
               f"内存占用 Top · {rep.get('filename','')}",
               json.dumps(d, ensure_ascii=False, indent=2)[:4000]]
        return "\n".join(out)


def mat_sql_in_threads(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    rid = pos[0] if pos else ""
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    only_risky = _bool(pos[1] if len(pos) > 1 else "true", True)
    params: Dict[str, Any] = {"dumpDir": rep["dump_dir"]}
    if not only_risky:
        params["onlyRisky"] = "false"
    r = _http("GET", "/sql-in-threads", params=params)
    if not r["ok"]:
        return r["error"]
    d = r["data"] or []
    items = d if isinstance(d, list) else (d.get("matches") or d.get("threads") or [])
    sliced, total, more = _trim_list(items, _TOP_DEFAULT, "matches")
    zh = [f"线程中的 SQL · {rep.get('filename','')} ({total} 条)"]
    en = [f"SQL in threads · {rep.get('filename','')} ({total} matches)"]
    if more:
        zh.append(more.split(" / ")[0]); en.append(more.split(" / ")[1])
    for i, it in enumerate(sliced, 1):
        zh.append(f"{i}. {json.dumps(it, ensure_ascii=False)[:300]}")
        en.append(f"{i}. {json.dumps(it, ensure_ascii=False)[:300]}")
    return "\n".join(f"{z} / {e}" for z, e in zip(zh, en))


def mat_threadlocals(memory, session_id: str, arg: str) -> str:
    pos = _positional(arg)
    rid = pos[0] if pos else ""
    rep = _load_report(memory, session_id, rid)
    if rep.get("error"):
        return rep["error"]
    group_by = pos[1] if len(pos) > 1 else "valueClass"
    params = {"dumpDir": rep["dump_dir"], "groupBy": group_by, "top": _TOP_DEFAULT}
    r = _http("GET", "/threadlocals", params=params, timeout=60.0)
    if not r["ok"]:
        return r["error"]
    d = r["data"]
    items = d if isinstance(d, list) else (d.get("groups") or d.get("entries") or [])
    sliced, total, more = _trim_list(items, _TOP_DEFAULT, "entries")
    out = [f"ThreadLocals (groupBy={group_by}) · {rep.get('filename','')} ({total})",
           f"ThreadLocals（groupBy={group_by}）· {rep.get('filename','')}（{total}）"]
    if more:
        out.append(more)
    for i, it in enumerate(sliced, 1):
        out.append(f"{i}. {json.dumps(it, ensure_ascii=False)[:300]}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Dispatcher (called from _execute_tool)
# ---------------------------------------------------------------------------

_TOOLS: Dict[str, Callable] = {
    "mat_overview": mat_overview,
    "mat_histogram": mat_histogram,
    "mat_dominator": mat_dominator,
    "mat_threads": mat_threads,
    "mat_threadlocals": mat_threadlocals,
    "mat_oql": mat_oql,
    "mat_object": mat_object,
    "mat_path2gc": mat_path2gc,
    "mat_leak_suspects": mat_leak_suspects,
    "mat_diagnose_oom": mat_diagnose_oom,
    "mat_connection_pools": mat_connection_pools,
    "mat_top_consumers": mat_top_consumers,
    "mat_sql_in_threads": mat_sql_in_threads,
}


def dispatch_mat_tool(memory, session_id: str, name: str, arg: str, state: dict = None) -> str:
    fn = _TOOLS.get(name)
    if not fn:
        return f"[错误] 未知 MAT 工具 {name} / unknown MAT tool {name}"
    try:
        return fn(memory, session_id, arg, state=state)
    except TypeError:
        try:
            return fn(memory, session_id, arg)
        except Exception as e:
            return f"[Tool Error] {type(e).__name__}: {e}"
    except Exception as e:
        return f"[Tool Error] {type(e).__name__}: {e}"
