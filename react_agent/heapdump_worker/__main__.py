"""``python -m react_agent.heapdump_worker`` 入口。

启动 worker + watchdog 两个后台任务，直到收到 SIGTERM/SIGINT 优雅退出。
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

# 加载 .env（独立进程启动，server.py 的 load_dotenv 不会自动执行）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from . import worker_loop, worker_id
from .watchdog import watchdog_loop


def _setup_logging() -> None:
    level = os.getenv("HEAPDUMP_WORKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _main() -> int:
    _setup_logging()
    log = logging.getLogger("react_agent.heapdump_worker")
    log.info("Starting heapdump worker (id=%s)", worker_id())

    stop_event = asyncio.Event()

    def _handle_signal(_sig, _frame):
        log.info("Received signal; requesting graceful shutdown")
        stop_event.set()

    # SIGINT (Ctrl-C) + SIGTERM (kubernetes/systemd stop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            # 某些平台/子线程环境注册失败，可忽略
            pass

    worker_task = asyncio.create_task(worker_loop(stop_event), name="heapdump-worker")
    watchdog_task = asyncio.create_task(watchdog_loop(stop_event), name="heapdump-watchdog")

    await stop_event.wait()
    # 给两个 task 一点时间收尾
    for t in (worker_task, watchdog_task):
        try:
            await asyncio.wait_for(t, timeout=30)
        except asyncio.TimeoutError:
            log.warning("Task %s did not stop within 30s; cancelling", t.get_name())
            t.cancel()
    log.info("Heapdump worker exited")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(_main())
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)


def main() -> None:
    """Console-script entry point for `jvmind-worker`."""
    try:
        rc = asyncio.run(_main())
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)
