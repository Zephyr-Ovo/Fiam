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

_CONTROL_MARKERS = {"later", "sleep", "mute", "notify", "carry_over", "hold"}


# Map raw HTTP `source` field values to known channel names. Keeps the wire
# protocol stable while flow.jsonl scene stays in the small known set.
_SOURCE_TO_CHANNEL = {
    "chat": "favilla",
    "favilla": "favilla",
    "stroll": "stroll",
    "studio": "favilla",
    "app": "favilla",
    "webapp": "favilla",
    "browser": "browser",
}


def channel_for_source(source: str) -> str:
    s = (source or "").strip().lower()
    return _SOURCE_TO_CHANNEL.get(s, s or "favilla")


def scene_for_user(source: str) -> str:
    s = (source or "").strip()
    if "@" in s:
        return s
    return f"user@{channel_for_source(s)}"


def scene_for_ai(source: str) -> str:
    s = (source or "").strip()
    if "@" in s:
        return s
    return f"ai@{channel_for_source(s)}"


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
    scene: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    user_name: str,
) -> Beat:
    """Build a user dialogue beat for a runtime scene."""
    return Beat(
        t=t,
        text=text.strip(),
        scene=scene,
        user=user_status,
        ai=ai_status,
    )


def assistant_text_beats(
    text: str,
    *,
    t: datetime,
    scene: str,
    user_status: "UserStatus",
    ai_status: "AiStatus",
    runtime: str | None = None,
) -> list[Beat]:
    """Build assistant dialogue and dispatch beats from a text response."""
    beats: list[Beat] = []
    from fiam.markers import strip_xml_markers

    routed, remaining = split_routed_text(strip_xml_markers(text, _CONTROL_MARKERS))

    for marker in routed:
        beats.append(Beat(
            t=t,
            text=marker.body.strip(),
            scene=f"ai@{marker.channel}",
            user=user_status,
            ai=ai_status,
            runtime=runtime,
        ))

    if remaining.strip():
        beats.append(Beat(
            t=t,
            text=remaining.strip(),
            scene=scene,
            user=user_status,
            ai=ai_status,
            runtime=runtime,
        ))

    return beats