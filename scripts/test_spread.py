"""Test spreading activation retrieval on Pool."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiam.store.pool import Pool, Event
from fiam.retriever.spread import (
    seed_activation,
    spread_activation,
    select_events,
    retrieve,
)


def _make_pool(n_events: int, dim: int = 64, rng: np.random.Generator | None = None) -> tuple[Pool, Path]:
    """Create a temp Pool with n_events, each with a random fingerprint and simple edges."""
    if rng is None:
        rng = np.random.default_rng(42)
    tmp = tempfile.mkdtemp()
    pool = Pool(Path(tmp), dim=dim)
    pool.ensure_dirs()

    base_time = datetime(2026, 4, 10, tzinfo=timezone.utc)
    for i in range(n_events):
        vec = rng.standard_normal(dim).astype(np.float32)
        vec /= np.linalg.norm(vec) + 1e-9
        eid = f"ev_{i:03d}"
        t = base_time + timedelta(hours=i)
        pool.ingest_event(eid, t, f"Event {i} body text.", vec)

    return pool, Path(tmp)


def _add_chain_edges(pool: Pool, n: int) -> None:
    """Add temporal edges: 0→1→2→...→n-1."""
    srcs, dsts, types, weights = [], [], [], []
    for i in range(n - 1):
        srcs.append(i)
        dsts.append(i + 1)
        types.append(0)  # temporal
        weights.append(0.8)
    pool.add_edges_batch(srcs, dsts, types, weights)


# ── Seed tests ────────────────────────────────────────────────────


def test_seed_basic():
    """Seed activation produces non-zero values for similar events."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(10, 64, rng)
    # Use the first event's fingerprint as query
    fp = pool.load_fingerprints()
    query = fp[0]
    act = seed_activation(query, pool)
    assert len(act) == 10
    assert act[0] > 0.9, f"Self-similarity should be ~1.0, got {act[0]:.3f}"
    assert act.min() >= 0.0, "No negative activations"
    print(f"  seed basic: self={act[0]:.3f}, min={act.min():.3f}, max={act.max():.3f} OK")


def test_seed_shield():
    """Events after shield_after get zero activation."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(10, 64, rng)
    fp = pool.load_fingerprints()
    query = fp[5]
    # Shield all events created after event 7 (event 8, 9)
    base = datetime(2026, 4, 10, tzinfo=timezone.utc)
    shield_time = base + timedelta(hours=7, minutes=30)
    act = seed_activation(query, pool, shield_after=shield_time)
    assert act[8] == 0.0, f"Event 8 should be shielded, got {act[8]:.3f}"
    assert act[9] == 0.0, f"Event 9 should be shielded, got {act[9]:.3f}"
    assert act[5] > 0.5, f"Query event should still be active, got {act[5]:.3f}"
    print("  seed shield OK")


def test_seed_empty_pool():
    """Empty pool → empty activation."""
    tmp = tempfile.mkdtemp()
    pool = Pool(Path(tmp), dim=64)
    pool.ensure_dirs()
    query = np.random.default_rng(0).standard_normal(64).astype(np.float32)
    act = seed_activation(query, pool)
    assert len(act) == 0
    print("  seed empty pool OK")


# ── Spread tests ──────────────────────────────────────────────────


def test_spread_with_edges():
    """Spreading along a chain amplifies connected nodes."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(10, 64, rng)
    _add_chain_edges(pool, 10)

    # Only activate node 0
    act = np.zeros(10, dtype=np.float32)
    act[0] = 1.0

    result = spread_activation(act, pool, steps=2, decay=0.7, threshold=0.01)
    # Node 1 should have some activation (1-hop)
    assert result[1] > 0.0, f"Node 1 should be activated, got {result[1]:.3f}"
    # Activation should decay: node 0 > node 1 > node 2
    assert result[0] >= result[1], f"Node 0 ({result[0]:.3f}) should >= node 1 ({result[1]:.3f})"
    print(f"  spread chain: [0]={result[0]:.3f}, [1]={result[1]:.3f}, [2]={result[2]:.3f} OK")


