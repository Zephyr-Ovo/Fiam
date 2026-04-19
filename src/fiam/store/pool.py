"""
Pool — unified event storage with layered data structures.

Layers:
  1. Content  — events/<id>.md (human-readable body text)
  2. Meta     — events.jsonl   ({id, t, access_count, fingerprint_idx})
  3. Finger   — fingerprints.npy (N × dim matrix, row = fingerprint_idx)
  4. Cosine   — cosine.npy     (N × N pairwise similarity, for edge candidates)
  5. Edges    — PyG format: edge_index.npy [2, E] + edge_attr.npy [E, D]

All layers use fingerprint_idx as the shared index into the matrix rows.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Event node — minimal metadata
# ------------------------------------------------------------------

@dataclass
class Event:
    """Lightweight event node. Heavy data lives in matrix layers."""

    id: str                   # e.g. "ev_0419_001"
    t: datetime               # creation time (UTC)
    access_count: int = 0     # how many times recalled
    fingerprint_idx: int = -1 # row index in fingerprints.npy (-1 = not yet embedded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "t": self.t.isoformat(),
            "access_count": self.access_count,
            "fingerprint_idx": self.fingerprint_idx,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Event:
        t = d["t"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return Event(
            id=d["id"],
            t=t,
            access_count=d.get("access_count", 0),
            fingerprint_idx=d.get("fingerprint_idx", -1),
        )


# ------------------------------------------------------------------
# Pool — unified store
# ------------------------------------------------------------------

class Pool:
    """Unified layered event storage."""

    def __init__(self, pool_dir: Path, *, dim: int = 768) -> None:
        self.root = pool_dir
        self.dim = dim

        # Layer paths
        self.content_dir = pool_dir / "events"
        self.meta_path = pool_dir / "events.jsonl"
        self.fingerprints_path = pool_dir / "fingerprints.npy"
        self.cosine_path = pool_dir / "cosine.npy"
        self.edge_index_path = pool_dir / "edge_index.npy"
        self.edge_attr_path = pool_dir / "edge_attr.npy"

        # In-memory caches (lazy-loaded)
        self._events: list[Event] | None = None
        self._fingerprints: np.ndarray | None = None
        self._cosine: np.ndarray | None = None
        self._edge_index: np.ndarray | None = None
        self._edge_attr: np.ndarray | None = None

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.content_dir.mkdir(parents=True, exist_ok=True)

    # ==================================================================
    # Layer 1: Content (.md files)
    # ==================================================================

    def read_body(self, event_id: str) -> str:
        path = self.content_dir / f"{event_id}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_body(self, event_id: str, body: str) -> None:
        self.content_dir.mkdir(parents=True, exist_ok=True)
        path = self.content_dir / f"{event_id}.md"
        path.write_text(body, encoding="utf-8")

    # ==================================================================
    # Layer 2: Metadata (events.jsonl)
    # ==================================================================

    def load_events(self) -> list[Event]:
        """Load all event metadata. Cached after first call."""
        if self._events is not None:
            return self._events
        events: list[Event] = []
        if self.meta_path.exists():
            with open(self.meta_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(Event.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError) as exc:
                        logger.warning("events.jsonl:%d skipped (corrupt): %s", line_no, exc)
                        continue
        self._events = events
        return events

    def save_events(self, events: list[Event] | None = None) -> None:
        """Rewrite the full events.jsonl from the in-memory list."""
        if events is not None:
            self._events = events
        if self._events is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.meta_path, "w", encoding="utf-8") as f:
            for ev in self._events:
                f.write(ev.to_json() + "\n")

    def append_event(self, event: Event) -> None:
        """Append a single event to the index."""
        events = self.load_events()
        events.append(event)
        self._events = events
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.meta_path, "a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")

    def get_event(self, event_id: str) -> Event | None:
        for ev in self.load_events():
            if ev.id == event_id:
                return ev
        return None

    def new_event_id(self, prefix: str = "ev") -> str:
        """Generate sequential event ID for today: ev_MMDD_NNN."""
        today = datetime.now(timezone.utc).strftime("%m%d")
        pat = re.compile(rf"^{re.escape(prefix)}_{today}_(\d{{3}})$")
        max_seq = 0
        for ev in self.load_events():
            m = pat.match(ev.id)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"{prefix}_{today}_{max_seq + 1:03d}"

    @property
    def event_count(self) -> int:
        return len(self.load_events())

    # ==================================================================
    # Layer 3: Fingerprints (N × dim matrix)
    # ==================================================================

    def load_fingerprints(self) -> np.ndarray:
        """Load fingerprint matrix. Returns empty (0, dim) if not exists."""
        if self._fingerprints is not None:
            return self._fingerprints
        if self.fingerprints_path.exists():
            try:
                self._fingerprints = np.load(self.fingerprints_path)
            except (ValueError, OSError) as exc:
                logger.error("fingerprints.npy corrupt, starting empty: %s", exc)
                self._fingerprints = np.empty((0, self.dim), dtype=np.float32)
        else:
            self._fingerprints = np.empty((0, self.dim), dtype=np.float32)
        return self._fingerprints

    def append_fingerprint(self, vec: np.ndarray) -> int:
        """Add a vector to the fingerprint matrix. Returns its row index."""
        fp = self.load_fingerprints()
        vec = vec.astype(np.float32).reshape(1, -1)
        new_fp = np.vstack([fp, vec]) if fp.shape[0] > 0 else vec
        self._fingerprints = new_fp
        self._save_fingerprints()
        return new_fp.shape[0] - 1

    def update_fingerprint(self, idx: int, vec: np.ndarray) -> None:
        """Replace fingerprint at given index (e.g. after re-embed from console edit)."""
        fp = self.load_fingerprints()
        fp[idx] = vec.astype(np.float32)
        self._save_fingerprints()

    def _save_fingerprints(self) -> None:
        if self._fingerprints is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            np.save(self.fingerprints_path, self._fingerprints)

    # ==================================================================
    # Layer 4: Cosine similarity matrix (N × N)
    # ==================================================================

    def load_cosine(self) -> np.ndarray:
        """Load pairwise cosine similarity matrix."""
        if self._cosine is not None:
            return self._cosine
        if self.cosine_path.exists():
            try:
                self._cosine = np.load(self.cosine_path)
            except (ValueError, OSError) as exc:
                logger.error("cosine.npy corrupt, starting empty: %s", exc)
                self._cosine = np.empty((0, 0), dtype=np.float32)
        else:
            self._cosine = np.empty((0, 0), dtype=np.float32)
        return self._cosine

    def rebuild_cosine(self) -> np.ndarray:
        """Recompute full N×N cosine matrix from fingerprints."""
        fp = self.load_fingerprints()
        n = fp.shape[0]
        if n == 0:
            self._cosine = np.empty((0, 0), dtype=np.float32)
        else:
            norms = np.linalg.norm(fp, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-9)
            normed = fp / norms
            self._cosine = (normed @ normed.T).astype(np.float32)
        self._save_cosine()
        return self._cosine

    def extend_cosine(self, new_vec: np.ndarray) -> np.ndarray:
        """Incrementally extend cosine matrix by one new vector.

        Computes similarities of the new vector against all existing ones
        and appends one row + one column. Much cheaper than full rebuild.
        """
        fp = self.load_fingerprints()
        cos = self.load_cosine()
        n = fp.shape[0]

        # new_vec is already the last row in fingerprints
        new_norm = np.linalg.norm(new_vec)
        if new_norm < 1e-9:
            new_normed = np.zeros(self.dim, dtype=np.float32)
        else:
            new_normed = (new_vec / new_norm).astype(np.float32)

        if cos.shape[0] == 0 or cos.shape[0] != n - 1:
            # Mismatch — full rebuild
            return self.rebuild_cosine()

        # Existing fingerprints (all except the last = new one)
        old_fp = fp[:-1]
        old_norms = np.linalg.norm(old_fp, axis=1, keepdims=True)
        old_norms = np.maximum(old_norms, 1e-9)
        old_normed = old_fp / old_norms

        sims = (old_normed @ new_normed).astype(np.float32)  # (n-1,)

        # Build new row/col
        new_row = np.append(sims, 1.0).reshape(1, -1)  # (1, n)
        expanded = np.vstack([
            np.hstack([cos, sims.reshape(-1, 1)]),
            new_row,
        ])
        self._cosine = expanded.astype(np.float32)
        self._save_cosine()
        return self._cosine

    def _save_cosine(self) -> None:
        if self._cosine is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            np.save(self.cosine_path, self._cosine)

    # ==================================================================
    # Layer 5: Edges — PyG format
    # ==================================================================

    def load_edges(self) -> tuple[np.ndarray, np.ndarray]:
        """Load edge_index [2, E] and edge_attr [E, D].

        edge_attr columns: [type_id, weight]
        type_id is an integer encoding of the edge type string.
        """
        if self._edge_index is not None and self._edge_attr is not None:
            return self._edge_index, self._edge_attr
        if self.edge_index_path.exists() and self.edge_attr_path.exists():
            try:
                self._edge_index = np.load(self.edge_index_path)
                self._edge_attr = np.load(self.edge_attr_path)
            except (ValueError, OSError) as exc:
                logger.error("edge npy files corrupt, starting empty: %s", exc)
                self._edge_index = np.empty((2, 0), dtype=np.int64)
                self._edge_attr = np.empty((0, 2), dtype=np.float32)
        else:
            self._edge_index = np.empty((2, 0), dtype=np.int64)
            self._edge_attr = np.empty((0, 2), dtype=np.float32)
        return self._edge_index, self._edge_attr

    def add_edge(self, src_idx: int, dst_idx: int, type_id: int, weight: float) -> None:
        """Append a single directed edge."""
        ei, ea = self.load_edges()
        new_col = np.array([[src_idx], [dst_idx]], dtype=np.int64)
        new_attr = np.array([[float(type_id), weight]], dtype=np.float32)
        self._edge_index = np.hstack([ei, new_col]) if ei.shape[1] > 0 else new_col
        self._edge_attr = np.vstack([ea, new_attr]) if ea.shape[0] > 0 else new_attr
        self._save_edges()

    def add_edges_batch(
        self,
        src_indices: list[int],
        dst_indices: list[int],
        type_ids: list[int],
        weights: list[float],
    ) -> None:
        """Append multiple edges at once."""
        if not src_indices:
            return
        ei, ea = self.load_edges()
        new_ei = np.array([src_indices, dst_indices], dtype=np.int64)
        new_ea = np.column_stack([
            np.array(type_ids, dtype=np.float32),
            np.array(weights, dtype=np.float32),
        ])
        self._edge_index = np.hstack([ei, new_ei]) if ei.shape[1] > 0 else new_ei
        self._edge_attr = np.vstack([ea, new_ea]) if ea.shape[0] > 0 else new_ea
        self._save_edges()

    def remove_edges_for(self, idx: int) -> None:
        """Remove all edges involving a given node index."""
        ei, ea = self.load_edges()
        if ei.shape[1] == 0:
            return
        mask = (ei[0] != idx) & (ei[1] != idx)
        self._edge_index = ei[:, mask]
        self._edge_attr = ea[mask]
        self._save_edges()

    def update_edge_weight(self, src_idx: int, dst_idx: int, weight: float) -> bool:
        """Update weight of an existing edge. Returns True if found."""
        ei, ea = self.load_edges()
        mask = (ei[0] == src_idx) & (ei[1] == dst_idx)
        if not mask.any():
            return False
        ea[mask, 1] = weight
        self._save_edges()
        return True

    def _save_edges(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self._edge_index is not None:
            np.save(self.edge_index_path, self._edge_index)
        if self._edge_attr is not None:
            np.save(self.edge_attr_path, self._edge_attr)

    @property
    def edge_count(self) -> int:
        ei, _ = self.load_edges()
        return ei.shape[1]

    # ==================================================================
    # Edge type registry
    # ==================================================================

    # Type string ↔ integer mapping for compact edge_attr storage
    EDGE_TYPES: dict[str, int] = {
        "temporal":    0,
        "semantic":    1,
        "causal":      2,
        "cause":       2,   # alias
        "remind":      3,
        "elaboration": 4,
        "contrast":    5,
    }
    EDGE_TYPE_NAMES: dict[int, str] = {
        0: "temporal",
        1: "semantic",
        2: "causal",
        3: "remind",
        4: "elaboration",
        5: "contrast",
    }

    @classmethod
    def edge_type_id(cls, name: str) -> int:
        return cls.EDGE_TYPES.get(name, 1)  # default to semantic

    @classmethod
    def edge_type_name(cls, type_id: int) -> str:
        return cls.EDGE_TYPE_NAMES.get(type_id, "semantic")

    # ==================================================================
    # High-level operations
    # ==================================================================

    def ingest_event(
        self,
        event_id: str,
        t: datetime,
        body: str,
        fingerprint: np.ndarray,
    ) -> Event:
        """Full pipeline: write body → append fingerprint → extend cosine → save meta.

        Returns the new Event with fingerprint_idx set.
        Raises on failure — caller should handle. Partial writes are logged
        so they can be repaired (body may exist without meta entry).
        """
        self.ensure_dirs()

        # Content (safe to write first — orphan .md files are harmless)
        self.write_body(event_id, body)

        try:
            # Fingerprint
            idx = self.append_fingerprint(fingerprint)

            # Cosine (incremental)
            self.extend_cosine(fingerprint)
        except Exception:
            logger.error("ingest_event %s: fingerprint/cosine failed, body written as orphan", event_id)
            raise

        # Metadata
        ev = Event(id=event_id, t=t, fingerprint_idx=idx)
        self.append_event(ev)

        return ev

    def invalidate_caches(self) -> None:
        """Force reload from disk on next access."""
        self._events = None
        self._fingerprints = None
        self._cosine = None
        self._edge_index = None
        self._edge_attr = None

    def delete_event(self, event_id: str) -> bool:
        """Delete an event and all related data (body, fingerprint row, cosine row/col, edges).

        Because removing a fingerprint row shifts all higher indices,
        edges referencing shifted indices are updated accordingly.
        Returns True if the event existed and was deleted.
        """
        ev = self.get_event(event_id)
        if ev is None:
            return False

        idx = ev.fingerprint_idx

        # 1. Remove edges involving this node
        self.remove_edges_for(idx)

        # 2. Shift edge indices > idx down by 1 (because fingerprint row removed)
        ei, ea = self.load_edges()
        if ei.shape[1] > 0:
            ei = ei.copy()
            ei[0, ei[0] > idx] -= 1
            ei[1, ei[1] > idx] -= 1
            self._edge_index = ei
            self._save_edges()

        # 3. Remove fingerprint row
        fp = self.load_fingerprints()
        if 0 <= idx < fp.shape[0]:
            self._fingerprints = np.delete(fp, idx, axis=0)
            self._save_fingerprints()

        # 4. Remove cosine row + column
        cos = self.load_cosine()
        if 0 <= idx < cos.shape[0]:
            self._cosine = np.delete(np.delete(cos, idx, axis=0), idx, axis=1)
            self._save_cosine()

        # 5. Remove from event list and update fingerprint_idx for shifted events
        events = self.load_events()
        events = [e for e in events if e.id != event_id]
        for e in events:
            if e.fingerprint_idx > idx:
                e.fingerprint_idx -= 1
        self.save_events(events)

        # 6. Remove body file
        body_path = self.content_dir / f"{event_id}.md"
        if body_path.exists():
            body_path.unlink()

        logger.info("Deleted event %s (fingerprint_idx=%d)", event_id, idx)
        return True
