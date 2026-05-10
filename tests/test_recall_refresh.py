from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fiam.runtime.recall import refresh_recall
from fiam.store.pool import Event


@dataclass
class _Config:
    background_path: Path
    recall_top_k: int = 3


class _Pool:
    def __init__(self) -> None:
        self.event = Event(
            id="today_event",
            t=datetime.now(timezone.utc),
            access_count=0,
            fingerprint_idx=0,
        )
        self.saved = False

    def load_fingerprints(self) -> np.ndarray:
        return np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

    def load_edges(self):
        return np.empty((2, 0), dtype=np.int64), np.empty((0, 2), dtype=np.float32)

    def load_events(self) -> list[Event]:
        return [self.event]

    def get_event(self, event_id: str) -> Event | None:
        return self.event if event_id == self.event.id else None

    def read_body(self, event_id: str) -> str:
        return "today event body"

    def save_events(self) -> None:
        self.saved = True


class RecallRefreshTest(unittest.TestCase):
    def test_manual_recall_can_include_recent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _Config(background_path=Path(tmp) / "recall.md")
            pool = _Pool()

            shielded = refresh_recall(config, pool, np.array([1.0, 0.0, 0.0], dtype=np.float32))
            self.assertEqual(shielded, 0)
            self.assertFalse(config.background_path.exists())

            unshielded = refresh_recall(
                config,
                pool,
                np.array([1.0, 0.0, 0.0], dtype=np.float32),
                shield_recent=False,
            )
            self.assertEqual(unshielded, 1)
            self.assertIn("today event body", config.background_path.read_text(encoding="utf-8"))
            self.assertTrue(pool.saved)


if __name__ == "__main__":
    unittest.main()