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
    scene: str
    text_hash: str
    model_id: str
    dim: int
    vector_file: str = ""
    row_idx: int = -1

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "FeatureRecord":
        return FeatureRecord(
            key=str(data["key"]),
            vector_idx=int(data["vector_idx"]),
            t=str(data.get("t", "")),
            scene=str(data.get("scene") or data.get("source") or ""),
            text_hash=str(data.get("text_hash", "")),
            model_id=str(data.get("model_id", "")),
            dim=int(data.get("dim", 0)),
            vector_file=str(data.get("vector_file", "")),
            row_idx=int(data.get("row_idx", -1)),
        )


class FeatureStore:
    """Append-only beat vector store.

    Vectors are written into chunk files under ``chunks/`` so appending one
    beat does not rewrite the full historical matrix.
    """

    def __init__(self, root: Path, *, dim: int, chunk_size: int = 1024) -> None:
        self.root = root
        self.dim = dim
        self.chunk_size = chunk_size
        self.index_path = root / "flow_index.jsonl"
        self.chunks_dir = root / "chunks"
        self._records: dict[str, FeatureRecord] | None = None
        self._vectors: np.ndarray | None = None
        self._next_idx: int | None = None
        self._current_chunk_path: Path | None = None
        self._current_chunk: np.ndarray | None = None

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

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
        """Load all vectors as one matrix.

        This is mainly a compatibility helper. Hot paths should use
        ``get_beat_vector`` by index record instead of materialising all rows.
        """
        if self._vectors is not None:
            return self._vectors
        arrays: list[np.ndarray] = []
        for chunk in sorted(self.chunks_dir.glob("flow_vectors_*.npy")):
            try:
                arr = np.load(chunk)
                if arr.size:
                    arrays.append(arr.astype(np.float32, copy=False))
            except (OSError, ValueError):
                continue
        self._vectors = np.vstack(arrays) if arrays else np.empty((0, self.dim), dtype=np.float32)
        return self._vectors

    def count(self) -> int:
        """Return the number of indexed beat vectors."""
        records = self.load_records()
        if records:
            return max(rec.vector_idx for rec in records.values()) + 1
        return int(self.load_vectors().shape[0])

    def append_beat_vector(self, beat: Beat, vec: np.ndarray, *, model_id: str) -> int:
        """Store a beat vector if absent; return vector row index."""
        key = beat_key(beat)
        records = self.load_records()
        existing = records.get(key)
        if existing is not None:
            return existing.vector_idx

        self.ensure_dirs()
        row = vec.astype(np.float32).reshape(1, -1)
        if row.shape[1] != self.dim:
            raise ValueError(f"vector dim {row.shape[1]} != expected {self.dim}")
        next_idx = self._next_vector_idx()
        vector_file, row_idx = self._append_chunk(row)

        text_hash = hashlib.sha256(beat.text.encode("utf-8")).hexdigest()
        rec = FeatureRecord(
            key=key,
            vector_idx=next_idx,
            t=beat.t.isoformat(),
            scene=beat.scene,
            text_hash=text_hash,
            model_id=model_id,
            dim=int(row.shape[1]),
            vector_file=vector_file,
            row_idx=row_idx,
        )
        with open(self.index_path, "a", encoding="utf-8") as handle:
            handle.write(rec.to_json() + "\n")
        records[key] = rec
        self._next_idx = next_idx + 1
        self._vectors = None
        return next_idx

    def get_beat_vector(self, beat: Beat) -> np.ndarray | None:
        rec = self.load_records().get(beat_key(beat))
        if rec is None:
            return None
        if rec.vector_file:
            path = self.root / rec.vector_file
            try:
                vectors = np.load(path, mmap_mode="r")
            except (OSError, ValueError):
                return None
            if 0 <= rec.row_idx < vectors.shape[0]:
                return np.asarray(vectors[rec.row_idx], dtype=np.float32)
            return None
        vectors = self.load_vectors()
        if 0 <= rec.vector_idx < vectors.shape[0]:
            return vectors[rec.vector_idx]
        return None

    def _next_vector_idx(self) -> int:
        if self._next_idx is None:
            self._next_idx = self.count()
        return self._next_idx

    def _append_chunk(self, row: np.ndarray) -> tuple[str, int]:
        chunk_path, chunk = self._current_writable_chunk()
        row_idx = int(chunk.shape[0])
        updated = np.vstack([chunk, row]) if chunk.shape[0] else row
        np.save(chunk_path, updated)
        self._current_chunk_path = chunk_path
        self._current_chunk = updated
        return chunk_path.relative_to(self.root).as_posix(), row_idx

    def _current_writable_chunk(self) -> tuple[Path, np.ndarray]:
        if self._current_chunk_path is not None and self._current_chunk is not None:
            if self._current_chunk.shape[0] < self.chunk_size:
                return self._current_chunk_path, self._current_chunk

        chunks = sorted(self.chunks_dir.glob("flow_vectors_*.npy"))
        if chunks:
            last = chunks[-1]
            try:
                arr = np.load(last).astype(np.float32, copy=False)
            except (OSError, ValueError):
                arr = np.empty((0, self.dim), dtype=np.float32)
            if arr.ndim != 2 or arr.shape[1] != self.dim:
                arr = np.empty((0, self.dim), dtype=np.float32)
            if arr.shape[0] < self.chunk_size:
                self._current_chunk_path = last
                self._current_chunk = arr
                return last, arr
            next_num = _chunk_number(last) + 1
        else:
            next_num = 0

        path = self.chunks_dir / f"flow_vectors_{next_num:06d}.npy"
        arr = np.empty((0, self.dim), dtype=np.float32)
        self._current_chunk_path = path
        self._current_chunk = arr
        return path, arr


def _chunk_number(path: Path) -> int:
    try:
        return int(path.stem.rsplit("_", 1)[-1])
    except ValueError:
        return 0