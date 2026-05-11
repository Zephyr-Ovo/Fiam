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
    shield_recent: bool = True,
    shield_after: datetime | None = None,
) -> int:
    """Refresh recall.md from a query vector and return fragment count.

    When ``shield_recent`` is True (default), suppress events created today so
    automatic recall does not surface in-flight context. Pass False for manual
    recall flows that explicitly want recent events included.

    ``shield_after`` overrides the default today-midnight cutoff: any event
    whose ``t >= shield_after`` is suppressed. Used by the chat /recall
    endpoint to exclude events from the *current* session window (events
    created since the last session boundary are still in the AI's live
    context, so re-surfacing them via recall would be redundant).
    """
    if shield_after is None:
        shield_after = (
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            if shield_recent
            else None
        )
    results = retrieve(
        query_vec,
        pool,
        shield_after=shield_after,
        top_k=top_k or config.recall_top_k,
    )
    if not results:
        return 0

    now = datetime.now(timezone.utc)
    fragments: list[dict[str, str]] = []
    bullet_lines: list[str] = []
    count = 0

    for event_id, activation in results:
        ev = pool.get_event(event_id)
        if ev is None:
            continue
        body = pool.read_body(event_id)
        fragment = body.strip()[:400]
        if len(body.strip()) > 400:
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

        fragments.append({"hint": hint, "text": fragment})
        bullet_lines.append(f"- ({hint}) {fragment}")
        ev.access_count += 1
        count += 1

    if count == 0:
        return 0

    # Try ds narration; on failure, fall back to raw bullet dump.
    narrated: str | None = None
    try:
        from fiam_lib.app_markers import narrate_recall_fragments
        narrated = narrate_recall_fragments(fragments, config)
    except Exception:
        narrated = None

    header = f"<!-- recall | {now.strftime('%Y-%m-%dT%H:%M:%SZ')} -->"
    body_md = narrated if narrated else "\n".join(bullet_lines)
    pool.save_events()
    config.background_path.parent.mkdir(parents=True, exist_ok=True)
    config.background_path.write_text(f"{header}\n\n{body_md}\n", encoding="utf-8")
    (config.background_path.parent / ".recall_dirty").touch()
    return count