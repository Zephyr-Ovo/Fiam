"""Marker parsing for AI-authored XML control commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class OutboundMarker:
    channel: str
    recipient: str
    body: str


@dataclass(frozen=True)
class WakeMarker:
    at: str  # normalized to ISO with timezone offset


@dataclass(frozen=True)
class SleepMarker:
    at: str  # normalized to ISO with timezone offset


@dataclass(frozen=True)
class TodoMarker:
    at: str  # normalized to ISO with timezone offset
    text: str


@dataclass(frozen=True)
class StateMarker:
    state: str
    until: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RouteMarker:
    family: str
    reason: str = ""


@dataclass(frozen=True)
class HoldMarker:
    reason: str = ""

_XML_MARKER_RE = re.compile(
    r"<\s*(?P<name>[A-Za-z_][\w:-]*)\b(?P<attrs>[^<>]*?)\s*"
    r"(?:/>|>(?P<body>.*?)</\s*(?P=name)\s*>)",
    re.DOTALL | re.IGNORECASE,
)

_ATTR_RE = re.compile(r"([A-Za-z_][\w:-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def _mask_markdown_code(text: str) -> str:
    """Replace Markdown code spans/blocks with spaces while preserving offsets."""
    def mask(match: re.Match[str]) -> str:
        return " " * (match.end() - match.start())

    masked = re.sub(r"```.*?```", mask, text or "", flags=re.DOTALL)
    masked = re.sub(r"`[^`\n]*`", mask, masked)
    return masked


def parse_outbound_markers(
    text: str,
    *,
    allowed_channels: Iterable[str] | None = None,
) -> list[OutboundMarker]:
    """Parse ``<send to="channel:address">body</send>`` route markers."""
    allowed = {item.lower() for item in allowed_channels} if allowed_channels else None
    markers: list[OutboundMarker] = []
    masked_text = _mask_markdown_code(text)
    for match in _XML_MARKER_RE.finditer(masked_text):
        if match.group("name").lower() != "send":
            continue
        attrs = _attrs(match.group("attrs") or "")
        target = attrs.get("to", "").strip()
        if ":" not in target:
            continue
        channel, recipient = target.split(":", 1)
        channel = channel.strip().lower()
        if allowed is not None and channel not in allowed:
            continue
        recipient = recipient.strip()
        body = text[match.start("body"):match.end("body")].strip()
        if channel and recipient and body:
            markers.append(OutboundMarker(channel=channel, recipient=recipient, body=body))
    return markers


def _attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw or ""):
        attrs[match.group(1).lower()] = match.group(2) if match.group(2) is not None else match.group(3) or ""
    return attrs


def _xml_markers(text: str):
    for match in _XML_MARKER_RE.finditer(text or ""):
        yield match.group("name").lower(), _attrs(match.group("attrs") or ""), match.group("body") or ""


def parse_wake_markers(text: str, *, default_tz=None) -> list["WakeMarker"]:
    """Parse ``<wake at="YYYY-MM-DD HH:MM"/>`` markers.

    Only the ``at`` attribute is read. Sleep must be set first for wake to
    have meaning at runtime; the parser does not enforce that.
    """
    markers: list[WakeMarker] = []
    for name, attrs, _body in _xml_markers(text):
        if name != "wake":
            continue
        iso = _normalize_short_time(attrs.get("at", ""), default_tz=default_tz)
        if iso:
            markers.append(WakeMarker(at=iso))
    return markers


def parse_sleep_markers(text: str, *, default_tz=None) -> list["SleepMarker"]:
    """Parse ``<sleep at="YYYY-MM-DD HH:MM"/>`` markers.

    Overwrite-style: a later marker supersedes an earlier one. Only the
    ``at`` attribute is read.
    """
    markers: list[SleepMarker] = []
    for name, attrs, _body in _xml_markers(text):
        if name != "sleep":
            continue
        iso = _normalize_short_time(attrs.get("at", ""), default_tz=default_tz)
        if iso:
            markers.append(SleepMarker(at=iso))
    return markers


def parse_todo_markers(text: str, *, default_tz=None) -> list["TodoMarker"]:
    """Parse ``<todo at="YYYY-MM-DD HH:MM">description</todo>`` markers."""
    markers: list[TodoMarker] = []
    for name, attrs, body in _xml_markers(text):
        if name != "todo":
            continue
        at_raw = attrs.get("at", "").strip()
        body_text = (body or "").strip()
        if not at_raw or not body_text:
            continue
        iso = _normalize_short_time(at_raw, default_tz=default_tz)
        if iso:
            markers.append(TodoMarker(at=iso, text=body_text))
    return markers


def _normalize_short_time(raw: str, *, default_tz=None) -> str:
    """Accept ``YYYY-MM-DD HH:MM`` or full ISO; return ISO with tz offset.

    ``default_tz`` is a ``tzinfo`` (typically ``config.tz``) used when the
    input has no timezone. Returns ``""`` on parse failure.
    """
    from datetime import datetime, timezone

    if not raw:
        return ""
    candidate = raw.strip().replace("T", " ").replace("Z", "+00:00")
    # Allow seconds, allow offset
    fmts = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
    ]
    parsed = None
    for fmt in fmts:
        try:
            parsed = datetime.strptime(candidate, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz or timezone.utc)
    return parsed.isoformat()


def parse_state_markers(text: str) -> list[StateMarker]:
    markers: list[StateMarker] = []
    for name, attrs, _body in _xml_markers(text):
        if name != "state":
            continue
        state = (attrs.get("value") or attrs.get("state") or attrs.get("name") or "").strip().lower()
        if state not in {"block", "mute", "notify", "sleep", "busy", "together"}:
            continue
        markers.append(StateMarker(
            state=state,
            until=attrs.get("until", "").strip(),
            reason=attrs.get("reason", "").strip(),
        ))
    return markers


def parse_route_markers(text: str) -> list[RouteMarker]:
    markers: list[RouteMarker] = []
    for name, attrs, _body in _xml_markers(text):
        if name != "route":
            continue
        family = attrs.get("family", "").strip().lower()
        if family:
            markers.append(RouteMarker(family=family, reason=attrs.get("reason", "").strip()))
    return markers


def parse_cot_markers(text: str) -> list[str]:
    """Extract bodies of ``<cot>...</cot>`` markers (marker-authored thinking).

    The AI opts in by emitting ``<cot>`` blocks; we never sniff free text.
    Bodies are returned in source order, stripped. Empty bodies are skipped.
    """
    out: list[str] = []
    for name, _attrs, body in _xml_markers(text):
        if name != "cot":
            continue
        cleaned = (body or "").strip()
        if cleaned:
            out.append(cleaned)
    return out


def parse_hold_markers(text: str) -> list[HoldMarker]:
    """Parse ``<hold/>`` and ``<hold>reason</hold>`` markers."""
    out: list[HoldMarker] = []
    for name, _attrs, body in _xml_markers(text):
        if name == "hold":
            out.append(HoldMarker(reason=(body or "").strip()))
    return out


def parse_hold_reason(text: str) -> str:
    markers = parse_hold_markers(text)
    if not markers:
        return ""
    return markers[-1].reason


def strip_xml_markers(text: str, names: set[str] | Iterable[str]) -> str:
    wanted = {name.lower() for name in names}

    def replace(match: re.Match[str]) -> str:
        name = match.group("name").lower()
        if name in wanted:
            return ""
        return match.group(0)

    return _XML_MARKER_RE.sub(replace, text or "").strip()
