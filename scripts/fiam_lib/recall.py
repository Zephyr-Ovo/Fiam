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
    """Distill an event body into a clean memory fragment with speaker labels.

    Event bodies are stored as:
        [user]
        啊数据全丢了...
        [assistant]
        别急...

    We extract key dialogue with speaker labels preserved.
    """
    # Split into (role, text) pairs
    segments: list[tuple[str, str]] = []
    current_role = ""
    current_text: list[str] = []

    for line in body.split("\n"):
        stripped = line.strip()
        if stripped in ("[user]", "[assistant]"):
            if current_role and current_text:
                segments.append((current_role, " ".join(current_text).strip()))
            current_role = stripped[1:-1]  # "user" or "assistant"
            current_text = []
        elif stripped:
            current_text.append(stripped)
    if current_role and current_text:
        segments.append((current_role, " ".join(current_text).strip()))

    if not segments:
        return body.strip()[:200]

    # Build fragment: keep user turn + short assistant response
    parts: list[str] = []
    budget = 200

    for role, text in segments:
        prefix = "user " if role == "user" else "AI "
        if len(text) > budget:
            text = text[:budget - 3] + "..."
        parts.append(f"{prefix}{text}")
        budget -= len(text) + 4
        if budget <= 0:
            break

    return " → ".join(parts) if len(parts) <= 2 else parts[0]
