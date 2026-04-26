"""Marker parsing for AI-authored route commands.

Outbound markers have the shape ``[→target:recipient] body``. Targets are
matched against plugin dispatch targets at runtime, so adding a new dispatch
plugin no longer requires editing daemon regexes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class OutboundMarker:
    channel: str
    recipient: str
    body: str


_OUTBOUND_RE = re.compile(
    r"\[→(?P<channel>[A-Za-z0-9_-]+):(?P<recipient>[^\]\n]+)\]\s*"
    r"(?P<body>.*?)"
    r"(?=(?:\n\s*)?\[→[A-Za-z0-9_-]+:[^\]\n]+\]|\Z)",
    re.DOTALL,
)


def parse_outbound_markers(
    text: str,
    *,
    allowed_channels: Iterable[str] | None = None,
) -> list[OutboundMarker]:
    """Parse outbound route markers from a CC response."""
    allowed = {item.lower() for item in allowed_channels} if allowed_channels else None
    markers: list[OutboundMarker] = []
    for match in _OUTBOUND_RE.finditer(text):
        channel = match.group("channel").strip().lower()
        if allowed is not None and channel not in allowed:
            continue
        recipient = match.group("recipient").strip()
        body = match.group("body").strip()
        if channel and recipient and body:
            markers.append(OutboundMarker(channel=channel, recipient=recipient, body=body))
    return markers