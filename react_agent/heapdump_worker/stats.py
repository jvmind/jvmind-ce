"""解析 DONE 后回填 stats：调 query-service /overview?dumpDir=&full=true 写入 heapdump_reports.stats。

对齐 mat-study/07 §T5 与 EXECUTION_PLAN P2.4。失败时不改 DONE 状态（stats 留空，
可事后重试或前端展示"stats 缺失"提示）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

from ..db import SessionLocal
from ..models import HeapdumpReportModel

_logger = logging.getLogger(__name__)

_QUERY_SERVICE_URL = os.getenv("MAT_QUERY_SERVICE_URL", "http://127.0.0.1:8090")
_OVERVIEW_TIMEOUT = float(os.getenv("MAT_OVERVIEW_TIMEOUT", "120"))
_OVERVIEW_RETRIES = int(os.getenv("MAT_OVERVIEW_RETRIES", "2"))
_OVERVIEW_RETRY_DELAY = float(os.getenv("MAT_OVERVIEW_RETRY_DELAY", "3"))


async def backfill_stats(report_id: str, dump_dir: str) -> Optional[dict]:
    """调 query-service /overview 拉回 stats 并写入 DB。返回写入的 stats dict 或 None。"""
    url = _QUERY_SERVICE_URL.rstrip("/") + "/overview"
    stats: Optional[dict] = None
    last_err: Optional[Exception] = None
    for attempt in range(_OVERVIEW_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_OVERVIEW_TIMEOUT) as c:
                resp = await c.get(url, params={"dumpDir": dump_dir, "full": "true"})
                resp.raise_for_status()
                stats = resp.json()
            break
        except httpx.TimeoutException as e:
            last_err = e
            _logger.warning("[worker] backfill_stats timeout rid=%s attempt=%d/%d timeout=%ss err=%s",
                            report_id, attempt + 1, _OVERVIEW_RETRIES + 1, _OVERVIEW_TIMEOUT, e)
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = (e.response.text or "")[:500]
            except Exception:
                pass
            last_err = e
            _logger.warning("[worker] backfill_stats http error rid=%s attempt=%d status=%s body=%s",
                            report_id, attempt + 1, e.response.status_code, body)
            if 400 <= e.response.status_code < 500:
                break
        except Exception as e:
            last_err = e
            _logger.warning("[worker] backfill_stats failed rid=%s attempt=%d err=%r",
                            report_id, attempt + 1, e, exc_info=True)
        if attempt < _OVERVIEW_RETRIES:
            import asyncio
            await asyncio.sleep(_OVERVIEW_RETRY_DELAY)

    if not stats:
        return None

    db = SessionLocal()
    try:
        r = db.query(HeapdumpReportModel).filter(HeapdumpReportModel.id == report_id).first()
        if not r:
            _logger.warning("[worker] backfill_stats: report gone rid=%s", report_id)
            return None
        r.stats = json.dumps(stats, ensure_ascii=False)
        db.commit()
        return stats
    except Exception:
        db.rollback()
        _logger.exception("[worker] backfill_stats DB write failed rid=%s", report_id)
        return None
    finally:
        db.close()
