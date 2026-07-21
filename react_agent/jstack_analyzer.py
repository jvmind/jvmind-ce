"""jstack 线程转储解析与统计

标准 jstack -l 输出格式示例：
  Full thread dump OpenJDK 64-Bit Server VM (25.402-b08 mixed mode):

  "http-nio-8080-exec-1" #29 daemon prio=5 os_prio=0 tid=0x00007f... nid=0x3a2b waiting on condition [0x00007f...]
     java.lang.Thread.State: WAITING (parking)
      at sun.misc.Unsafe.park(Native Method)
      - parking to wait for  <0x000000076c0b4f30> (a java.util.concurrent.locks.ReentrantLock$NonfairSync)
      at java.util.concurrent.locks.LockSupport.park(LockSupport.java:175)
      ...

  "pool-1-thread-1" #15 prio=5 tid=0x00007f... nid=0x3a1f runnable [0x00007f...]
     java.lang.Thread.State: RUNNABLE
      at java.net.SocketInputStream.socketRead0(Native Method)
      ...

  Found one Java-level deadlock:
  =============================
  "thread-1":
    waiting to lock <0x000000076c0b4f30> (a ReentrantLock$NonfairSync)
    which is held by "thread-2"
  ...
"""
from __future__ import annotations

import json as _json
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------- 正则 ----------
# 线程头： "name" #tid_num daemon? prio=N os_prio=N tid=0x... nid=0x... state_hint [addr]
_RE_THREAD_HEADER = re.compile(
    r'^"(?P<name>.+?)"\s+'
    r'(?:#(?P<tid_num>\d+))?\s*'
    r'(?:\[(?P<os_nid>\d+)\]\s*)?'          # JDK 26+: [native_id] brackets after #
    r'(?P<daemon>daemon)?\s*'
    r'(?:prio=(?P<prio>\d+)\s+)?'            # prio optional (JVM internal threads)
    r'(?:os_prio=[-\d]+\s+)?'
    r'(?:cpu=[\d.]+\w+\s+)?'                 # JDK 26+: cpu time
    r'(?:elapsed=[\d.]+\w+\s+)?'             # JDK 26+: elapsed time
    r'tid=(?P<tid>0x[0-9a-f]+)\s+'
    r'(?:nid=(?P<nid>0x[0-9a-f]+|\d+))?\s*' # nid decimal or hex, optional (carrier threads)
    r'(?P<state_hint>.*?)'
    r'(?:\s+\[[^\]]*\])?\s*$'
)

# 状态行： java.lang.Thread.State: WAITING (parking)
_RE_STATE = re.compile(
    r'java\.lang\.Thread\.State:\s*(?P<state>\S+)\s*(?:\((?P<detail>[^)]*)\))?'
)

# 栈帧： at com.foo.Bar.method(Bar.java:42)
# 兼容 JDK 21+ 虚拟线程/lambda 合成类名，如
#   at java.lang.VirtualThread$$Lambda/0x000000002b006270.run(...)
# 类名部分允许 / 和十六进制；方法名取最后一个 . 之后、( 之前的片段。
_RE_FRAME = re.compile(
    r'at\s+(?P<class>[\w.$/]+)\.(?P<method>[\w$<>]+)'
    r'\((?P<file>[^)]*)\)\s*$'
)

# 锁行： - locked <0x...> (desc)
_RE_LOCK = re.compile(
    r'^\s+-\s+(?P<type>locked|waiting to lock|parking to wait for)\s+'
    r'<(?P<addr>0x[0-9a-f]+)>\s*'
    r'(?:\((?P<desc>[^)]*)\))?'
)

# 死锁线程行："thread-1":
_RE_DEADLOCK_THREAD = re.compile(r'^"(?P<name>.+?)":\s*$')

# 死锁 held by 行： which is held by "thread-2"
_RE_HELD_BY = re.compile(r'which is held by "(?P<name>.+?)"')

# nid 提取
_RE_NID = re.compile(r'nid=(0x[0-9a-f]+|\d+)')

# tid 编号提取
_RE_TID_NUM = re.compile(r'#(\d+)')


# ---------- 数据模型 ----------
def _parse_thread_header(line: str) -> Optional[dict]:
    m = _RE_THREAD_HEADER.match(line.rstrip())
    if not m:
        # 尝试宽松匹配
        name_m = re.match(r'^"(?P<name>.+?)"\s*', line)
        if not name_m:
            return None
        nid_m = _RE_NID.search(line)
        tid_m = _RE_TID_NUM.search(line)
        # extract state hint from tail
        rest = line[name_m.end():].strip()
        state_hint = ""
        # try to find trailing state words like "runnable", "waiting on condition", etc.
        sh_m = re.search(r'(runnable|waiting on condition|in Object\.wait\(\)|blocked)\s*$', rest)
        if sh_m:
            state_hint = sh_m.group(1)
        return {
            "name": name_m.group("name"),
            "tid": tid_m.group(1) if tid_m else "?",
            "nid": nid_m.group(1) if nid_m else "?",
            "daemon": False,
            "priority": 5,
            "state_hint": state_hint,
        }
    nid = m.group("nid")
    if nid is None:
        nid = m.group("os_nid") or "?"
    return {
        "name": m.group("name"),
        "tid": m.group("tid"),
        "nid": nid,
        "daemon": bool(m.group("daemon")),
        "priority": int(m.group("prio")) if m.group("prio") else 5,
        "state_hint": (m.group("state_hint") or "").strip(),
    }


def _parse_state_line(line: str) -> str:
    m = _RE_STATE.search(line)
    if m:
        return m.group("state")
    return "UNKNOWN"


def _parse_frame(line: str) -> Optional[dict]:
    m = _RE_FRAME.match(line.strip())
    if not m:
        return None
    file_str = m.group("file") or ""
    return {
        "class": m.group("class"),
        "method": m.group("method"),
        "native": file_str == "Native Method",
        "line_str": file_str,
    }


def _parse_lock(line: str) -> Optional[dict]:
    m = _RE_LOCK.match(line)
    if not m:
        return None
    return {
        "type": m.group("type"),
        "addr": m.group("addr"),
        "desc": m.group("desc") or "",
    }


