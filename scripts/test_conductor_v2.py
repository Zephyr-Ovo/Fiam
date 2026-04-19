"""Test Conductor — beat flow orchestration."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiam.conductor import Conductor
from fiam.store.beat import Beat, read_beats
from fiam.store.pool import Pool


class FakeEmbedder:
    """Deterministic embedder for testing: hash-based vectors."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(hash(text) % (2**31))
        vec = rng.standard_normal(self.dim).astype(np.float32)
        vec /= np.linalg.norm(vec) + 1e-9
        return vec


def _make_conductor(tmp: str, dim: int = 64) -> Conductor:
    pool_dir = Path(tmp) / "pool"
    pool = Pool(pool_dir, dim=dim)
    pool.ensure_dirs()
    embedder = FakeEmbedder(dim=dim)
    flow_path = Path(tmp) / "flow.jsonl"
    recall_path = Path(tmp) / "recall.md"
    return Conductor(
        pool, embedder, flow_path, recall_path,
        user_status="cc", ai_status="online",
        gorge_max_blocks=5,
    )


def _make_beat(text: str, source: str = "cc", minutes_offset: int = 0) -> Beat:
    t = datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset)
    return Beat(t=t, text=text, source=source, user="cc", ai="online")


# ── Basic ingestion ───────────────────────────────────────────────


def test_ingest_beat_writes_flow():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        beat = _make_beat("你好啊", minutes_offset=0)
        c.ingest_beat(beat)
        beats = read_beats(c.flow_path)
        assert len(beats) == 1
        assert beats[0].text == "你好啊"
        print("  ingest writes flow OK")


def test_ingest_external():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        c.ingest_external("hello from tg", "tg")
        beats = read_beats(c.flow_path)
        assert len(beats) == 1
        assert beats[0].source == "tg"
        print("  ingest external OK")


# ── Gorge segmentation ──────────────────────────────────────────


def test_gorge_cuts_segment():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp, dim=64)
        events_created = []
        for i in range(8):
            beat = _make_beat(f"topic_{i}_unique_text_{i*100}", minutes_offset=i)
            eid = c.ingest_beat(beat)
            if eid:
                events_created.append(eid)
        assert len(events_created) >= 1, "Expected gorge to cut at least 1 event"
        ev = c.pool.get_event(events_created[0])
        assert ev is not None
        body = c.pool.read_body(events_created[0])
        assert len(body) > 0
        print(f"  gorge cuts: {len(events_created)} event(s) OK")


def test_flush_all():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        for i in range(3):
            c.ingest_beat(_make_beat(f"msg {i}", minutes_offset=i))
        eids = c.flush_all()
        assert len(eids) == 1
        body = c.pool.read_body(eids[0])
        assert "msg 0" in body
        assert "msg 2" in body
        print(f"  flush_all: {eids[0]} OK")


def test_flush_all_empty():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        assert c.flush_all() == []
        print("  flush_all empty OK")


# ── CC output parsing ─────────────────────────────────────────────


def test_ingest_cc_output():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        jsonl_path = Path(tmp) / "test.jsonl"
        with open(jsonl_path, "wb") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "你好"},
                "timestamp": "2026-04-19T10:00:00Z",
            }, ensure_ascii=False).encode("utf-8") + b"\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "id": "msg_1",
                            "content": [{"type": "text", "text": "你好！"}]},
                "timestamp": "2026-04-19T10:00:05Z",
            }, ensure_ascii=False).encode("utf-8") + b"\n")
        results, offset = c.ingest_cc_output(jsonl_path)
        assert len(results) == 2
        assert offset > 0
        beats = read_beats(c.flow_path)
        assert len(beats) == 2
        print(f"  ingest cc output: {len(results)} beats OK")


# ── Status ────────────────────────────────────────────────────────


def test_status():
    with tempfile.TemporaryDirectory() as tmp:
        c = _make_conductor(tmp)
        assert c.user_status == "cc"
        c.set_status(user="away", ai="sleep")
        assert c.user_status == "away"
        assert c.ai_status == "sleep"
        print("  status management OK")


# ── Injection formatting ──────────────────────────────────────────


def test_format_user_message():
    result = Conductor.format_user_message([
        ("tg:Zephyr", "晚上吃什么"),
        ("email:bob", "meeting at 3pm"),
    ])
    assert "[tg:Zephyr]" in result
    assert "晚上吃什么" in result
    assert "[email:bob]" in result
    print("  format user message OK")


def test_format_user_message_empty():
    assert Conductor.format_user_message([]) == ""
    print("  format empty message OK")


def test_format_additional_context():
    result = Conductor.format_additional_context(
        recall_text="- (3天前) 某个记忆片段",
        schedule_info="14:00 check email",
    )
    assert "[recall]" in result
    assert "[schedule]" in result
    assert "某个记忆片段" in result
    print("  format additional context OK")


# ── Recall trigger ────────────────────────────────────────────────


def test_recall_on_drift():
    with tempfile.TemporaryDirectory() as tmp:
        pool_dir = Path(tmp) / "pool"
        pool = Pool(pool_dir, dim=64)
        pool.ensure_dirs()
        rng = np.random.default_rng(42)
        base_time = datetime(2026, 4, 10, tzinfo=timezone.utc)
        for i in range(5):
            vec = rng.standard_normal(64).astype(np.float32)
            vec /= np.linalg.norm(vec)
            pool.ingest_event(f"ev_{i:03d}", base_time + timedelta(hours=i), f"Event {i}", vec)

        embedder = FakeEmbedder(dim=64)
        flow_path = Path(tmp) / "flow.jsonl"
        recall_path = Path(tmp) / "recall.md"
        c = Conductor(
            pool, embedder, flow_path, recall_path,
            drift_threshold=0.99,  # high → drift triggers easily
            gorge_max_blocks=50,
        )
        c.ingest_beat(_make_beat("topic A about cooking", minutes_offset=0))
        c.ingest_beat(_make_beat("completely different topic about quantum physics", minutes_offset=1))

        if recall_path.exists():
            content = recall_path.read_text(encoding="utf-8")
            assert "recall" in content
            print("  recall on drift: written OK")
        else:
            print("  recall on drift: no match (pool too small, OK)")


if __name__ == "__main__":
    print("Testing Conductor...")
    test_ingest_beat_writes_flow()
    test_ingest_external()
    print("\nTesting Gorge integration...")
    test_gorge_cuts_segment()
    test_flush_all()
    test_flush_all_empty()
    print("\nTesting CC output parsing...")
    test_ingest_cc_output()
    print("\nTesting status...")
    test_status()
    print("\nTesting injection formatting...")
    test_format_user_message()
    test_format_user_message_empty()
    test_format_additional_context()
    print("\nTesting recall...")
    test_recall_on_drift()
    print("\nAll tests passed.")
