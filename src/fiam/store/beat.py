"""
Beat — the atomic entry of fiam's narrative stream (flow.jsonl).

A beat represents one unit of information entering fiam's awareness,
regardless of source (CC dialogue, tool action, email, etc.).

flow.jsonl is append-only, one JSON object per line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Two orthogonal dimensions:
#   actor   — who produced this beat
#   channel — which surface it appeared on (favilla, browser, ...)
#   kind    — what kind of beat it is (message, action, think, ...)
# A beat with channel="browser" + kind="action" is a browser tool action;
# channel="favilla" + kind="think" is a private thought during a Favilla turn.
Actor = Literal["user", "ai", "external", "system"]
Kind = Literal["message", "action", "tool_result", "think", "schedule"]
Channel = str
KNOWN_CHANNELS: set[str] = {
    "favilla", "browser", "stroll", "email", "studio", "cc", "system",
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
    meta: dict[str, Any] | None = None  # extra info (tool name, source=marker/native, ...)

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
        return data

    def to_json(self) -> str:
        """Single-line JSON suitable for appending to flow.jsonl."""
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
        )


# ------------------------------------------------------------------
# flow.jsonl I/O
# ------------------------------------------------------------------


def append_beat(path: Path, beat: Beat) -> None:
    """Append a single beat to flow.jsonl (atomic line-write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = beat.to_json() + "\n"
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))


def append_beats(path: Path, beats: list[Beat]) -> None:
    """Append multiple beats in one write."""
    if not beats:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "".join(b.to_json() + "\n" for b in beats)
    with open(path, "ab") as f:
        f.write(blob.encode("utf-8"))


def read_beats(path: Path, *, after: datetime | None = None) -> list[Beat]:
    """Read all beats from flow.jsonl, optionally filtering by time."""
    if not path.exists():
        return []
    beats: list[Beat] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            beat = Beat.from_dict(d)
            if after is not None and beat.t <= after:
                continue
            beats.append(beat)
    return beats


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