# ---------- 主解析 ----------
# 线程头 state_hint → Thread.State 映射（用于没有 java.lang.Thread.State: 行的线程）
_STATE_HINT_MAP: Dict[str, str] = {
    "runnable": "RUNNABLE",
    "waiting on condition": "WAITING",
    "in Object.wait()": "WAITING",
    "in Object.wait0": "WAITING",
    "blocked": "BLOCKED",
}


def _infer_state_from_hint(header: dict) -> str:
    """从线程头部的 state_hint 推断线程状态（无 java.lang.Thread.State: 行时使用）。"""
    hint = (header.get("state_hint") or "").strip().lower()
    if not hint:
        # 无提示信息（如 carrier 线程）→ RUNNABLE
        return "RUNNABLE"
    return _STATE_HINT_MAP.get(hint, "RUNNABLE")


def _parse_single_thread(lines: List[str]) -> Optional[dict]:
    """解析单个线程的文本块，返回线程信息字典。"""
    if not lines:
        return None
    header = _parse_thread_header(lines[0])
    if not header:
        return None

    frames: List[dict] = []
    lock_details: List[dict] = []
    state = "UNKNOWN"
    has_locked_ownable = False

    # JDK 21+ 虚拟线程 carrier 线程会有两段栈：
    #   "Carrying virtual thread #N"  → carrier 自身的续体调度栈
    #   "Mounted virtual thread #N"   → 挂载的虚拟线程的应用栈（真正有分析价值）
    # 若存在 Mounted 段，只取该段的帧；否则取全部帧。
    mounted_idx = None
    carrying_vthread = False
    for i, line in enumerate(lines[1:], start=1):
        s = line.strip()
        if s.startswith("Carrying virtual thread"):
            carrying_vthread = True
        if s.startswith("Mounted virtual thread"):
            mounted_idx = i
            break
    frame_lines = lines[mounted_idx + 1:] if mounted_idx is not None else lines[1:]
    # 是否挂载了虚拟线程（当前正在执行某个 vthread）
    is_carrier = mounted_idx is not None or carrying_vthread

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("java.lang.Thread.State:"):
            state = _parse_state_line(stripped)
        elif stripped.startswith("- ") and _RE_LOCK.match(line):
            ld = _parse_lock(line)
            if ld:
                lock_details.append(ld)
        elif "Locked ownable synchronizers" in stripped:
            has_locked_ownable = True

    # 帧只从选定的段提取（Mounted 段或整块）
    for line in frame_lines:
        stripped = line.strip()
        if stripped.startswith("at "):
            frame = _parse_frame(stripped)
            if frame:
                frames.append(frame)

    # JDK 26+ carrier threads / JVM internal threads 没有 java.lang.Thread.State: 行
    if state == "UNKNOWN":
        state = _infer_state_from_hint(header)

    in_native = bool(frames and frames[0].get("native"))
    lock_held = None
    lock_waiting = None
    for ld in lock_details:
        desc = ld["desc"] or ld["addr"]
        if ld["type"] == "locked":
            lock_held = (lock_held or "") + (", " if lock_held else "") + desc
        elif ld["type"] in ("waiting to lock", "parking to wait for"):
            lock_waiting = desc

    return {
        "name": header["name"],
        "tid": header["tid"],
        "nid": header["nid"],
        "state": state,
        "daemon": header["daemon"],
        "priority": header["priority"],
        "frames": frames,
        "stack_depth": len(frames),
        "in_native": in_native,
        "lock_held": lock_held,
        "lock_waiting": lock_waiting,
        "is_carrier": is_carrier,
        "mounted_vthread": mounted_idx is not None,
    }


def _parse_deadlocks(lines: List[str]) -> List[dict]:
    """解析死锁段。"""
    deadlocks: List[dict] = []
    current: Optional[dict] = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = _RE_DEADLOCK_THREAD.match(stripped)
        if m:
            current = {
                "thread": m.group("name"),
                "waiting_for_addr": None,
                "waiting_for_desc": "",
                "held_by": None,
            }
            deadlocks.append(current)
            continue
        if current and ("waiting to lock" in stripped or "waiting on" in stripped):
            addr_m = re.search(r'<([^>]+)>', stripped)
            desc_m = re.search(r'\(([^)]*)\)', stripped)
            if addr_m:
                current["waiting_for_addr"] = addr_m.group(1)
            if desc_m:
                current["waiting_for_desc"] = desc_m.group(1)
        if current:
            hm = _RE_HELD_BY.search(stripped)
            if hm:
                current["held_by"] = hm.group("name")
    return deadlocks


# ---------- JSON 格式支持（JDK 26+ jcmd Thread.dump_to_file -format=json） ----------
_RE_JSON_FRAME = re.compile(
    r'(?:(?P<jmodule>[\w.]+)/)?'        # optional module prefix, e.g. "java.base/"
    r'(?P<jclass>[\w.$]+)\.'             # fully-qualified class name
    r'(?P<jmethod>[\w$<>]+)'             # method name
    r'\((?P<jfile>[^)]*)\)\s*$'          # file:line or "Native Method"
)


def _parse_json_frame(frame_str: str) -> Optional[dict]:
    """Parse a JDK 21+ JSON format stack frame.

    Supports JDK 21–27+ frame format: ``module/Class.method(file:line)``.
    Examples:
      "java.base/java.lang.Object.wait0(Native Method)"
      "VirtualThreadBench.lambda$submitTasks$0(VirtualThreadBench.java:19)"
      "java.base/jdk.internal.misc.Unsafe.park(Native Method)"
    """
    m = _RE_JSON_FRAME.match(frame_str.strip())
    if not m:
        return None
    file_str = m.group("jfile") or ""
    return {
        "class": m.group("jclass"),
        "method": m.group("jmethod"),
        "native": file_str == "Native Method",
        "line_str": file_str,
    }


