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
        return self.put_bytes(data, suffix=suffix)

    def put_bytes(self, data: bytes, *, suffix: str = "") -> str:
        raw = bytes(data or b"")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.path_for_hash(digest, suffix=suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
        return digest

    def get_text(self, digest: str, *, suffix: str = ".txt") -> str:
        if not digest:
            return ""
        data = self.get_bytes(digest, suffix=suffix)
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def get_bytes(self, digest: str, *, suffix: str = "") -> bytes:
        if not digest:
            return b""
        path = self.path_for_hash(digest, suffix=suffix)
        try:
            return path.read_bytes()
        except OSError:
            return b""

    def path_for_hash(self, digest: str, *, suffix: str = "") -> Path:
        clean = "".join(ch for ch in digest.lower() if ch in "0123456789abcdef")
        if len(clean) != 64:
            raise ValueError("object hash must be a SHA-256 hex digest")
        return self.root / clean[:2] / f"{clean}{suffix}"

    def _path_for_hash(self, digest: str, *, suffix: str) -> Path:
        return self.path_for_hash(digest, suffix=suffix)
