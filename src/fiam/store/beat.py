"""
Beat — the atomic entry of fiam's narrative stream (flow.jsonl).

A beat represents one unit of information entering fiam's awareness,
regardless of source (CC dialogue, tool action, TG message, etc.).

flow.jsonl is append-only, one JSON object per line.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

# Valid beat sources
BeatSource = Literal["cc", "action", "tg", "email", "favilla", "schedule"]
BEAT_SOURCES: set[str] = {"cc", "action", "tg", "email", "favilla", "schedule"}

# Status enums
UserStatus = Literal["tg", "cc", "away", "together"]
AiStatus = Literal["online", "sleep", "busy", "together", "block", "mute", "notify"]


@dataclass(frozen=True, slots=True)
class Beat:
    """One atomic entry in the narrative stream."""

    t: datetime           # timestamp (UTC)
    text: str             # natural-language content
    source: BeatSource    # origin channel
    user: UserStatus      # user status at the time of this beat
    ai: AiStatus          # AI status at the time of this beat

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": self.t.isoformat(),
            "text": self.text,
            "source": self.source,
            "user": self.user,
            "ai": self.ai,
        }

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
            text=d["text"],
            source=d["source"],
            user=d.get("user", "away"),
            ai=d.get("ai", "online"),
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
