"""
YAML frontmatter schema and validation for event files.

An event file looks like:

    ---
    time: 2026-04-03T14:32:00Z
    intensity: 0.45
    access_count: 0
    embedding: embeddings/ev_20260403_001.npy
    tags: [Zephyr, prank]
    links: []
    ---

    Free-form text body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ------------------------------------------------------------------
# Dataclass representing a parsed event
# ------------------------------------------------------------------

@dataclass
class EventRecord:
    # Identity — filename IS the ID; no separate id field
    filename: str                      # e.g. "ev_20260403_001"

    # Temporal
    time: datetime

    # Text intensity (surface-level conversational heat)
    intensity: float = 0.0             # [0.0, 1.0]

    # State
    access_count: int = 0

    # Memory dynamics
    strength: float = 1.0              # memory strength [0.0, 3.0], decays/reinforces
    last_accessed: Optional[datetime] = None  # last time this event was recalled
    user_weight: float = 1.0            # user feedback weight [0.2, 2.0], scales retrieval score

    # Storage
    embedding: str = ""                # relative path to .npy, e.g. "embeddings/ev_....npy"

    # Graph links — each link is {"id": str, "type": str, "weight": float}
    #   type: "temporal" | "semantic" | "causal"
    #   weight: probability [0.0, 1.0]
    tags: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)

    # Embedding metadata
    embedding_dim: int = 0             # 0 = unknown/legacy (will be set on embed)

    # Body text (the non-frontmatter portion of the file)
    body: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def event_id(self) -> str:
        return self.filename

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """Return a dict suitable for writing as YAML frontmatter."""
        d: dict[str, Any] = {
            "time": self.time.strftime("%m-%d %H:%M"),
            "intensity": round(float(self.intensity), 4),
            "access_count": int(self.access_count),
            "strength": round(float(self.strength), 4),
            "embedding": self.embedding,
            "embedding_dim": self.embedding_dim,
            "tags": list(self.tags),
        }
        if self.last_accessed is not None:
            d["last_accessed"] = self.last_accessed.strftime("%m-%d %H:%M")
        if self.user_weight != 1.0:
            d["user_weight"] = round(float(self.user_weight), 4)
        return d

    @staticmethod
    def normalise_links(raw: list) -> list[dict]:
        """Migrate legacy bare-string links to {id, type, weight} dicts."""
        out: list[dict] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                out.append({"id": item, "type": "temporal", "weight": 0.5})
        return out


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

class ValidationError(ValueError):
    pass


_REQUIRED_KEYS = {"time"}


def validate_frontmatter(data: dict[str, Any], filename: str = "<unknown>") -> None:
    """Raise ValidationError if the frontmatter dict is malformed."""
    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise ValidationError(
            f"{filename}: missing required frontmatter keys: {sorted(missing)}"
        )

    if "intensity" in data:
        val = data["intensity"]
        if not isinstance(val, (int, float)):
            raise ValidationError(
                f"{filename}: 'intensity' must be a number, got {type(val).__name__}"
            )
        i = float(val)
        if not (0.0 <= i <= 1.0):
            raise ValidationError(f"{filename}: intensity {i} out of range [0.0, 1.0]")

    if "access_count" in data and not isinstance(data["access_count"], int):
        raise ValidationError(
            f"{filename}: 'access_count' must be an integer"
        )


# ------------------------------------------------------------------
# Parse from raw frontmatter dict
# ------------------------------------------------------------------

def _parse_time(raw: Any) -> datetime:
    """Parse time from frontmatter: datetime object, ISO string, or short 'MM-DD HH:MM' format."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw)
    # Short format: "04-08 01:55" (no year, no seconds)
    if len(s) <= 11 and " " in s and "T" not in s:
        return datetime.strptime(s, "%m-%d %H:%M").replace(year=2026, tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_event(
    frontmatter: dict[str, Any],
    body: str,
    filename: str,
) -> EventRecord:
    """Parse and validate a frontmatter dict + body into an EventRecord."""
    validate_frontmatter(frontmatter, filename)

    time = _parse_time(frontmatter["time"])

    embedding_path = frontmatter.get("embedding", "")
    if isinstance(embedding_path, Path):
        embedding_path = str(embedding_path)

    # Parse last_accessed (optional)
    raw_last = frontmatter.get("last_accessed")
    last_accessed: Optional[datetime] = None
    if raw_last is not None:
        last_accessed = _parse_time(raw_last)

    return EventRecord(
        filename=filename,
        time=time,
        intensity=float(frontmatter.get("intensity", 0.0)),
        access_count=int(frontmatter.get("access_count", 0)),
        strength=float(frontmatter.get("strength", 1.0)),
        last_accessed=last_accessed,
        user_weight=float(frontmatter.get("user_weight", 1.0)),
        embedding=embedding_path,
        tags=list(frontmatter.get("tags") or []),
        links=EventRecord.normalise_links(list(frontmatter.get("links") or [])),
        embedding_dim=int(frontmatter.get("embedding_dim", 0)),
        body=body.strip(),
    )