def _parse_jstack_json(text: str) -> dict:
    """Parse a JDK 21+ JSON format thread dump into the same data model as parse_jstack().

    Works for both JDK 21–26 (string IDs, no formatVersion) and JDK 27+
    (numeric IDs, formatVersion=2).  Numeric values are coerced to str/int
    as needed.
    """
    raw = _json.loads(text)
    td = raw.get("threadDump", {})
    jdk_version = td.get("runtimeVersion", "")
    threads: List[dict] = []

    def _extract(cont: dict, parent_name: str = "") -> None:
        container_name = cont.get("container", parent_name)
        for t in cont.get("threads", []):
            tid = str(t.get("tid", ""))
            name = t.get("name", "") or ""
            is_virtual = t.get("virtual", False)
            # JDK 21+ virtual threads often have empty name; synthesize a readable
            # identifier so pool grouping / thread list rendering stays useful.
            if not name.strip():
                if is_virtual:
                    # Group virtual threads by their container so the pool chart
                    # aggregates them; append tid to keep individual rows distinct.
                    short_container = (container_name or "vthread").split(".")[-1].split("@")[0] or "vthread"
                    name = f"VThread-{short_container}#{tid}"
                else:
                    name = f"Thread#{tid}"
            state = t.get("state", "UNKNOWN")

            frames = [_parse_json_frame(sf) for sf in t.get("stack", [])]
            frames = [f for f in frames if f is not None]

            lock_waiting = t.get("waitingOn") or ""
            if not lock_waiting:
                pb = t.get("parkBlocker")
                if pb and isinstance(pb, dict):
                    lock_waiting = pb.get("object") or ""

            lock_held_list: List[str] = []
            for mo in t.get("monitorsOwned", []):
                for l_addr in mo.get("locks", []):
                    lock_held_list.append(str(l_addr))
            lock_held = "; ".join(lock_held_list) if lock_held_list else None

            carrier_tid = str(t.get("carrier", "")) if t.get("carrier") else None

            threads.append({
                "name": name,
                "tid": tid,
                "nid": "",
                "state": state,
                "daemon": False,
                "priority": 5,
                "frames": frames,
                "stack_depth": len(frames),
                "in_native": bool(frames and frames[0].get("native")),
                "lock_held": lock_held,
                "lock_waiting": lock_waiting or None,
                "is_carrier": False,
                "mounted_vthread": False,
                "virtual": is_virtual,
                "carrier_tid": carrier_tid,
                "container_name": container_name,
            })

        for child in cont.get("threadContainers", []):
            _extract(child, container_name)

    for c in td.get("threadContainers", []):
        _extract(c)

    return {
        "threads": threads,
        "deadlocks": [],
        "total_threads": len(threads),
        "jdk_version": jdk_version,
    }


# JDK 内部版本号 → 语义版本映射
_JDK_VERSION_MAP: Dict[int, str] = {
    25: "8", 28: "8",   # JDK 8 系列
    52: "11",
    53: "12", 54: "13", 55: "14",
    56: "15", 57: "16", 58: "17",
    59: "18", 60: "17",
    61: "19", 62: "20", 63: "21",
    64: "21", 65: "21+",
}


def _parse_jdk_version(header_line: str) -> Optional[str]:
    """从 jstack header 中提取 JDK 版本号。"""
    m = re.search(r'\((\d+)\.(\d+)-', header_line)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r'\((\d+)\.(\d+)\+', header_line)
    if m:
        return f"{m.group(1)}.{m.group(2)}+"
    m = re.search(r'VM \((\d+)\.(\d+)', header_line)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r'\((\d+)-[a-z]', header_line)
    if m:
        internal = int(m.group(1))
        return _JDK_VERSION_MAP.get(internal, f"内部版本 {internal}")
    return None


def parse_jstack(text: str) -> dict:
    """解析 jstack 文本（JDK 8-25 文本格式 或 JDK 26+ JSON 格式），返回结构化结果。

    自动检测：若输入以 ``{`` 开头则走 JSON 路径，否则走文本 ReAct 路径。

    返回：
      {
        "threads": [...],
        "deadlocks": [...],
        "total_threads": int,
        "jdk_version": str | None,
      }
    """
    cleaned = text.strip()
    if cleaned.startswith("{"):
        return _parse_jstack_json(cleaned)
    lines = text.splitlines()
    state = "header"  # header | thread | deadlock | jni
    thread_blocks: List[List[str]] = []
    current_block: Optional[List[str]] = None
    deadlock_lines: List[str] = []
    jdk_version: Optional[str] = None

    for line in lines:
        stripped = line.rstrip()
        if state == "header":
            if jdk_version is None and ("Full thread dump" in stripped or "JVM" in stripped):
                jdk_version = _parse_jdk_version(stripped)
            if stripped.startswith('"') and ('#' in stripped or 'tid=' in stripped):
                state = "thread"
                current_block = [stripped]
        elif state == "thread":
            if stripped.startswith('"') and ('#' in stripped or 'tid=' in stripped):
                if current_block:
                    thread_blocks.append(current_block)
                current_block = [stripped]
            elif "Found one Java-level deadlock" in stripped:
                if current_block:
                    thread_blocks.append(current_block)
                    current_block = None
                state = "deadlock"
                deadlock_lines.append(stripped)
            elif "JNI global refs" in stripped:
                if current_block:
                    thread_blocks.append(current_block)
                    current_block = None
                state = "jni"
            else:
                if current_block is not None:
                    current_block.append(stripped)
        elif state == "deadlock":
            if "JNI global refs" in stripped:
                state = "jni"
            else:
                deadlock_lines.append(stripped)
        elif state == "jni":
            pass

    if current_block:
        thread_blocks.append(current_block)

    threads = [_parse_single_thread(b) for b in thread_blocks]
    threads = [t for t in threads if t is not None]

    deadlocks = _parse_deadlocks(deadlock_lines)

    return {
        "threads": threads,
        "deadlocks": deadlocks,
        "total_threads": len(threads),
        "jdk_version": jdk_version,
    }


# ---------- 规则诊断 ----------
THREAD_COUNT_WARN = 1000
THREAD_COUNT_CRIT = 2000
POOL_BLOAT_MIN_SIZE = 500
POOL_BLOAT_RATIO = 0.50
BLOCKED_WARN_PCT = 5
BLOCKED_CRIT_PCT = 15
CONN_POOL_WARN_COUNT = 2
CONN_POOL_CRIT_COUNT = 5
CONN_POOL_CRIT_RATIO = 0.10
IO_WAIT_MIN_COUNT = 3
RUNNABLE_HIGH_PCT = 50

# 栈顶命中即视为伪 RUNNABLE（实际在做 IO 等待，不计入 CPU 密集）
_IO_RUNNABLE_METHODS = frozenset({
    "socketRead0", "socketAccept", "accept0", "read0", "readBytes",
    "epollWait", "epollCtl", "poll0", "poll", "recvfrom", "recv0",
})

