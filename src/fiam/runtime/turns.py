"""Shared helpers for converting AI turns into flow beats."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fiam.store.beat import Beat

if TYPE_CHECKING:
    from fiam.markers import OutboundMarker
    from fiam.store.beat import AiStatus, UserStatus


_ROUTED_BLOCK_RE = re.compile(
    r"\[→[A-Za-z0-9_-]+:[^\]\n]+\]\s*.*?(?=(?:\n\s*)?\[→[A-Za-z0-9_-]+:[^\]\n]+\]|\Z)",
    re.DOTALL,
)


def parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp string, falling back to now()."""
    if ts_str:
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def speaker_label(name: str, fallback: str) -> str:
    return (name or fallback).strip().lower() or fallback


def speaker_text(speaker: str, text: str) -> str:
    clean = text.strip()
    if clean.startswith(f"{speaker}:") or clean.startswith(f"{speaker}："):
        return clean
    return f"{speaker}：{clean}"


def split_routed_text(text: str) -> tuple[list["OutboundMarker"], str]:
    """Extract outbound markers and return (markers, remaining_dialogue)."""
    from fiam.markers import parse_outbound_markers

    markers = parse_outbound_markers(text)
    if not markers:
        return [], text
    remaining = _ROUTED_BLOCK_RE.sub("", text).strip()
    return markers, remaining


def user_beat(
    text: str,
    *,
    t: datetime,
    source: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    user_name: str,
    meta: dict | None = None,
) -> Beat:
    """Build a user dialogue beat for a runtime source."""
    return Beat(
        t=t,
        text=speaker_text(speaker_label(user_name, "zephyr"), text),
        source=source,
        user=user_status,
        ai=ai_status,
        meta=meta or {},
    )


def assistant_text_beats(
    text: str,
    *,
    t: datetime,
    source: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    ai_name: str,
    meta: dict | None = None,
) -> list[Beat]:
    """Build assistant dialogue and dispatch beats from a text response."""
    ai_label = speaker_label(ai_name, "ai")
    base_meta = dict(meta or {})
    beats: list[Beat] = []
    routed, remaining = split_routed_text(text)

    for marker in routed:
        beats.append(Beat(
            t=t,
            text=speaker_text(ai_label, f"对 {marker.recipient} 说：{marker.body}"),
            source="dispatch",
            user=user_status,
            ai=ai_status,
            meta={**base_meta, "target": marker.channel, "recipient": marker.recipient},
        ))

    if remaining.strip():
        beats.append(Beat(
            t=t,
            text=speaker_text(ai_label, remaining.strip()),
            source=source,
            user=user_status,
            ai=ai_status,
            meta=base_meta,
        ))

    return beats