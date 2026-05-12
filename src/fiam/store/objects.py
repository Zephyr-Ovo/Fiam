"""Content-addressed object storage for large event payloads."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ObjectStore:
    """Store large text/blob payloads by SHA-256 under ``store/objects``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put_text(self, text: str, *, suffix: str = ".txt") -> str:
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        path = self._path_for_hash(digest, suffix=suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return digest

    def get_text(self, digest: str, *, suffix: str = ".txt") -> str:
        if not digest:
            return ""
        path = self._path_for_hash(digest, suffix=suffix)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _path_for_hash(self, digest: str, *, suffix: str) -> Path:
        clean = "".join(ch for ch in digest.lower() if ch in "0123456789abcdef")
        if len(clean) != 64:
            raise ValueError("object hash must be a SHA-256 hex digest")
        return self.root / clean[:2] / f"{clean}{suffix}"
