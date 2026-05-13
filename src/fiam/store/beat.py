"""
Beat — the atomic entry of fiam's event stream.

A beat represents one unit of information entering fiam's awareness,
regardless of source (CC dialogue, tool action, email, etc.).

SQLite events are the source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Two orthogonal dimensions:
#   actor   — who produced this beat
#   channel — canonical event/conversation domain (chat, studio, browser, ...)
#   surface — concrete app/page/client that carried it (favilla, atrium, ...)
#   kind    — what kind of beat it is (message, action, think, ...)
# A beat with channel="browser" + kind="action" is a browser tool action;
# channel="chat" + surface="favilla" + kind="think" is a private thought during a Favilla turn.
Actor = Literal["user", "ai", "external", "system"]
Kind = Literal["message", "action", "tool_result", "think", "schedule", "dispatch", "state", "attachment", "trace"]
Channel = str
KNOWN_CHANNELS: set[str] = {
    "chat", "studio", "stroll", "browser", "email", "schedule", "limen", "ring", "cc", "system",
}
# Runtime tags the model family that produced an AI beat.
# None for user/external/system beats.
KNOWN_RUNTIMES: set[str] = {"cc", "claude", "gemini"}

# Conductor-level state types (NOT Beat fields — these describe the daemon's
# current state, e.g. "AI is asleep right now"). Beats no longer carry these.
UserStatus = Literal["cc", "away", "together"]
AiStatus = Literal["online", "sleep", "busy", "together", "block", "mute", "notify"]


@dataclass(frozen=True, slots=True)
class Beat:
    """One atomic entry in the narrative stream."""

    t: datetime              # timestamp (UTC)
    actor: Actor             # who produced this beat
    channel: Channel         # which surface it appeared on
    kind: Kind               # what kind of beat (message/action/think/...)
    content: str             # natural-language content
    runtime: str | None = None  # model family: cc / claude / gemini / ... (None for non-AI)
    meta: dict[str, Any] | None = None  # extra info (tool name, session/source tags, ...)
    surface: str = ""        # concrete source surface, e.g. favilla / atrium

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "t": self.t.isoformat(),
            "actor": self.actor,
            "channel": self.channel,
            "kind": self.kind,
            "content": self.content,
        }
        if self.runtime:
            data["runtime"] = self.runtime
        if self.meta:
            data["meta"] = self.meta
        if self.surface:
            data["surface"] = self.surface
        return data

    def to_json(self) -> str:
        """Single-line JSON for migrations and diagnostics."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Beat:
        t = d["t"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return Beat(
            t=t,
            actor=d.get("actor", "system"),
            channel=d.get("channel", ""),
            kind=d.get("kind", "message"),
            content=d["content"],
            runtime=d.get("runtime"),
            meta=d.get("meta"),
            surface=d.get("surface", ""),
        )


# ------------------------------------------------------------------
# Event store I/O
# ------------------------------------------------------------------


def append_beat(path: Path, beat: Beat) -> str | None:
    """Persist a single beat to SQLite."""
    from fiam.store.events import EventStore, db_path_for_flow, object_dir_for_flow

    return EventStore(
        db_path_for_flow(path),
        object_dir=object_dir_for_flow(path),
    ).append_beat(beat)


def append_beats(path: Path, beats: list[Beat]) -> list[str]:
    """Persist multiple beats to SQLite."""
    if not beats:
        return []
    from fiam.store.events import EventStore, db_path_for_flow, object_dir_for_flow

    return EventStore(
        db_path_for_flow(path),
        object_dir=object_dir_for_flow(path),
    ).append_beats(beats)


def read_beats(path: Path, *, after: datetime | None = None) -> list[Beat]:
    """Read beats from SQLite."""
    from fiam.store.events import EventStore, db_path_for_flow, object_dir_for_flow

    return EventStore(db_path_for_flow(path), object_dir=object_dir_for_flow(path)).read_beats(after=after)


def iter_beats(path: Path, byte_offset: int = 0) -> tuple[list[Beat], int]:
    """Incremental read from byte_offset. Returns (new_beats, new_offset).

    Safe against incomplete trailing lines (mid-write).
    """
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if byte_offset > size:
        byte_offset = 0
    if byte_offset >= size:
        return [], byte_offset

    with open(path, "rb") as f:
        f.seek(byte_offset)
        raw = f.read()

    safe_offset = byte_offset
    beats: list[Beat] = []
    pos = 0
    for raw_line in raw.split(b"\n"):
        line_end = pos + len(raw_line) + 1
        text = raw_line.decode("utf-8", errors="replace").strip()
        pos = line_end

        if not text:
            safe_offset = min(byte_offset + pos, size)
            continue
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            break  # incomplete line — stop here
        safe_offset = min(byte_offset + pos, size)
        beats.append(Beat.from_dict(d))

    return beats, safe_offset
