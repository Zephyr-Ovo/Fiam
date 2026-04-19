"""Smoke test for beat + pool data structures."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from fiam.store.beat import Beat, append_beat, append_beats, read_beats, iter_beats
from fiam.store.pool import Pool, Event


def test_beat_roundtrip():
    """Beat serialise → deserialise."""
    b = Beat(
        t=datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc),
        text="你好世界",
        source="tg",
        user="tg",
        ai="online",
    )
    d = b.to_dict()
    b2 = Beat.from_dict(d)
    assert b == b2, f"{b} != {b2}"
    print("  beat roundtrip OK")


def test_flow_io():
    """Append & read flow.jsonl."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "flow.jsonl"
        beats = [
            Beat(t=datetime(2026, 4, 19, i, 0, 0, tzinfo=timezone.utc),
                 text=f"beat {i}", source="cc", user="cc", ai="online")
            for i in range(5)
        ]
        append_beats(path, beats)
        loaded = read_beats(path)
        assert len(loaded) == 5
        assert loaded[0].text == "beat 0"
        assert loaded[4].text == "beat 4"

        # Incremental read
        partial, offset = iter_beats(path, 0)
        assert len(partial) == 5
        append_beat(path, Beat(
            t=datetime(2026, 4, 19, 5, 0, 0, tzinfo=timezone.utc),
            text="beat 5", source="tg", user="tg", ai="online",
        ))
        new, offset2 = iter_beats(path, offset)
        assert len(new) == 1
        assert new[0].text == "beat 5"
        print("  flow IO OK")


def test_pool_basic():
    """Pool: ingest event, check all layers."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = Pool(Path(tmp) / "pool", dim=4)
        pool.ensure_dirs()

        vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        ev = pool.ingest_event(
            event_id="ev_0419_001",
            t=datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc),
            body="test event body",
            fingerprint=vec,
        )
        assert ev.fingerprint_idx == 0
        assert pool.event_count == 1

        # Content
        body = pool.read_body("ev_0419_001")
        assert body == "test event body"

        # Fingerprints
        fp = pool.load_fingerprints()
        assert fp.shape == (1, 4)
        np.testing.assert_array_almost_equal(fp[0], vec)

        # Cosine
        cos = pool.load_cosine()
        assert cos.shape == (1, 1)
        assert abs(cos[0, 0] - 1.0) < 1e-6

        # Add second event
        vec2 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
        ev2 = pool.ingest_event(
            event_id="ev_0419_002",
            t=datetime(2026, 4, 19, 11, 0, 0, tzinfo=timezone.utc),
            body="second event",
            fingerprint=vec2,
        )
        assert ev2.fingerprint_idx == 1
        assert pool.event_count == 2

        cos = pool.load_cosine()
        assert cos.shape == (2, 2)
        assert abs(cos[0, 1]) < 1e-6  # orthogonal vectors
        assert abs(cos[1, 1] - 1.0) < 1e-6

        # Edges
        pool.add_edge(0, 1, Pool.edge_type_id("temporal"), 0.8)
        assert pool.edge_count == 1

        ei, ea = pool.load_edges()
        assert ei[0, 0] == 0 and ei[1, 0] == 1
        assert abs(ea[0, 1] - 0.8) < 1e-6

        # Event lookup
        found = pool.get_event("ev_0419_001")
        assert found is not None
        assert found.fingerprint_idx == 0

        # New ID generation
        new_id = pool.new_event_id()
        assert new_id == "ev_0419_003"

        print("  pool basic OK")


def test_pool_edges_batch():
    """Pool: batch edge operations."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = Pool(Path(tmp) / "pool", dim=4)
        pool.ensure_dirs()

        pool.add_edges_batch(
            src_indices=[0, 1, 2],
            dst_indices=[1, 2, 0],
            type_ids=[0, 1, 2],
            weights=[0.9, 0.7, 0.5],
        )
        assert pool.edge_count == 3

        pool.remove_edges_for(1)
        assert pool.edge_count == 1  # only 2→0 remains

        updated = pool.update_edge_weight(2, 0, 0.3)
        assert updated
        _, ea = pool.load_edges()
        assert abs(ea[0, 1] - 0.3) < 1e-6

        print("  pool edges batch OK")


if __name__ == "__main__":
    print("Testing beat + pool data structures...")
    test_beat_roundtrip()
    test_flow_io()
    test_pool_basic()
    test_pool_edges_batch()
    print("\nAll tests passed.")
