"""Test gorge segmentation + drift detection."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiam.gorge import (
    block_similarities,
    gorge,
    depth_scores,
    detect_drift,
    StreamGorge,
)


def _make_topic_embeddings(
    topics: list[int],
    dim: int = 64,
    noise: float = 0.05,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate embeddings where same topic → similar vectors.

    topics: e.g. [0,0,0,1,1,1,2,2] — 3 topics.
    Returns (len(topics), dim) normalized embeddings.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    unique = sorted(set(topics))
    centroids = {t: rng.standard_normal(dim).astype(np.float32) for t in unique}
    for c in centroids.values():
        c /= np.linalg.norm(c) + 1e-9

    vecs = []
    for t in topics:
        v = centroids[t] + rng.standard_normal(dim).astype(np.float32) * noise
        v /= np.linalg.norm(v) + 1e-9
        vecs.append(v)
    return np.array(vecs)


# ── Batch tests ───────────────────────────────────────────────────


def test_gorge_clear_topics():
    """Three distinct topics → boundaries between them."""
    topics = [0]*5 + [1]*5 + [2]*5
    embs = _make_topic_embeddings(topics, dim=64, noise=0.02)
    bounds, sims, depths = gorge(embs, window=2, confirm=2)
    # Should find boundaries near gap 4 and gap 9
    assert len(bounds) >= 1, f"Expected ≥1 boundary, got {bounds}"
    # At least one boundary should be close to gap 4 (between topic 0 and 1)
    near_4 = any(3 <= b <= 5 for b in bounds)
    near_9 = any(8 <= b <= 10 for b in bounds)
    assert near_4 or near_9, f"Boundaries {bounds} not near expected gaps 4/9"
    print(f"  clear topics: boundaries={bounds} OK")


def test_gorge_single_topic():
    """Uniform topic (zero noise) → no boundaries."""
    embs = _make_topic_embeddings([0]*10, dim=64, noise=0.0)
    bounds, sims, depths = gorge(embs, window=2, confirm=2)
    assert len(bounds) == 0, f"Expected 0 boundaries, got {bounds}"
    print("  single topic: no boundaries OK")


def test_gorge_too_few():
    """< 3 embeddings → empty result."""
    embs = _make_topic_embeddings([0, 1], dim=32, noise=0.01)
    bounds, sims, depths = gorge(embs)
    assert bounds == [] and sims == [] and depths == []
    print("  too few: empty OK")


def test_block_similarities():
    """Basic similarity shape check."""
    embs = _make_topic_embeddings([0]*4, dim=32, noise=0.01)
    sims = block_similarities(embs, window=2)
    assert len(sims) == 3
    # Same topic → high similarities
    assert all(s > 0.8 for s in sims), f"Expected high sims, got {sims}"
    print(f"  block sims: {[f'{s:.3f}' for s in sims]} OK")


def test_depth_scores_valley():
    """Manually craft a valley pattern."""
    sims = [0.9, 0.5, 0.9]  # clear valley at index 1
    depths = depth_scores(sims)
    assert depths[1] > depths[0], f"Valley not deepest: {depths}"
    assert depths[1] > depths[2], f"Valley not deepest: {depths}"
    print(f"  depth valley: {[f'{d:.3f}' for d in depths]} OK")


# ── Drift detection tests ─────────────────────────────────────────


def test_drift_same_topic():
    """Same topic → no drift."""
    rng = np.random.default_rng(99)
    c = rng.standard_normal(64).astype(np.float32)
    c /= np.linalg.norm(c)
    v1 = c + rng.standard_normal(64).astype(np.float32) * 0.02
    v2 = c + rng.standard_normal(64).astype(np.float32) * 0.02
    v1 /= np.linalg.norm(v1)
    v2 /= np.linalg.norm(v2)
    assert not detect_drift(v1, v2, 0.65)
    print("  same topic no drift OK")


def test_drift_different_topic():
    """Different topics → drift."""
    rng = np.random.default_rng(99)
    v1 = rng.standard_normal(64).astype(np.float32)
    v1 /= np.linalg.norm(v1)
    v2 = rng.standard_normal(64).astype(np.float32)
    v2 /= np.linalg.norm(v2)
    # Random orthogonal vectors → cosine ≈ 0 → drift
    assert detect_drift(v1, v2, 0.65)
    print("  different topic drift OK")


# ── StreamGorge tests ─────────────────────────────────────────────


def test_stream_basic():
    """Stream feeding → eventually gets a cut."""
    topics = [0]*6 + [1]*6
    embs = _make_topic_embeddings(topics, dim=64, noise=0.02)
    sg = StreamGorge(window=2, depth_confirm=2, stream_confirm=2, max_blocks=20)
    cut = None
    for i, vec in enumerate(embs):
        result = sg.push(vec)
        if result is not None:
            cut = (i, result)
            break
    assert cut is not None, "StreamGorge never confirmed a cut"
    push_idx, gap_idx = cut
    # Gap should be near the topic boundary (gap 5)
    assert 3 <= gap_idx <= 7, f"Cut gap {gap_idx} not near expected 5"
    consumed = sg.consume(gap_idx)
    assert len(consumed) == gap_idx + 1
    assert sg.size == push_idx + 1 - (gap_idx + 1)
    print(f"  stream basic: cut at push {push_idx}, gap {gap_idx} OK")


def test_stream_safety_valve():
    """Buffer exceeding max_blocks → force cut."""
    sg = StreamGorge(window=2, depth_confirm=2, stream_confirm=2, max_blocks=5)
    rng = np.random.default_rng(77)
    # All same topic → no natural cut, but max_blocks triggers
    centroid = rng.standard_normal(64).astype(np.float32)
    centroid /= np.linalg.norm(centroid)
    cut = None
    for i in range(10):
        v = centroid + rng.standard_normal(64).astype(np.float32) * 0.01
        v /= np.linalg.norm(v)
        result = sg.push(v)
        if result is not None:
            cut = (i, result)
            sg.consume(result)
            break
    assert cut is not None, "Safety valve should have triggered"
    print(f"  stream safety valve: cut at push {cut[0]}, gap {cut[1]} OK")


def test_stream_flush_all():
    """flush_all returns all vectors."""
    sg = StreamGorge()
    rng = np.random.default_rng(55)
    for _ in range(4):
        v = rng.standard_normal(32).astype(np.float32)
        sg.push(v)
    flushed = sg.flush_all()
    assert len(flushed) == 4
    assert sg.size == 0
    print("  stream flush_all OK")


def test_stream_multiple_cuts():
    """Three topics → at least 1 cut confirmed during streaming."""
    topics = [0]*5 + [1]*5 + [2]*5
    embs = _make_topic_embeddings(topics, dim=64, noise=0.02)
    sg = StreamGorge(window=2, depth_confirm=2, stream_confirm=2, max_blocks=30)
    cuts = []
    for vec in embs:
        result = sg.push(vec)
        if result is not None:
            cuts.append(result)
            sg.consume(result)
    assert len(cuts) >= 1, f"Expected ≥1 cut, got {cuts}"
    print(f"  stream multiple topics: {len(cuts)} cuts OK")


if __name__ == "__main__":
    print("Testing Gorge segmentation...")
    test_gorge_clear_topics()
    test_gorge_single_topic()
    test_gorge_too_few()
    test_block_similarities()
    test_depth_scores_valley()
    print("\nTesting drift detection...")
    test_drift_same_topic()
    test_drift_different_topic()
    print("\nTesting StreamGorge...")
    test_stream_basic()
    test_stream_safety_valve()
    test_stream_flush_all()
    test_stream_multiple_cuts()
    print("\nAll tests passed.")