# 连接池借连接特征（class.method 子串匹配，扫描前若干帧）
_CONN_POOL_BORROW_SIGNATURES = (
    "HikariPool.getConnection",
    "ConcurrentBag.borrow",
    "DruidDataSource.getConnection",
    "DruidDataSource.getConnectionInternal",
    "GenericObjectPool.borrowObject",
    "ConnectionPool.borrowConnection",
    "BasicResourcePool.awaitAvailable",
)
_CONN_POOL_SCAN_FRAMES = 15


def _overall_from_findings(findings: List[dict]) -> str:
    if any(f["severity"] == "high" for f in findings):
        return "critical"
    if findings:
        return "warning"
    return "health"


def _diagnose_threads(threads, blocked_percent, deadlock_count, thread_pools, lock_hotspots) -> dict:
    findings: List[dict] = []
    recommendations_zh: List[str] = []
    recommendations_en: List[str] = []
    total = len(threads)

    if total == 0:
        return {"overall": "health", "findings": [], "recommendations_zh": [], "recommendations_en": []}

    # Rule 1a: 线程总数偏高
    if total > THREAD_COUNT_CRIT:
        findings.append({
            "rule": "thread_count_high", "severity": "high",
            "title_zh": "线程总数过高", "title_en": "Thread count too high",
            "detail_zh": f"线程总数达 {total}，超过 {THREAD_COUNT_CRIT}，可能存在线程泄漏",
            "detail_en": f"Total threads {total} exceeds {THREAD_COUNT_CRIT}, possible thread leak",
        })
    elif total > THREAD_COUNT_WARN:
        findings.append({
            "rule": "thread_count_high", "severity": "medium",
            "title_zh": "线程总数偏高", "title_en": "Thread count elevated",
            "detail_zh": f"线程总数达 {total}，超过 {THREAD_COUNT_WARN}，建议关注",
            "detail_en": f"Total threads {total} exceeds {THREAD_COUNT_WARN}, worth attention",
        })

    # Rule 1b: 单池膨胀
    for p in thread_pools:
        if p["total"] > POOL_BLOAT_MIN_SIZE and p["total"] / total > POOL_BLOAT_RATIO:
            findings.append({
                "rule": "thread_pool_bloat", "severity": "medium",
                "title_zh": "单个线程池异常膨胀", "title_en": "Single thread pool bloated",
                "detail_zh": f"线程池 '{p['pool']}' 有 {p['total']} 个线程，占总数 {p['total']/total*100:.0f}%，疑似泄漏源",
                "detail_en": f"Pool '{p['pool']}' has {p['total']} threads ({p['total']/total*100:.0f}% of total), likely leak source",
            })

    # Rule 2: 死锁
    if deadlock_count > 0:
        findings.append({
            "rule": "deadlock", "severity": "high",
            "title_zh": "检测到死锁", "title_en": "Deadlock detected",
            "detail_zh": f"检测到 {deadlock_count} 组 Java 级死锁，需立即处理",
            "detail_en": f"Detected {deadlock_count} Java-level deadlock(s), requires immediate action",
        })
        recommendations_zh.append("分析死锁锁链，调整加锁顺序或引入超时锁（tryLock），必要时重启应用")
        recommendations_en.append("Analyze the deadlock lock chain, unify lock ordering or use tryLock with timeout; restart if necessary")

    # Rule 3: 锁竞争
    if blocked_percent > BLOCKED_CRIT_PCT:
        sev = "high"
    elif blocked_percent > BLOCKED_WARN_PCT:
        sev = "medium"
    else:
        sev = None
    if sev:
        top_lock = lock_hotspots[0] if lock_hotspots else None
        extra_zh = ""
        extra_en = ""
        if top_lock:
            holder = top_lock.get("held_by") or "未知"
            extra_zh = f"，最热锁 {top_lock['desc'][:40]} 有 {top_lock['blocked_count']} 个线程等待（持有者 {holder}）"
            extra_en = f"; hottest lock {top_lock['desc'][:40]} has {top_lock['blocked_count']} waiters (held by {top_lock.get('held_by') or 'unknown'})"
        findings.append({
            "rule": "lock_contention", "severity": sev,
            "title_zh": "锁竞争激烈", "title_en": "Heavy lock contention",
            "detail_zh": f"BLOCKED 线程占比 {blocked_percent}%{extra_zh}",
            "detail_en": f"BLOCKED threads at {blocked_percent}%{extra_en}",
        })
        recommendations_zh.append("定位持有热点锁的线程，缩小同步块范围或改用并发容器 / 读写锁")
        recommendations_en.append("Locate the thread holding the hot lock; narrow synchronized scope or switch to concurrent collections / read-write locks")

    # Rule 4: 连接池耗尽 / 泄漏
    conn_pool_waiters = []
    for t in threads:
        if t["state"] not in ("WAITING", "TIMED_WAITING"):
            continue
        for fr in t["frames"][:_CONN_POOL_SCAN_FRAMES]:
            sig = f"{fr['class']}.{fr['method']}"
            if any(pat in sig for pat in _CONN_POOL_BORROW_SIGNATURES):
                conn_pool_waiters.append(t)
                break
    n_conn = len(conn_pool_waiters)
    if n_conn >= CONN_POOL_WARN_COUNT:
        crit = n_conn >= CONN_POOL_CRIT_COUNT or (n_conn / total) > CONN_POOL_CRIT_RATIO
        findings.append({
            "rule": "conn_pool_exhaustion", "severity": "high" if crit else "medium",
            "title_zh": "连接池耗尽或连接泄漏", "title_en": "Connection pool exhaustion or leak",
            "detail_zh": f"{n_conn} 个线程阻塞在获取数据库/连接池连接，疑似连接池耗尽或连接未归还",
            "detail_en": f"{n_conn} threads blocked acquiring pool connections, likely pool exhaustion or leaked connections",
        })
        recommendations_zh.append("检查连接池大小配置与连接是否正常归还（try-with-resources / finally close），排查慢 SQL 占用连接")
        recommendations_en.append("Check pool size config and that connections are returned (try-with-resources/finally close); investigate slow SQL holding connections")

    # Rule 5: IO 等待集中（排除已被连接池规则覆盖的线程）
    conn_ids = {id(t) for t in conn_pool_waiters}
    io_wait_count = 0
    for t in threads:
        if t["state"] not in ("WAITING", "TIMED_WAITING"):
            continue
        if id(t) in conn_ids:
            continue
        if t["frames"] and t["frames"][0]["method"] in _IO_RUNNABLE_METHODS:
            io_wait_count += 1
    if io_wait_count >= IO_WAIT_MIN_COUNT:
        findings.append({
            "rule": "io_wait_concentration", "severity": "medium",
            "title_zh": "大量线程阻塞在 IO 等待", "title_en": "Many threads blocked on IO wait",
            "detail_zh": f"{io_wait_count} 个等待线程栈顶为网络/IO 读取，可能是下游服务或数据库响应慢",
            "detail_en": f"{io_wait_count} waiting threads top-of-stack in network/IO read, possibly slow downstream service or database",
        })
        recommendations_zh.append("检查下游服务 / 数据库响应耗时与超时配置，考虑增加超时与熔断")
        recommendations_en.append("Check downstream service / database latency and timeout config; consider timeouts and circuit breakers")

    # Rule 6: CPU 密集 / 疑似死循环（剔除伪 RUNNABLE）
    real_runnable = [
        t for t in threads
        if t["state"] == "RUNNABLE" and not (t["frames"] and t["frames"][0]["method"] in _IO_RUNNABLE_METHODS)
    ]
    if total and (len(real_runnable) / total * 100) > RUNNABLE_HIGH_PCT:
        # 是否集中在少数热点方法
        method_counts: Dict[str, int] = {}
        for t in real_runnable:
            if t["frames"]:
                key = f"{t['frames'][0]['class']}.{t['frames'][0]['method']}"
                method_counts[key] = method_counts.get(key, 0) + 1
        top = sorted(method_counts.values(), reverse=True)
        concentrated = bool(top) and sum(top[:3]) / len(real_runnable) > 0.5
        if concentrated:
            hot = max(method_counts.items(), key=lambda x: x[1])[0]
            findings.append({
                "rule": "cpu_intensive", "severity": "medium",
                "title_zh": "CPU 密集或疑似死循环", "title_en": "CPU-intensive or possible busy loop",
                "detail_zh": f"真实 RUNNABLE 线程占比 {len(real_runnable)/total*100:.0f}%，且集中于 {hot}，疑似 CPU 密集或死循环",
                "detail_en": f"Real RUNNABLE threads at {len(real_runnable)/total*100:.0f}%, concentrated in {hot}, possible CPU-intensive work or busy loop",
            })
            recommendations_zh.append("对热点方法做 CPU profiling（async-profiler），确认是否存在死循环或算法热点")
            recommendations_en.append("Profile the hot method with async-profiler to confirm busy loops or algorithmic hotspots")

    return {
        "overall": _overall_from_findings(findings),
        "findings": findings,
        "recommendations_zh": recommendations_zh,
        "recommendations_en": recommendations_en,
    }


