"""SQLite event store for beat-level source of truth."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fiam.store.beat import Beat
from fiam.store.objects import ObjectStore


LARGE_CONTENT_BYTES = 8192


def db_path_for_flow(flow_path: Path) -> Path:
    return flow_path.parent / "events.sqlite3"


def object_dir_for_flow(flow_path: Path) -> Path:
    return flow_path.parent / "objects"


def event_id_for_beat(beat: Beat) -> str:
    meta = beat.meta if isinstance(beat.meta, dict) else {}
    raw = str(meta.get("event_id") or meta.get("id") or "").strip()
    if raw:
        return raw
    return "ev_" + message_id_for_beat(beat)[:24]


def message_id_for_beat(beat: Beat) -> str:
    meta = beat.meta if isinstance(beat.meta, dict) else {}
    raw = str(meta.get("message_id") or "").strip()
    if raw:
        return raw
    blob = json.dumps(beat.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def name_for_beat(beat: Beat) -> str:
    meta = beat.meta if isinstance(beat.meta, dict) else {}
    raw = str(meta.get("name") or "").strip()
    if raw:
        return raw
    if beat.kind == "action":
        return str(meta.get("tool") or meta.get("tool_name") or "").strip()
    if beat.kind == "tool_result":
        return str(meta.get("tool") or meta.get("tool_name") or "").strip()
    if beat.kind == "think":
        source = str(meta.get("source") or "").strip()
        return source
    return ""


class EventStore:
    """Append-only event store with idempotent inserts."""

    def __init__(self, db_path: Path, *, object_dir: Path | None = None) -> None:
        self.db_path = db_path
        self.object_store = ObjectStore(object_dir or db_path.parent / "objects")

    def append_beat(self, beat: Beat) -> str | None:
        """Insert one beat and return its event id, or None when duplicate."""
        self.ensure_schema()
        event_id = event_id_for_beat(beat)
        message_id = message_id_for_beat(beat)
        meta = beat.meta if isinstance(beat.meta, dict) else {}
        turn_id = str(meta.get("turn_id") or "").strip()
        request_id = str(meta.get("request_id") or "").strip()
        session_id = str(meta.get("session_id") or "").strip()
        surface = str(beat.surface or meta.get("surface") or "").strip().lower()
        name = name_for_beat(beat)
        dispatch_id = str(meta.get("dispatch_id") or "").strip()
        dispatch_target = str(meta.get("dispatch_target") or "").strip()
        dispatch_recipient = str(meta.get("dispatch_recipient") or "").strip()
        dispatch_status = str(meta.get("dispatch_status") or "").strip()
        dispatch_last_error = str(meta.get("dispatch_last_error") or "").strip()
        try:
            dispatch_attempts = int(meta.get("dispatch_attempts") or 0)
        except (TypeError, ValueError):
            dispatch_attempts = 0
        object_mime = str(meta.get("object_mime") or meta.get("mime") or "").strip()
        object_name = str(meta.get("object_name") or meta.get("name") or "").strip()
        try:
            object_size = int(meta.get("object_size") or meta.get("size") or 0)
        except (TypeError, ValueError):
            object_size = 0
        content = beat.content
        object_hash = str(meta.get("object_hash") or "").strip()
        inline_content = content
        if len(content.encode("utf-8")) > LARGE_CONTENT_BYTES:
            object_hash = self.object_store.put_text(content, suffix=".txt")
            inline_content = ""
        meta_json = json.dumps(beat.meta or {}, ensure_ascii=False, sort_keys=True)
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    id, message_id, turn_id, request_id, session_id, surface, t, actor, channel, kind, name,
                    content, runtime, meta_json, object_hash, content_size,
                    dispatch_id, dispatch_target, dispatch_recipient, dispatch_status, dispatch_attempts,
                    dispatch_last_error, object_mime, object_name, object_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    message_id,
                    turn_id,
                    request_id,
                    session_id,
                    surface,
                    beat.t.isoformat(),
                    beat.actor,
                    beat.channel,
                    beat.kind,
                    name,
                    inline_content,
                    beat.runtime,
                    meta_json,
                    object_hash,
                    len(content.encode("utf-8")),
                    dispatch_id,
                    dispatch_target,
                    dispatch_recipient,
                    dispatch_status,
                    dispatch_attempts,
                    dispatch_last_error,
                    object_mime,
                    object_name,
                    object_size,
                ),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
        finally:
            conn.close()
        return event_id

    def append_beats(self, beats: Iterable[Beat]) -> list[str]:
        ids: list[str] = []
        for beat in beats:
            event_id = self.append_beat(beat)
            if event_id:
                ids.append(event_id)
        return ids

    def read_beats(
        self,
        *,
        after: datetime | None = None,
        channel: str | None = None,
        surface: str | None = None,
        limit: int | None = None,
        ascending: bool = True,
    ) -> list[Beat]:
        if not self.db_path.exists():
            return []
        where: list[str] = []
        params: list[Any] = []
        if after is not None:
            where.append("t > ?")
            params.append(after.isoformat())
        if channel:
            where.append("channel = ?")
            params.append(channel)
        if surface:
            where.append("surface = ?")
            params.append(surface)
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t " + ("ASC" if ascending else "DESC")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        beats = [self._beat_from_row(row) for row in rows]
        if not ascending:
            beats.reverse()
        return beats

    def read_unembedded(self, *, limit: int = 100) -> list[Beat]:
        """Return events whose embedding/index work has not completed."""
        if not self.db_path.exists():
            return []
        self.ensure_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE embedded_at = '' ORDER BY t ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        return [self._beat_from_row(row) for row in rows]

    def read_event(self, event_id: str) -> Beat | None:
        """Return one event beat by id."""
        if not self.db_path.exists():
            return None
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (str(event_id),)).fetchone()
        finally:
            conn.close()
        return self._beat_from_row(row) if row is not None else None

    def update_event_meta(self, event_id: str, updates: dict[str, Any]) -> bool:
        """Merge derived metadata into one event row."""
        event_id = str(event_id or "").strip()
        if not event_id or not updates:
            return False
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute("SELECT meta_json FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                return False
            try:
                meta = json.loads(str(row["meta_json"] or "{}"))
            except json.JSONDecodeError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            meta.update({str(key): value for key, value in updates.items() if value not in (None, "")})
            cur = conn.execute(
                "UPDATE events SET meta_json = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False, sort_keys=True), event_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def enqueue_unembedded_memory_jobs(self, *, limit: int = 100, kind: str = "event") -> int:
        """Create idempotent memory jobs for unembedded events."""
        beats = self.read_unembedded(limit=limit)
        if not beats:
            return 0
        self.ensure_schema()
        now = datetime.now(timezone.utc).isoformat()
        changed = 0
        conn = self._connect()
        try:
            for beat in beats:
                event_id = str((beat.meta or {}).get("event_id") or "")
                if not event_id:
                    continue
                job_id = self.memory_job_id(event_id, kind=kind)
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_jobs (
                        job_id, event_id, kind, status, attempts, next_visible_at,
                        lease_until, leased_by, last_error, dead_lettered_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'pending', 0, '', '', '', '', '', ?, ?)
                    """,
                    (job_id, event_id, kind, now, now),
                )
                if conn.total_changes > before:
                    changed += 1
            conn.commit()
        finally:
            conn.close()
        return changed

    def enqueue_memory_job(self, event_id: str, *, kind: str = "event") -> bool:
        """Create one idempotent memory job for an event or derived object id."""
        event_id = str(event_id or "").strip()
        kind = str(kind or "event").strip() or "event"
        if not event_id:
            return False
        self.ensure_schema()
        now = datetime.now(timezone.utc).isoformat()
        job_id = self.memory_job_id(event_id, kind=kind)
        conn = self._connect()
        try:
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO memory_jobs (
                    job_id, event_id, kind, status, attempts, next_visible_at,
                    lease_until, leased_by, last_error, dead_lettered_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, '', '', '', '', '', ?, ?)
                """,
                (job_id, event_id, kind, now, now),
            )
            conn.commit()
            return conn.total_changes > before
        finally:
            conn.close()

    def claim_memory_jobs(self, *, limit: int = 100, worker_id: str = "", lease_seconds: int = 300) -> list[dict[str, Any]]:
        """Claim visible memory jobs without deleting them."""
        self.ensure_schema()
        now = datetime.now(timezone.utc)
        now_s = now.isoformat()
        lease_until = datetime.fromtimestamp(now.timestamp() + max(0, int(lease_seconds)), timezone.utc).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM memory_jobs
                WHERE status = 'pending'
                  AND dead_lettered_at = ''
                  AND (next_visible_at = '' OR next_visible_at <= ?)
                  AND (lease_until = '' OR lease_until <= ?)
                ORDER BY created_at ASC, job_id ASC
                LIMIT ?
                """,
                (now_s, now_s, int(limit)),
            ).fetchall()
            jobs = [dict(row) for row in rows]
            for job in jobs:
                conn.execute(
                    """
                    UPDATE memory_jobs
                    SET status = 'claimed', leased_by = ?, lease_until = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (worker_id or "memory-worker", lease_until, now_s, job["job_id"]),
                )
            conn.commit()
        finally:
            conn.close()
        return jobs

    def ack_memory_job(self, job_id: str) -> bool:
        """Mark a memory job as done after all derived writes succeed."""
        self.ensure_schema()
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'done', leased_by = '', lease_until = '', next_visible_at = '', updated_at = ?
                WHERE job_id = ?
                """,
                (now, str(job_id)),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def fail_memory_job(self, job_id: str, *, error: str = "", max_attempts: int = 3, backoff_seconds: int = 60) -> bool:
        """Retry a memory job later or dead-letter it after max attempts."""
        self.ensure_schema()
        now = datetime.now(timezone.utc)
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM memory_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"] or 0) + 1
            status = "dead_letter" if attempts >= max(1, int(max_attempts)) else "pending"
            dead_lettered_at = now.isoformat() if status == "dead_letter" else ""
            delay = max(0, int(backoff_seconds)) * attempts
            next_visible_at = "" if status == "dead_letter" else datetime.fromtimestamp(now.timestamp() + delay, timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?, attempts = ?, next_visible_at = ?, leased_by = '', lease_until = '',
                    last_error = ?, dead_lettered_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, attempts, next_visible_at, str(error or "")[:1000], dead_lettered_at, now.isoformat(), str(job_id)),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def read_memory_jobs(self, *, status: str = "") -> list[dict[str, Any]]:
        """Return memory jobs for tests and diagnostics."""
        if not self.db_path.exists():
            return []
        self.ensure_schema()
        conn = self._connect()
        try:
            if status:
                rows = conn.execute("SELECT * FROM memory_jobs WHERE status = ? ORDER BY created_at ASC, job_id ASC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM memory_jobs ORDER BY created_at ASC, job_id ASC").fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    @staticmethod
    def memory_job_id(event_id: str, *, kind: str = "event") -> str:
        seed = f"{kind}:{event_id}"
        return "mem_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    turn_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    surface TEXT NOT NULL DEFAULT '',
                    t TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    runtime TEXT,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    object_hash TEXT NOT NULL DEFAULT '',
                    content_size INTEGER NOT NULL DEFAULT 0,
                    dispatch_id TEXT NOT NULL DEFAULT '',
                    dispatch_target TEXT NOT NULL DEFAULT '',
                    dispatch_recipient TEXT NOT NULL DEFAULT '',
                    dispatch_status TEXT NOT NULL DEFAULT '',
                    dispatch_attempts INTEGER NOT NULL DEFAULT 0,
                    dispatch_last_error TEXT NOT NULL DEFAULT '',
                    object_mime TEXT NOT NULL DEFAULT '',
                    object_name TEXT NOT NULL DEFAULT '',
                    object_size INTEGER NOT NULL DEFAULT 0,
                    embed_model TEXT NOT NULL DEFAULT '',
                    embedded_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "turn_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "request_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "session_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "surface", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "dispatch_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "dispatch_target", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "dispatch_recipient", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "dispatch_status", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "dispatch_attempts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "dispatch_last_error", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "object_mime", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "object_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "object_size", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "embed_model", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "embedded_at", "TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_channel_t ON events(channel, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_surface_t ON events(surface, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_t ON events(t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_turn ON events(turn_id, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_request ON events(request_id, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_object_hash ON events(object_hash, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_name ON events(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dispatch ON events(dispatch_target, dispatch_status, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dispatch_id ON events(dispatch_id, t)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_jobs (
                    job_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'event',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_visible_at TEXT NOT NULL DEFAULT '',
                    lease_until TEXT NOT NULL DEFAULT '',
                    leased_by TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    dead_lettered_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, kind)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_jobs_status_visible ON memory_jobs(status, next_visible_at, lease_until)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_jobs_event ON memory_jobs(event_id, kind)")
            conn.commit()
        finally:
            conn.close()

    def mark_embedded(self, event_id: str, *, model_id: str, embedded_at: datetime) -> None:
        self.ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE events SET embed_model = ?, embedded_at = ? WHERE id = ?",
                (model_id, embedded_at.isoformat(), event_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _beat_from_row(self, row: sqlite3.Row) -> Beat:
        try:
            meta = json.loads(str(row["meta_json"] or "{}"))
            if not isinstance(meta, dict):
                meta = {}
        except json.JSONDecodeError:
            meta = {}
        meta.setdefault("event_id", row["id"])
        if row["turn_id"]:
            meta.setdefault("turn_id", row["turn_id"])
        if row["request_id"]:
            meta.setdefault("request_id", row["request_id"])
        if row["session_id"]:
            meta.setdefault("session_id", row["session_id"])
        if "surface" in row.keys() and row["surface"]:
            meta.setdefault("surface", row["surface"])
        if row["name"]:
            meta.setdefault("name", row["name"])
        if row["embed_model"]:
            meta.setdefault("embed_model", row["embed_model"])
        if row["embedded_at"]:
            meta.setdefault("embedded_at", row["embedded_at"])
        object_hash = str(row["object_hash"] or "")
        if object_hash:
            meta.setdefault("object_hash", object_hash)
        for column in (
            "dispatch_target",
            "dispatch_id",
            "dispatch_recipient",
            "dispatch_status",
            "dispatch_attempts",
            "dispatch_last_error",
            "object_mime",
            "object_name",
            "object_size",
        ):
            if column in row.keys() and row[column] not in ("", 0, None):
                meta.setdefault(column, row[column])
        content = str(row["content"] or "")
        if not content and object_hash:
            content = self.object_store.get_text(object_hash, suffix=".txt")
        return Beat(
            t=datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")),
            actor=row["actor"],
            channel=row["channel"],
            kind=row["kind"],
            content=content,
            runtime=row["runtime"],
            meta=meta or None,
            surface=row["surface"] if "surface" in row.keys() else "",
        )
