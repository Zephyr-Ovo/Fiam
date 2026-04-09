"""
Event store CRUD.

Each event is one Markdown file in store/events/ with YAML frontmatter.
Semantic vectors are stored as separate .npy files in store/embeddings/.
The filename (without extension) IS the event ID.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import frontmatter  # python-frontmatter
import yaml

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord, parse_event, validate_frontmatter


# ------------------------------------------------------------------
# HomeStore
# ------------------------------------------------------------------

class HomeStore:
    """Read/write interface for the event store."""

    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        config.ensure_dirs()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_event(self, event_id: str) -> EventRecord:
        """Load a single event by ID (filename without .md)."""
        path = self._event_path(event_id)
        if not path.exists():
            raise FileNotFoundError(f"Event not found: {path}")
        return self._load(path)

    def all_events(self) -> list[EventRecord]:
        """Return all events sorted by time (oldest first)."""
        events = [
            self._load(p)
            for p in self.config.events_dir.glob("*.md")
            if p.is_file()
        ]
        events.sort(key=lambda e: e.time)
        return events

    def iter_events(self) -> Iterator[EventRecord]:
        """Yield events one at a time without loading all into memory."""
        for path in sorted(self.config.events_dir.glob("*.md")):
            if path.is_file():
                yield self._load(path)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write_event(self, event: EventRecord) -> Path:
        """Persist an EventRecord to disk.  Returns the written path."""
        path = self._event_path(event.filename)
        md_text = _serialise_event(event)
        path.write_text(md_text, encoding="utf-8")
        return path

    def new_event_id(self) -> str:
        """Generate the next sequential event ID for today."""
        prefix = self.config.event_id_prefix
        today = datetime.now(timezone.utc).strftime("%m%d")
        pattern = re.compile(rf"^{re.escape(prefix)}_{today}_(\d+)$")

        max_seq = 0
        for path in self.config.events_dir.glob(f"{prefix}_{today}_*.md"):
            m = pattern.match(path.stem)
            if m:
                max_seq = max(max_seq, int(m.group(1)))

        return f"{prefix}_{today}_{max_seq + 1:03d}"

    def increment_access_count(self, event_id: str) -> EventRecord:
        """Bump access_count on disk and return updated record."""
        event = self.read_event(event_id)
        event.access_count += 1
        self.write_event(event)
        return event

    def update_metadata(self, event: EventRecord) -> Path:
        """Re-write an event to disk preserving its body.

        Use this after modifying metadata fields (strength, access_count,
        last_accessed) without touching the body text.
        """
        return self.write_event(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _event_path(self, event_id: str) -> Path:
        return self.config.events_dir / f"{event_id}.md"

    def _load(self, path: Path) -> EventRecord:
        post = frontmatter.load(str(path))
        return parse_event(
            frontmatter=dict(post.metadata),
            body=post.content,
            filename=path.stem,
        )


# ------------------------------------------------------------------
# Serialisation
# ------------------------------------------------------------------

def _serialise_event(event: EventRecord) -> str:
    """Render an EventRecord as Obsidian-compatible Markdown with YAML frontmatter."""
    fm = event.to_frontmatter_dict()
    yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    body = event.body.strip()
    return f"---\n{yaml_str}---\n\n{body}\n"