# ---------- 统计 ----------
def compute_stats(parsed: dict) -> dict:
    """基于 parse_jstack 的结果生成统计摘要。"""
    threads: List[dict] = parsed["threads"]
    deadlocks: List[dict] = parsed["deadlocks"]

    by_state: Dict[str, int] = {}
    daemon_count = 0
    total_depth = 0
    max_depth = 0
    max_depth_thread = ""
    depth_list: List[int] = []

    # 锁热点统计
    lock_wait_map: Dict[str, dict] = {}
    # 等待模式统计
    waiting_patterns: Dict[str, int] = {}
    # 包级别栈帧统计
    package_counts: Dict[str, int] = {}

    for t in threads:
        s = t["state"]
        by_state[s] = by_state.get(s, 0) + 1
        if t["daemon"]:
            daemon_count += 1
        d = t["stack_depth"]
        total_depth += d
        depth_list.append(d)
        if d > max_depth:
            max_depth = d
            max_depth_thread = t["name"]

        # 锁等待 -> 锁热点
        if t.get("lock_waiting"):
            key = t["lock_waiting"]
            if key not in lock_wait_map:
                lock_wait_map[key] = {"desc": key, "blocked_count": 0, "blocked_by_threads": []}
            lock_wait_map[key]["blocked_count"] += 1
            lock_wait_map[key]["blocked_by_threads"].append(t["name"])

        # 等待模式：WAITING 状态线程的首帧
        if s == "WAITING" or s == "TIMED_WAITING":
            if t["frames"]:
                first = t["frames"][0]
                pattern = f"{first['class']}.{first['method']}"
                waiting_patterns[pattern] = waiting_patterns.get(pattern, 0) + 1

        # 包统计
        seen_packages = set()
        for f in t["frames"][:3]:  # 只看前 3 帧
            parts = f["class"].rsplit(".", 2)
            if len(parts) >= 2:
                pkg = ".".join(parts[:-1])
            else:
                pkg = parts[0]
            if pkg not in seen_packages:
                seen_packages.add(pkg)
                package_counts[pkg] = package_counts.get(pkg, 0) + 1

    # RUNNABLE 热点方法统计
    runnable_frames: Dict[str, int] = {}
    for t in threads:
        if t["state"] != "RUNNABLE":
            continue
        if t.get("frames"):
            f0 = t["frames"][0]
            method = f"{f0['class']}.{f0['method']}"
            runnable_frames[method] = runnable_frames.get(method, 0) + 1

    # 线程池统计
    pool_stats: Dict[str, dict] = {}
    for t in threads:
        pool = _extract_pool_name(t["name"])
        if pool not in pool_stats:
            pool_stats[pool] = {"pool": pool, "total": 0, "RUNNABLE": 0, "BLOCKED": 0, "WAITING": 0, "TIMED_WAITING": 0}
        pool_stats[pool]["total"] += 1
        state = t["state"]
        if state in pool_stats[pool]:
            pool_stats[pool][state] += 1

    avg_depth = round(total_depth / len(threads), 2) if threads else 0

    # 锁持有者映射
    lock_held_map: Dict[str, str] = {}
    for t in threads:
        if t.get("lock_held"):
            lock_held_map[t["lock_held"]] = t["name"]

    # 锁热点排序
    lock_hotspots = sorted(lock_wait_map.values(), key=lambda x: -x["blocked_count"])[:10]
    for h in lock_hotspots:
        h["held_by"] = lock_held_map.get(h["desc"])

    # 等待模式排序
    waiting_patterns_sorted = sorted(
        waiting_patterns.items(), key=lambda x: -x[1]
    )[:10]
    waiting_patterns_list = [
        {"pattern": p, "count": c} for p, c in waiting_patterns_sorted
    ]

    # 栈深分布
    ranges = [(0, 5), (6, 10), (11, 20), (21, 30), (31, 50), (51, 999)]
    dist = []
    for lo, hi in ranges:
        c = sum(1 for d in depth_list if lo <= d <= hi)
        if c > 0:
            label = f"{lo}-{hi}" if hi != 999 else f"{lo}+"
            dist.append({"range": label, "count": c})

    # 线程摘要（前端表格 + top 最深）
    threads_summary = []
    for t in threads:
        top_frame = ""
        frames_compact = []
        if t["frames"]:
            f0 = t["frames"][0]
            top_frame = f"{f0['class']}.{f0['method']}"
            frames_compact = [f"{f['class']}.{f['method']}({f['line_str']})" for f in t["frames"]]
        threads_summary.append({
            "name": t["name"],
            "state": t["state"],
            "depth": t["stack_depth"],
            "daemon": t["daemon"],
            "top_frame": top_frame,
            "lock_held": t.get("lock_held"),
            "lock_waiting": t.get("lock_waiting"),
            "frames": frames_compact,
        })

    # Top 5 最深栈
    deepest = sorted(
        threads_summary, key=lambda x: -x["depth"]
    )[:5]

    # 火焰图数据
    flamegraph = build_flamegraph_data(threads)

    # BLOCKED 线程数
    blocked_count = by_state.get("BLOCKED", 0)

    # 虚拟线程（JDK21+ 文本）：正在挂载 vthread 的 carrier 线程数
    carrier_count = sum(1 for t in threads if t.get("is_carrier"))
    mounted_vthread_count = sum(1 for t in threads if t.get("mounted_vthread"))
    uses_virtual_threads = carrier_count > 0

    # JSON 格式（JDK 26+）：虚拟线程 / TERMINATED / 容器聚合
    virtual_thread_count = sum(1 for t in threads if t.get("virtual"))
    terminated_count = by_state.get("TERMINATED", 0)
    container_raw: Dict[str, int] = {}
    for t in threads:
        cn = t.get("container_name")
        if cn:
            container_raw[cn] = container_raw.get(cn, 0) + 1
    container_stats = sorted(
        [{"container": k, "total": v} for k, v in container_raw.items()],
        key=lambda x: -x["total"],
    )

    diagnosis = _diagnose_threads(
        threads, round(blocked_count / len(threads) * 100, 2) if threads else 0,
        len(deadlocks), sorted(pool_stats.values(), key=lambda x: -x["total"]), lock_hotspots,
    )

    return {
        "total_threads": len(threads),
        "daemon_count": daemon_count,
        "by_state": by_state,
        "blocked_percent": round(blocked_count / len(threads) * 100, 2) if threads else 0,
        "deadlock_count": len(deadlocks),
        "deadlocks": deadlocks,
        "avg_stack_depth": avg_depth,
        "max_stack_depth": max_depth,
        "max_stack_thread": max_depth_thread,
        "stack_depth_dist": dist,
        "threads": threads_summary,
        "deepest": deepest,
        "lock_hotspots": lock_hotspots,
        "waiting_patterns": waiting_patterns_list,
        "top_packages": sorted(package_counts.items(), key=lambda x: -x[1])[:10],
        "runnable_hot_methods": sorted(runnable_frames.items(), key=lambda x: -x[1])[:10],
        "flamegraph": flamegraph,
        "thread_pools": sorted(pool_stats.values(), key=lambda x: -x["total"]),
        "uses_virtual_threads": uses_virtual_threads,
        "carrier_count": carrier_count,
        "mounted_vthread_count": mounted_vthread_count,
        "virtual_thread_count": virtual_thread_count,
        "terminated_count": terminated_count,
        "container_stats": container_stats,
        "jdk_version": parsed.get("jdk_version"),
        "diagnosis": diagnosis,
    }


