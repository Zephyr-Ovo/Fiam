"""Feature store for frozen embedding vectors.

The Pool keeps event fingerprints.  This module keeps beat-level base
vectors so annotation data can be trained later without re-embedding the
same flow text over and over.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from fiam.store.beat import Beat


def beat_key(beat: Beat) -> str:
    """Stable hash for one beat's persisted content."""
    blob = json.dumps(beat.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FeatureRecord:
    key: str
    vector_idx: int
    t: str
    source: str
    text_hash: str
    model_id: str
    dim: int

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "FeatureRecord":
        return FeatureRecord(
            key=str(data["key"]),
            vector_idx=int(data["vector_idx"]),
            t=str(data.get("t", "")),
            source=str(data.get("source", "")),
            text_hash=str(data.get("text_hash", "")),
            model_id=str(data.get("model_id", "")),
            dim=int(data.get("dim", 0)),
        )


class FeatureStore:
    """Append-only beat vector store."""

    def __init__(self, root: Path, *, dim: int) -> None:
        self.root = root
        self.dim = dim
        self.index_path = root / "flow_index.jsonl"
        self.vectors_path = root / "flow_vectors.npy"
        self._records: dict[str, FeatureRecord] | None = None
        self._vectors: np.ndarray | None = None

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def load_records(self) -> dict[str, FeatureRecord]:
        if self._records is not None:
            return self._records
        records: dict[str, FeatureRecord] = {}
        if self.index_path.exists():
            with open(self.index_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = FeatureRecord.from_dict(json.loads(line))
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
                    records[rec.key] = rec
        self._records = records
        return records

    def load_vectors(self) -> np.ndarray:
        if self._vectors is not None:
            return self._vectors
        if self.vectors_path.exists():
            try:
                self._vectors = np.load(self.vectors_path)
            except (OSError, ValueError):
                self._vectors = np.empty((0, self.dim), dtype=np.float32)
        else:
            self._vectors = np.empty((0, self.dim), dtype=np.float32)
        return self._vectors

    def append_beat_vector(self, beat: Beat, vec: np.ndarray, *, model_id: str) -> int:
        """Store a beat vector if absent; return vector row index."""
        key = beat_key(beat)
        records = self.load_records()
        existing = records.get(key)
        if existing is not None:
            return existing.vector_idx

        self.ensure_dirs()
        vectors = self.load_vectors()
        row = vec.astype(np.float32).reshape(1, -1)
        next_idx = vectors.shape[0]
        self._vectors = np.vstack([vectors, row]) if vectors.shape[0] else row
        np.save(self.vectors_path, self._vectors)

        text_hash = hashlib.sha256(beat.text.encode("utf-8")).hexdigest()
        rec = FeatureRecord(
            key=key,
            vector_idx=next_idx,
            t=beat.t.isoformat(),
            source=beat.source,
            text_hash=text_hash,
            model_id=model_id,
            dim=int(row.shape[1]),
        )
        with open(self.index_path, "a", encoding="utf-8") as handle:
            handle.write(rec.to_json() + "\n")
        records[key] = rec
        return next_idx

    def get_beat_vector(self, beat: Beat) -> np.ndarray | None:
        rec = self.load_records().get(beat_key(beat))
        if rec is None:
            return None
        vectors = self.load_vectors()
        if 0 <= rec.vector_idx < vectors.shape[0]:
            return vectors[rec.vector_idx]
        return None