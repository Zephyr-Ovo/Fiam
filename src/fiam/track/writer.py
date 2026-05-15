from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


_FRONTMATTER_TEMPLATE = """---
source: track
agent: track
authors: [track]
visibility: both
editable: none
ts: {ts}
track_name: {name}
---
"""


def write_track(vault_dir: Path, name: str, body: str, *, now: datetime | None = None) -> Path:
    """Write `track/<name>.md` inside vault_dir. Overwrites; v0 is regenerate-all.

    Enforces frontmatter `editable: none` per STUDIO_CONVENTIONS.md §3 / §6.
    """
    safe = (name or "").strip().lower()
    if not safe or "/" in safe or ".." in safe or not safe.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid track name: {name!r}")
    vault_dir = Path(vault_dir)
    track_dir = vault_dir / "track"
    track_dir.mkdir(parents=True, exist_ok=True)
    target = track_dir / f"{safe}.md"
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    front = _FRONTMATTER_TEMPLATE.format(ts=ts, name=safe)
    target.write_text(front + "\n" + (body or "").rstrip() + "\n", encoding="utf-8")
    return target