def build_flamegraph_data(threads: List[dict]) -> dict:
    """从 RUNNABLE 线程的完整栈帧构建火焰图树结构。

    返回树的根节点（虚拟 root），每个节点：
      { "name": "class.method", "value": count, "children": [...] }
    value 表示经过该路径的 RUNNABLE 线程数。
    """
    root: dict = {"name": "root", "value": 0, "children": {}}

    for t in threads:
        if t["state"] != "RUNNABLE":
            continue
        frames = t.get("frames")
        if not frames:
            continue
        root["value"] += 1
        node = root
        # 传统火焰图：入口方法在栈底（树的浅层），当前/栈顶方法在栈顶（树的深层）。
        # jstack 的 frames[0] 是栈顶（当前方法），最后一帧是入口，故反向遍历。
        for f in reversed(frames):
            key = f"{f['class']}.{f['method']}"
            child = node["children"].get(key)
            if child is None:
                child = {"name": key, "value": 0, "children": {}}
                node["children"][key] = child
            child["value"] += 1
            node = child

    def _convert(n: dict) -> dict:
        children = list(n["children"].values())
        children.sort(key=lambda x: -x["value"])
        n["children"] = [_convert(c) for c in children]
        return n

    result = _convert(root)
    return result


def _extract_pool_name(thread_name: str) -> str:
    """从线程名中提取线程池名称（去除尾部编号）。"""
    m = re.match(r'^(.+?)[-#]\d+$', thread_name)
    if m:
        return m.group(1)
    return thread_name


