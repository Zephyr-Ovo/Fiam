"""Recall.md writing — memory fragments surfaced by retrieval."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _write_recall(config: "FiamConfig", events: list, ai_name: str | None = None) -> "Path":
    """Write recall.md — memory fragments surfaced by semantic retrieval.

    Each event body is stored as [user]\\n...\\n[assistant]\\n...
    We distill it into a clean one-line summary: user's words + topic hint.
    The result reads like background knowledge, not a conversation log.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [f"<!-- recall | {timestamp} -->", ""]

    for ev in events:
        age = now - ev.time
        if age.days > 30:
            time_hint = f"{age.days // 30}个月前"
        elif age.days > 0:
            time_hint = f"{age.days}天前"
        elif age.seconds > 3600:
            time_hint = f"{age.seconds // 3600}小时前"
        else:
            time_hint = "刚才"

        # Extract user-side text from event body (strip role markers)
        fragment = _extract_memory_fragment(ev.body)
        if len(fragment) > 200:
            fragment = fragment[:197] + "..."

        lines.append(f"- ({time_hint}) {fragment}")

    content = "\n".join(lines) + "\n"
    path = config.background_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _extract_memory_fragment(body: str) -> str:
    """Distill an event body into a clean memory fragment.

    Event bodies are stored as:
        [user]
        啊数据全丢了...
        [assistant]
        别急...

    We extract the user's words as the primary memory content.
    If user text is very short, include assistant's response for context.
    """
    parts = re.split(r'\[(?:user|assistant)\]\s*', body)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return body.strip()[:200]

    # parts[0] = user text, parts[1] = assistant text (if exists)
    user_text = parts[0]

    # If user text is very short and we have assistant context, add it
    if len(user_text) < 30 and len(parts) > 1:
        asst_text = parts[1]
        if len(asst_text) < 100:
            return f"{user_text} → {asst_text}"

    return user_text