def test_spread_no_edges():
    """No edges → activation unchanged (only normalised)."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(5, 64, rng)
    # No edges added
    act = np.array([1.0, 0.5, 0.3, 0.1, 0.0], dtype=np.float32)
    result = spread_activation(act, pool, steps=2, threshold=0.01)
    # Node 0 should still be highest (normalised to 1.0)
    assert result[0] == 1.0
    # Relative order preserved
    assert result[0] >= result[1] >= result[2] >= result[3]
    print("  spread no edges: order preserved OK")


def test_spread_bidirectional():
    """Test that edges are directed — only src→dst propagates."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(3, 64, rng)
    # Only edge: 0 → 2
    pool.add_edge(0, 2, type_id=2, weight=1.0)  # causal, strong

    act = np.zeros(3, dtype=np.float32)
    act[0] = 1.0

    result = spread_activation(act, pool, steps=1, decay=0.8, threshold=0.01)
    assert result[2] > 0.0, "Node 2 should get activation from node 0"
    # Node 1 has no incoming edges → should stay at 0
    assert result[1] == 0.0, f"Node 1 should be 0, got {result[1]:.3f}"
    print(f"  spread directed: [0]={result[0]:.3f}, [1]={result[1]:.3f}, [2]={result[2]:.3f} OK")


# ── Selection tests ───────────────────────────────────────────────


def test_select_deterministic():
    """With rng seed, selection is reproducible."""
    rng1 = np.random.default_rng(42)
    pool, _ = _make_pool(10, 64, rng1)

    act = np.linspace(0.0, 1.0, 10).astype(np.float32)

    result1 = select_events(act, pool, top_k=5, min_activation=0.15, rng=np.random.default_rng(99))
    result2 = select_events(act, pool, top_k=5, min_activation=0.15, rng=np.random.default_rng(99))
    assert result1 == result2, "Same seed → same selection"
    print(f"  select deterministic: {len(result1)} events OK")


def test_select_min_activation():
    """Events below min_activation are never selected."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(5, 64, rng)

    act = np.array([0.05, 0.1, 0.3, 0.6, 1.0], dtype=np.float32)
    # With min_activation=0.2, only indices 2,3,4 should be candidates
    results = select_events(act, pool, top_k=5, min_activation=0.2, rng=np.random.default_rng(42))
    selected_ids = {eid for eid, _ in results}
    assert "ev_000" not in selected_ids
    assert "ev_001" not in selected_ids
    print(f"  select min_activation filter: {len(results)} events OK")


def test_select_top_k_cap():
    """Selection never exceeds top_k."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(20, 64, rng)

    # All high activation → many candidates
    act = np.ones(20, dtype=np.float32)
    results = select_events(act, pool, top_k=3, min_activation=0.1, rng=np.random.default_rng(42))
    assert len(results) <= 3, f"Expected ≤3, got {len(results)}"
    print(f"  select top_k cap: {len(results)} ≤ 3 OK")


# ── End-to-end retrieve ──────────────────────────────────────────


def test_retrieve_e2e():
    """Full pipeline: seed → spread → select returns event IDs."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(15, 64, rng)
    _add_chain_edges(pool, 15)

    fp = pool.load_fingerprints()
    query = fp[5]  # Query similar to event 5

    results = retrieve(
        query, pool,
        steps=2, decay=0.5, threshold=0.01,
        top_k=5, min_activation=0.1,
        rng=np.random.default_rng(99),
    )
    assert len(results) > 0, "Should retrieve at least 1 event"
    assert all(isinstance(eid, str) for eid, _ in results)
    assert all(0.0 < act <= 1.0 for _, act in results)
    print(f"  retrieve e2e: {len(results)} events: {[(eid, f'{a:.2f}') for eid, a in results]} OK")


def test_retrieve_with_shield():
    """Retrieve with shield suppresses recent events."""
    rng = np.random.default_rng(42)
    pool, _ = _make_pool(10, 64, rng)
    _add_chain_edges(pool, 10)

    fp = pool.load_fingerprints()
    query = fp[9]  # Similar to event 9

    base = datetime(2026, 4, 10, tzinfo=timezone.utc)
    # Shield events 8, 9
    results = retrieve(
        query, pool,
        shield_after=base + timedelta(hours=7, minutes=30),
        steps=1, decay=0.5, threshold=0.01,
        top_k=5, min_activation=0.05,
        rng=np.random.default_rng(42),
    )
    selected_ids = {eid for eid, _ in results}
    assert "ev_008" not in selected_ids, "Event 8 should be shielded"
    assert "ev_009" not in selected_ids, "Event 9 should be shielded"
    print(f"  retrieve with shield: {len(results)} events (8,9 excluded) OK")


if __name__ == "__main__":
    print("Testing seed activation...")
    test_seed_basic()
    test_seed_shield()
    test_seed_empty_pool()
    print("\nTesting spread activation...")
    test_spread_with_edges()
    test_spread_no_edges()
    test_spread_bidirectional()
    print("\nTesting selection...")
    test_select_deterministic()
    test_select_min_activation()
    test_select_top_k_cap()
    print("\nTesting end-to-end retrieve...")
    test_retrieve_e2e()
    test_retrieve_with_shield()
    print("\nAll tests passed.")