def _extract_thread_text(text: str, identifier: str) -> Optional[str]:
    """从原始 jstack 文本/JSON 中提取指定线程的完整栈帧文本。"""
    identifier = identifier.strip().strip('"\'')
    # JSON path
    if text.strip().startswith("{"):
        return _extract_thread_text_json(text, identifier)
    # Text path
    lines = text.splitlines()
    result: List[str] = []
    capturing = False
    found_any = False

    for line in lines:
        is_header = line.startswith('"') and ('#' in line or 'tid=' in line)
        if is_header:
            if capturing:
                break
            name = line.split('"')[1] if line.count('"') >= 2 else ""
            nid_m = _RE_NID.search(line)
            tid_m = _RE_TID_NUM.search(line)
            nid = nid_m.group(1) if nid_m else None
            tid = tid_m.group(1) if tid_m else None
            # Also try extracting decimal nid from [nid] after # (JDK 26 format)
            os_nid_m = re.search(r'#\d+\s+\[(\d+)\]', line)
            os_nid = os_nid_m.group(1) if os_nid_m else None
            if (identifier == name or identifier in name or
                    (nid and identifier == nid) or
                    (tid and identifier == tid) or
                    (os_nid and identifier == os_nid)):
                capturing = True
                result.append(line)
                found_any = True
        elif capturing:
            if line.strip().startswith("Found one"):
                break
            result.append(line)

    return "\n".join(result) if found_any else None


def _extract_thread_text_json(text: str, identifier: str) -> Optional[str]:
    """从 JSON 格式线程转储中提取指定线程，格式化为可读文本。"""
    raw = _json.loads(text)
    td = raw.get("threadDump", {})

    target: Optional[dict] = None
    found_tid: Optional[str] = None
    found_container: Optional[str] = None

    def _search(cont: dict, parent_name: str = "") -> bool:
        nonlocal target, found_tid, found_container
        cname = cont.get("container", parent_name)
        for t in cont.get("threads", []):
            name = t.get("name", "")
            tid = str(t.get("tid", ""))
            if identifier == name or identifier == tid or (name and identifier in name):
                target = t
                found_tid = tid
                found_container = cname
                return True
        for child in cont.get("threadContainers", []):
            if _search(child, cname):
                return True
        return False

    for c in td.get("threadContainers", []):
        if _search(c):
            break

    if not target:
        return None

    lines = [
        f'Found thread / 找到线程: "{target.get("name", "")}" (tid={found_tid})',
        f'  State / 状态: {target.get("state", "UNKNOWN")}',
        f'  Virtual / 虚拟线程: {"Yes" if target.get("virtual") else "No"}',
    ]
    carrier = target.get("carrier")
    if carrier:
        lines.append(f'  Carrier TID / 载体线程: {carrier}')
    pb = target.get("parkBlocker")
    if pb and isinstance(pb, dict):
        lines.append(f'  Park Blocker: {pb.get("object", "")}')
    waiting_on = target.get("waitingOn")
    if waiting_on:
        lines.append(f'  Waiting On: {waiting_on}')
    for mo in target.get("monitorsOwned", []):
        for l_addr in mo.get("locks", []):
            lines.append(f'  - locked <{l_addr}>')
    if found_container:
        lines.append(f"  Container: {found_container}")

    lines.append("")
    lines.append("Stack Trace / 栈帧:")
    for i, sf in enumerate(target.get("stack", [])):
        marker = " *" if i == 0 else "  "
        lines.append(f"  {marker} {sf}")

    return "\n".join(lines)


# ---------- LLM 摘要 ----------
def _friendly_jdk(raw: Optional[str]) -> str:
    if not raw:
        return "未知"
    major = raw.split(".")[0]
    mapped = _JDK_VERSION_MAP.get(int(major)) if major.isdigit() else None
    return f"{mapped}（{raw}）" if mapped else raw


