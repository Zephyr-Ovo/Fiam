"""Shared helpers for converting AI turns into flow beats."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fiam.channels import normalize_channel
from fiam.store.beat import Beat

if TYPE_CHECKING:
    from fiam.markers import OutboundMarker


_CONTROL_MARKERS = {"wake", "todo", "sleep", "state", "route", "hold", "cot"}


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
    from fiam.markers import parse_outbound_markers, strip_xml_markers

    markers = parse_outbound_markers(text)
    if not markers:
        return [], text
    remaining = strip_xml_markers(text, {"send"}).strip()
    return markers, remaining


def user_beat(
    text: str,
    *,
    t: datetime,
    channel: str,
    user_name: str,
) -> Beat:
    """Build a user dialogue beat for a channel."""
    return Beat(
        t=t,
        actor="user",
        channel=normalize_channel(channel),
        kind="message",
        content=text.strip(),
    )


def assistant_text_beats(
    text: str,
    *,
    t: datetime,
    channel: str,
    runtime: str | None = None,
) -> list[Beat]:
    """Build assistant dialogue and dispatch beats from a text response."""
    beats: list[Beat] = []
    from fiam.markers import parse_cot_markers, strip_xml_markers

    canon = normalize_channel(channel)

    # Marker-authored thinking: AI opts in via <cot>...</cot>. No sniffing.
    for cot_text in parse_cot_markers(text):
        beats.append(Beat(
            t=t,
            actor="ai",
            channel=canon,
            kind="think",
            content=cot_text,
            runtime=runtime,
            meta={"source": "fiam", "name": "fiam"},
        ))

    routed, remaining = split_routed_text(strip_xml_markers(text, _CONTROL_MARKERS))

    for marker in routed:
        beats.append(Beat(
            t=t,
            actor="ai",
            channel=marker.channel,
            kind="message",
            content=marker.body.strip(),
            runtime=runtime,
        ))

    if remaining.strip():
        beats.append(Beat(
            t=t,
            actor="ai",
            channel=canon,
            kind="message",
            content=remaining.strip(),
            runtime=runtime,
        ))

    return beats
