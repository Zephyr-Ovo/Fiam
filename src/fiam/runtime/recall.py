"""Shared recall refresh helper for runtime backends."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from fiam.retriever.spread import retrieve

if TYPE_CHECKING:
    from fiam.config import FiamConfig
    from fiam.store.pool import Pool


def refresh_recall(
    config: "FiamConfig",
    pool: "Pool",
    query_vec: np.ndarray,
    *,
    top_k: int | None = None,
) -> int:
    """Refresh recall.md from a query vector and return fragment count."""
    results = retrieve(
        query_vec,
        pool,
        shield_after=datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ),
        top_k=top_k or config.recall_top_k,
    )
    if not results:
        return 0

    now = datetime.now(timezone.utc)
    lines = [f"<!-- recall | {now.strftime('%Y-%m-%dT%H:%M:%SZ')} -->", ""]
    count = 0

    for event_id, activation in results:
        ev = pool.get_event(event_id)
        if ev is None:
            continue
        body = pool.read_body(event_id)
        fragment = body.strip()[:200]
        if len(body.strip()) > 200:
            fragment += "..."

        age = now - ev.t
        if age.days > 30:
            hint = f"{age.days // 30}个月前"
        elif age.days > 0:
            hint = f"{age.days}天前"
        elif age.seconds > 3600:
            hint = f"{age.seconds // 3600}小时前"
        else:
            hint = "刚才"

        lines.append(f"- ({hint}) {fragment}")
        ev.access_count += 1
        count += 1

    if count == 0:
        return 0

    pool.save_events()
    config.background_path.parent.mkdir(parents=True, exist_ok=True)
    config.background_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (config.background_path.parent / ".recall_dirty").touch()
    return count