def summary_for_llm(stats: dict, max_chars: int = 2500) -> str:
    """Compact jstack stats for LLM consumption."""
    lines = [
        f"JDK Version: {_friendly_jdk(stats.get('jdk_version'))}",
        f"Total Threads: {stats['total_threads']}",
        f"Daemon Threads: {stats['daemon_count']}",
    ]
    bs = stats.get("by_state", {})
    parts = [f"  {s}={n}" for s, n in sorted(bs.items(), key=lambda x: -x[1])]
    lines.append("Thread State Distribution: " + ", ".join(parts))
    lines.append(f"BLOCKED Ratio: {stats.get('blocked_percent', 0)}%")

    if stats["deadlock_count"] > 0:
        for dl in stats["deadlocks"]:
            lines.append(
                f"  ⚠ Deadlock: {dl['thread']} waiting on {dl.get('waiting_for_desc', '?')} "
                f"(held by {dl.get('held_by', '?')})"
            )
    else:
        lines.append("Deadlocks: None detected")

    vt = stats.get("virtual_thread_count", 0)
    if vt > 0:
        lines.append(f"Virtual Threads: {vt} (JSON format / JSON 格式)")
    tc = stats.get("terminated_count", 0)
    if tc > 0:
        lines.append(f"TERMINATED Threads: {tc}")
    cs = stats.get("container_stats", [])
    if cs:
        cont_parts = [f"{c['container'][:40]}: {c['total']}" for c in cs[:5]]
        lines.append("Thread Containers: " + ", ".join(cont_parts))

    lines.append(f"Avg Stack Depth: {stats['avg_stack_depth']}")
    lines.append(f"Max Stack Depth: {stats['max_stack_depth']} ({stats['max_stack_thread']})")

    if stats["lock_hotspots"]:
        lines.append("Lock Contention Hotspots:")
        for h in stats["lock_hotspots"][:5]:
            lines.append(
                f"  - {h['desc'][:60]}: {h['blocked_count']} threads waiting"
            )

    if stats["waiting_patterns"]:
        lines.append("Waiting Patterns (Top 5):")
        for wp in stats["waiting_patterns"][:5]:
            lines.append(f"  - {wp['pattern']}: {wp['count']} occurrences")

    if stats["deepest"]:
        lines.append("Top 5 Deepest Stack Threads:")
        for t in stats["deepest"][:5]:
            lines.append(f"  - {t['name']}: depth={t['depth']}, state={t['state']}")

    diag = stats.get("diagnosis")
    if diag and diag.get("findings"):
        lines.append(f"Thread Diagnosis (Overall: {diag.get('overall', 'health')}):")
        for f in diag["findings"]:
            lines.append(f"  [{f['severity'].upper()}] {f['title_en']}: {f['detail_en']}")
        if diag.get("recommendations_en"):
            lines.append("Recommendations:")
            for r in diag["recommendations_en"]:
                lines.append(f"  - {r}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text


def analyze_specific_thread_tool(memory, session_id: str, arg: str) -> str:
    """Tool for Agent: analyze a specific thread's full stack trace. Format: <fid/report_id>,<thread_name/nid>"""
    from .memory.uploads import get_uploaded_text
    arg = arg.strip()
    parts = [p.strip() for p in arg.split(",", 1)]
    if len(parts) < 2:
        return ("Please provide file_id (or report_id) and thread identifier, separated by comma.\n"
                "Example: analyze_specific_thread(fid_xxx,http-nio-8080-exec-3)\n"
                "Or: analyze_specific_thread(rid_xxx,http-nio-8080-exec-3)\n"
                "Use read_jstack_report(list) to see available reports and file_ids.\n"
                "请提供 file_id (或 report_id) 和线程标识符，用逗号分隔。\n"
                "示例: analyze_specific_thread(fid_xxx,http-nio-8080-exec-3)")
    fid, identifier = parts
    text = get_uploaded_text(memory, fid)
    if not text:
        # try report_id to find file_id (scoped to this session)
        report = getattr(memory, 'get_jstack_report', lambda s, r: None)(session_id, fid)
        if report:
            text = get_uploaded_text(memory, report.get("file_id", ""))
            if text:
                fid = report["file_id"]
        if not text:
            # fall back to latest report in this session
            reports = getattr(memory, 'list_jstack_reports', lambda s: [])(session_id)
            if reports:
                latest_fid = reports[0].get("file_id")
                if latest_fid:
                    text = get_uploaded_text(memory, latest_fid)
                    if text:
                        fid = latest_fid
            if not text:
                return (f"File '{fid}' not found in this session. The raw file may have expired; please re-upload for thread drill-down.\n"
                        f"当前会话未找到 '{fid}' 对应的 jstack 原始文件，可能已过期；如需线程钻取请重新上传。")

    if not text:
        return "jstack raw text not found, please re-upload.\n未找到 jstack 原始文本，请重新上传。"

    thread_text = _extract_thread_text(text, identifier)
    if not thread_text:
        return (f"Thread '{identifier}' not found. Use analyze_jstack(file_id) to see available threads.\n"
                f"未找到匹配线程 '{identifier}'。当前可用线程可通过 analyze_jstack(file_id) 查看列表。")

    lines = [
        f"Found thread '{identifier}'. Full stack trace / 找到线程 '{identifier}'，完整栈帧信息：",
        "",
        thread_text,
        "",
        "Please analyze / 请分析以下内容：",
        "1. Whether the thread's state is normal / 该线程当前状态是否正常",
        "2. What operation it's performing (based on stack frames) / 它在执行什么操作",
        "3. If lock waiting/holding is involved, analyze lock chain / 如果涉及锁等待/锁持有，分析锁链关系",
        "4. Whether there are anomalies or potential risks / 是否存在异常或潜在风险",
        "5. Optimization suggestions / 优化建议",
    ]
    return "\n".join(lines)


def read_jstack_report_tool(memory, session_id: str, arg: str) -> str:
    """Tool for Agent: read existing jstack analysis reports in the current session."""
    arg = arg.strip()
    if not arg or arg == "list":
        reports = memory.list_jstack_reports(session_id)
        if not reports:
            return ("No jstack reports in current session. Please upload a jstack file first.\n"
                    "当前会话没有 jstack 报告。请先上传 jstack 文件。")
        lines = ["JStack Reports in current session / 当前会话的 jstack 报告列表："]
        for r in reports:
            ai_tag = " [AI diagnosis / 有 AI 诊断]" if r.get("has_ai") else ""
            lines.append(
                f"  - [{r['id']}] (file_id: {r.get('file_id','?')}) {r['filename']} ({r['created_at']}) "
                f"- Threads={r.get('total_threads','?')}, "
                f"BLOCKED={r.get('blocked_count','?')}{ai_tag}"
            )
        lines.append(f"\nTotal {len(reports)} reports. Use read_jstack_report(<report_id>) for details.\n"
                     f"共 {len(reports)} 份报告。使用 read_jstack_report(<report_id>) 查看详情。")
        return "\n".join(lines)

    report = memory.get_jstack_report(session_id, arg)
    if not report:
        return (f"Report '{arg}' not found. Use read_jstack_report(list) to list available reports.\n"
                f"未找到 ID 为 '{arg}' 的 jstack 报告。使用 read_jstack_report(list) 查看可用报告列表。")

    stats = report.get("stats", {})
    lines = [
        f"=== JStack Report / jstack 报告 ===",
        f"File ID: {report.get('file_id', '?')}",
        f"Filename / 文件名: {report.get('filename', '?')}",
        f"Analyzed at / 分析时间: {report.get('created_at', '?')}",
        "",
        f"Total Threads / 线程总数: {stats.get('total_threads', '?')}",
    ]
    bs = stats.get("by_state", {})
    if bs:
        lines.append("State Distribution / 状态分布: " + ", ".join(f"{s}={n}" for s, n in sorted(bs.items(), key=lambda x: -x[1])))
    lines.append(f"BLOCKED Ratio / BLOCKED 占比: {stats.get('blocked_percent', 0)}%")
    lines.append(f"Deadlocks / 死锁数: {stats.get('deadlock_count', 0)}")
    lines.append(f"Avg Stack Depth / 平均栈深度: {stats.get('avg_stack_depth', '?')}")
    lines.append(f"Max Stack Depth / 最大栈深度: {stats.get('max_stack_depth', '?')} ({stats.get('max_stack_thread', '')})")

    if stats.get("lock_hotspots"):
        lines.append("\nLock Contention Hotspots / 锁竞争热点:")
        for h in stats["lock_hotspots"][:5]:
            lines.append(f"  - {h['desc'][:60]}: {h['blocked_count']} threads waiting")

    if stats.get("deadlocks"):
        lines.append("\nDeadlock Details / 死锁详情:")
        for dl in stats["deadlocks"]:
            lines.append(f"  - {dl['thread']}: waiting on {dl.get('waiting_for_desc','?')} (held by {dl.get('held_by','?')})")

    ai_conc = report.get("ai_conclusion")
    if ai_conc:
        lines.append(f"\nAI Diagnosis / AI 诊断结论:\n{ai_conc[:2000]}")
        if len(ai_conc) > 2000:
            lines.append("...(truncated)")

    return "\n".join(lines)
