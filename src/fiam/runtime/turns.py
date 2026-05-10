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

_CONTROL_MARKERS = {"wake", "todo", "sleep", "mute", "notify", "carry_over", "hold"}


# Resolve incoming HTTP channel aliases to canonical channel names.
_CHANNEL_ALIASES = {
    "chat": "favilla",
    "favilla": "favilla",
    "stroll": "stroll",
    "studio": "favilla",
    "app": "favilla",
    "webapp": "favilla",
    "browser": "browser",
    "email": "email",
}


def normalize_channel(channel: str) -> str:
    s = (channel or "").strip().lower()
    return _CHANNEL_ALIASES.get(s, s or "favilla")


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
    channel: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    user_name: str,
) -> Beat:
    """Build a user dialogue beat for a channel."""
    return Beat(
        t=t,
        text=text.strip(),
        actor="user",
        channel=normalize_channel(channel),
        user=user_status,
        ai=ai_status,
    )


def assistant_text_beats(
    text: str,
    *,
    t: datetime,
    channel: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    runtime: str | None = None,
) -> list[Beat]:
    """Build assistant dialogue and dispatch beats from a text response."""
    beats: list[Beat] = []
    from fiam.markers import strip_xml_markers

    routed, remaining = split_routed_text(strip_xml_markers(text, _CONTROL_MARKERS))
    canon = normalize_channel(channel)

    for marker in routed:
        beats.append(Beat(
            t=t,
            text=marker.body.strip(),
            actor="ai",
            channel=marker.channel,
            user=user_status,
            ai=ai_status,
            runtime=runtime,
        ))

    if remaining.strip():
        beats.append(Beat(
            t=t,
            text=remaining.strip(),
            actor="ai",
            channel=canon,
            user=user_status,
            ai=ai_status,
            runtime=runtime,
        ))

    return beats