"""Object catalog read model over object-linked facts and upload manifests."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_HEX_RE = re.compile(r"^[0-9a-f]{8,64}$")


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    object_hash: str
    token: str
    name: str = ""
    mime: str = ""
    size: int = 0
    t: str = ""
    channel: str = ""
    surface: str = ""
    kind: str = ""
    actor: str = ""
    event_id: str = ""
    turn_id: str = ""
    dispatch_id: str = ""
    direction: str = ""
    visibility: str = ""
    provenance: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "object_hash": self.object_hash,
            "token": self.token,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "t": self.t,
            "channel": self.channel,
            "surface": self.surface,
            "kind": self.kind,
            "actor": self.actor,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "dispatch_id": self.dispatch_id,
            "direction": self.direction,
            "visibility": self.visibility,
            "provenance": self.provenance,
            "summary": self.summary,
            "tags": list(self.tags),
            "source": self.source,
        }
        return {key: value for key, value in data.items() if value not in ("", 0, [])}


class ObjectCatalog:
    """Search and resolve ObjectStore refs from derived object metadata."""

    def __init__(self, *, event_db_path: Path, upload_manifest_path: Path | None = None) -> None:
        self.event_db_path = event_db_path
        self.upload_manifest_path = upload_manifest_path

    @classmethod
    def from_config(cls, config) -> "ObjectCatalog":
        return cls(
            event_db_path=config.event_db_path,
            upload_manifest_path=config.home_path / "uploads" / "manifest.jsonl",
        )

    def search(self, query: str = "", *, limit: int = 20) -> list[ObjectRecord]:
        records = self._records()
        needle = str(query or "").strip().casefold()
        if needle:
            records = [record for record in records if needle in self._haystack(record)]
        return records[: max(1, min(100, int(limit or 20)))]

    def recent(self, *, limit: int = 20) -> list[ObjectRecord]:
        return self.search("", limit=limit)

    def resolve_token(self, token: str) -> str:
        raw = str(token or "").strip().lower()
        if raw.startswith("obj:"):
            raw = raw[4:]
        raw = "".join(ch for ch in raw if ch in "0123456789abcdef")
        if len(raw) == 64:
            return raw if self._object_exists(raw) else ""
        if len(raw) < 8 or not _HEX_RE.match(raw):
            return ""
        matches = [record.object_hash for record in self._records() if record.object_hash.startswith(raw)]
        unique = sorted(set(matches))
        return unique[0] if len(unique) == 1 else ""

    def _records(self) -> list[ObjectRecord]:
        by_hash: dict[str, ObjectRecord] = {}
        for record in [*self._event_records(), *self._manifest_records()]:
            existing = by_hash.get(record.object_hash)
            if existing is None or _sort_key(record) > _sort_key(existing):
                by_hash[record.object_hash] = record
        records = list(by_hash.values())
        records.sort(key=_sort_key, reverse=True)
        return records

    def _object_exists(self, object_hash: str) -> bool:
        return any(record.object_hash == object_hash for record in self._records())

    def _event_records(self) -> list[ObjectRecord]:
        if not self.event_db_path.exists():
            return []
        conn = sqlite3.connect(self.event_db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                  SELECT id, object_hash, object_name, object_mime, object_size, t, channel,
                      surface, kind, actor, turn_id, dispatch_id, meta_json
                FROM events
                WHERE object_hash != ''
                ORDER BY t DESC
                LIMIT 500
                """
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        out: list[ObjectRecord] = []
        for row in rows:
            object_hash = _clean_hash(row["object_hash"])
            if not object_hash:
                continue
            meta = _parse_meta(row["meta_json"])
            out.append(ObjectRecord(
                object_hash=object_hash,
                token=_token_for(object_hash),
                name=str(row["object_name"] or ""),
                mime=str(row["object_mime"] or ""),
                size=int(row["object_size"] or 0),
                t=str(row["t"] or ""),
                channel=str(row["channel"] or ""),
                surface=str(row["surface"] or ""),
                kind=str(row["kind"] or ""),
                actor=str(row["actor"] or ""),
                event_id=str(row["id"] or ""),
                turn_id=str(row["turn_id"] or ""),
                dispatch_id=str(row["dispatch_id"] or ""),
                direction=str(meta.get("direction") or ""),
                visibility=str(meta.get("visibility") or meta.get("privacy") or ""),
                provenance=str(meta.get("source") or meta.get("provenance") or ""),
                summary=str(meta.get("object_summary") or meta.get("summary") or ""),
                tags=_parse_tags(meta.get("object_tags") or meta.get("tags")),
                source="events",
            ))
        return out

    def _manifest_records(self) -> list[ObjectRecord]:
        path = self.upload_manifest_path
        if path is None or not path.exists():
            return []
        out: list[ObjectRecord] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            object_hash = _clean_hash(row.get("object_hash") or row.get("sha256"))
            if not object_hash:
                continue
            out.append(ObjectRecord(
                object_hash=object_hash,
                token=_token_for(object_hash),
                name=str(row.get("name") or ""),
                mime=str(row.get("mime") or ""),
                size=int(row.get("size") or 0),
                t=str(row.get("uploaded_at") or ""),
                channel=str(row.get("channel") or ""),
                surface=str(row.get("surface") or ""),
                kind=str(row.get("kind") or "upload"),
                direction=str(row.get("direction") or "inbound"),
                visibility=str(row.get("visibility") or row.get("privacy") or ""),
                provenance=str(row.get("source") or row.get("provenance") or "upload_manifest"),
                summary=str(row.get("object_summary") or row.get("summary") or ""),
                tags=_parse_tags(row.get("object_tags") or row.get("tags")),
                source="manifest",
            ))
        return out

    @staticmethod
    def _haystack(record: ObjectRecord) -> str:
        return "\n".join(str(value) for value in record.to_dict().values()).casefold()


def _clean_hash(value: Any) -> str:
    text = "".join(ch for ch in str(value or "").lower() if ch in "0123456789abcdef")
    return text if len(text) == 64 else ""


def _parse_meta(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = re.split(r"[,\s]+", value.strip())
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    tags: list[str] = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags[:20])


def _token_for(object_hash: str) -> str:
    return f"obj:{object_hash[:12]}"


def _sort_key(record: ObjectRecord) -> tuple[str, str]:
    raw = record.t or ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.fromtimestamp(0, timezone.utc)
    return (parsed.isoformat(), record.object_hash)