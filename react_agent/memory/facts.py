"""Token-budgeted fact selection for the system prompt."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, List

from .token_budget import compute_tokens


CATEGORY_PRIORITY: Dict[str, float] = {
    "user_remembered": 1.0,
    "user_preference": 0.9,
    "report_index": 0.5,
    "system_context": 0.3,
}

_HALF_LIFE_HOURS = 24.0


def _safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "null"


def _score(fact: Any, now: datetime) -> float:
    last_str = getattr(fact, "last_accessed_at", None) or getattr(fact, "created_at", None)
    try:
        last = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S") if last_str else now
    except Exception:
        last = now
    hours = max(0.0, (now - last).total_seconds() / 3600.0)
    recency = 0.5 ** (hours / _HALF_LIFE_HOURS)
    cat = CATEGORY_PRIORITY.get(getattr(fact, "category", None) or "user_remembered", 0.5)
    access = math.log((getattr(fact, "access_count", 0) or 0) + 2)
    return recency * cat * access


def scored_facts(
    memory: Any,
    session_id: str,
    model: str,
    budget_tokens: int = 800,
) -> List[Dict[str, Any]]:
    """Return top-N facts for ``session_id`` capped at ``budget_tokens``.

    Each returned dict has ``id``, ``text``, ``category``. Selected rows get
    their ``last_accessed_at`` and ``access_count`` updated in a single batch
    UPDATE so repeated reads deprioritise stale content.
    """
    db = getattr(memory, "db", None) or getattr(memory, "get_db", lambda: None)()
    if db is None:
        return []

    from react_agent.models import FactModel

    now = datetime.utcnow()
    facts = (
        db.query(FactModel)
        .filter(FactModel.session_id == session_id)
        .all()
    )
    facts.sort(key=lambda f: _score(f, now), reverse=True)

    selected: List[Dict[str, Any]] = []
    used = 0
    for f in facts:
        text = f.content or ""
        cost = compute_tokens(text, model)
        if used + cost > budget_tokens:
            continue
        selected.append({
            "id": f.id,
            "text": text,
            "category": f.category or "user_remembered",
        })
        used += cost

    if selected:
        ids = [s["id"] for s in selected]
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        db.query(FactModel).filter(FactModel.id.in_(ids)).update(
            {FactModel.last_accessed_at: now_str,
             FactModel.access_count: FactModel.access_count + 1},
            synchronize_session=False,
        )
        db.commit()
    return selected