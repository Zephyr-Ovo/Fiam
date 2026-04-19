"""Spreading activation retrieval on the Pool graph.

Replaces the legacy 4-factor scoring (semantic + retention + graph + MMR)
with a pure graph-spread approach:

  1. **Seed**: query_vec @ fingerprints.T → cosine similarities as initial
     activation. Today's events are shielded (zeroed out).
  2. **Spread**: multi-step propagation along Pool edges with type-dependent
     multipliers, fan penalty, and lateral inhibition.
  3. **Select**: each reached node fires independently with its activation
     value as Bernoulli probability.

Only depends on numpy arrays (Pool's fingerprints, edge_index, edge_attr).
No NetworkX, no torch, no scipy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import log

import numpy as np

from fiam.store.pool import Pool


# ------------------------------------------------------------------
# Edge type multipliers (how strongly each edge type conducts energy)
# ------------------------------------------------------------------

DEFAULT_TYPE_MULT: dict[int, float] = {
    0: 0.5,   # temporal  — weakest, just sequencing
    1: 0.8,   # semantic  — content similarity
    2: 1.4,   # causal    — strong link
    3: 1.2,   # remind    — association
    4: 1.0,   # elaboration
    5: 0.3,   # contrast  — weakest semantic link
}


# ------------------------------------------------------------------
# Seed: query vector → initial activation
# ------------------------------------------------------------------

def seed_activation(
    query_vec: np.ndarray,
    pool: Pool,
    *,
    shield_after: datetime | None = None,
) -> np.ndarray:
    """Compute initial activation from a query vector.

    Returns an array of shape (N,) where N = number of events in pool.
    Events created after *shield_after* get zero activation (prevents
    recalling things that just happened).
    """
    fp = pool.load_fingerprints()
    n = fp.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.float32)

    # Normalise
    q = query_vec.astype(np.float32).ravel()
    q_norm = np.linalg.norm(q)
    if q_norm < 1e-9:
        return np.zeros(n, dtype=np.float32)
    q = q / q_norm

    fp_norms = np.linalg.norm(fp, axis=1, keepdims=True)
    fp_norms = np.maximum(fp_norms, 1e-9)
    fp_normed = fp / fp_norms

    sims = (fp_normed @ q).astype(np.float32)  # (N,)
    # Clamp negatives
    sims = np.maximum(sims, 0.0)

    # Shield recent events
    if shield_after is not None:
        events = pool.load_events()
        for ev in events:
            if ev.fingerprint_idx >= 0 and ev.t >= shield_after:
                sims[ev.fingerprint_idx] = 0.0

    return sims


# ------------------------------------------------------------------
# Spread: propagate activation along edges
# ------------------------------------------------------------------

def spread_activation(
    activation: np.ndarray,
    pool: Pool,
    *,
    steps: int = 2,
    decay: float = 0.5,
    inhibition: float = 0.3,
    type_mult: dict[int, float] | None = None,
    threshold: float = 0.4,
) -> np.ndarray:
    """Propagate activation through Pool edges.

    Fire-once semantics: each node propagates only on the step it's first
    activated above threshold.

    Args:
        activation: Initial activation vector (N,).
        pool: Pool instance with loaded edges.
        steps: Number of propagation hops.
        decay: Energy multiplier per hop.
        inhibition: Lateral inhibition fraction when multi-source convergence.
        type_mult: Per-edge-type conductance multipliers.
        threshold: Minimum activation to keep propagating (stop condition).

    Returns:
        Final activation vector (N,) normalised to [0, 1].
    """
    if type_mult is None:
        type_mult = DEFAULT_TYPE_MULT

    n = len(activation)
    if n == 0:
        return activation.copy()

    edge_index, edge_attr = pool.load_edges()
    e_count = edge_index.shape[1] if edge_index.ndim == 2 else 0

    act = activation.copy().astype(np.float64)
    fired = act > threshold  # boolean mask

    # Pre-compute fan-out penalty per node
    fan_out = np.zeros(n, dtype=np.int64)
    if e_count > 0:
        srcs = edge_index[0]
        for s in srcs:
            if 0 <= s < n:
                fan_out[s] += 1

    for _step in range(steps):
        if e_count == 0:
            break

        delta = np.zeros(n, dtype=np.float64)
        source_count = np.zeros(n, dtype=np.int64)

        for ei in range(e_count):
            src = int(edge_index[0, ei])
            dst = int(edge_index[1, ei])
            if src < 0 or src >= n or dst < 0 or dst >= n:
                continue

            # Fire-once: only nodes active on previous step propagate
            if not fired[src]:
                continue
            if act[src] < threshold:
                continue

            w = float(edge_attr[ei, 1])
            tid = int(edge_attr[ei, 0])
            tm = type_mult.get(tid, 0.5)

            fan_pen = 1.0 / (1.0 + log(max(1, fan_out[src])))
            propagated = act[src] * w * tm * fan_pen * decay

            if propagated > 0.001:
                delta[dst] += propagated
                source_count[dst] += 1

        # Lateral inhibition: multi-source convergence dampened
        multi_source = source_count > 1
        delta[multi_source] *= (1.0 - inhibition)

        act += delta

        # Mark newly activated nodes as fired
        newly_fired = (act > threshold) & ~fired
        fired |= newly_fired

    # Normalise to [0, 1]
    max_a = act.max()
    if max_a > 0:
        act /= max_a

    return act.astype(np.float32)


# ------------------------------------------------------------------
# Select: probabilistic firing
# ------------------------------------------------------------------

def select_events(
    activation: np.ndarray,
    pool: Pool,
    *,
    top_k: int = 5,
    min_activation: float = 0.15,
    rng: np.random.Generator | None = None,
) -> list[tuple[str, float]]:
    """Select events by probabilistic firing.

    Each event with activation >= min_activation fires independently
    with its activation value as Bernoulli probability. Results are
    capped at top_k, ordered by activation descending.

    Returns list of (event_id, activation) tuples.
    """
    if rng is None:
        rng = np.random.default_rng()

    events = pool.load_events()
    # Build idx → event_id mapping
    idx_to_event: dict[int, str] = {}
    for ev in events:
        if ev.fingerprint_idx >= 0:
            idx_to_event[ev.fingerprint_idx] = ev.id

    n = len(activation)
    candidates: list[tuple[int, float]] = []
    for i in range(n):
        if activation[i] >= min_activation and i in idx_to_event:
            candidates.append((i, float(activation[i])))

    # Sort by activation descending for deterministic ordering
    candidates.sort(key=lambda x: -x[1])

    # Probabilistic firing: each candidate fires independently
    selected: list[tuple[str, float]] = []
    for idx, act_val in candidates:
        if len(selected) >= top_k:
            break
        if rng.random() < act_val:
            selected.append((idx_to_event[idx], act_val))

    return selected


# ------------------------------------------------------------------
# High-level API: retrieve
# ------------------------------------------------------------------

def retrieve(
    query_vec: np.ndarray,
    pool: Pool,
    *,
    shield_after: datetime | None = None,
    steps: int = 2,
    decay: float = 0.5,
    inhibition: float = 0.3,
    threshold: float = 0.4,
    top_k: int = 5,
    min_activation: float = 0.15,
    type_mult: dict[int, float] | None = None,
    rng: np.random.Generator | None = None,
) -> list[tuple[str, float]]:
    """Full retrieval pipeline: seed → spread → select.

    Args:
        query_vec: The query embedding (e.g. current sliding window mean).
        pool: Pool instance with all layers loaded.
        shield_after: Suppress events newer than this time.
        steps: Spreading activation hops.
        decay: Energy decay per hop.
        inhibition: Lateral inhibition factor.
        threshold: Activation floor for propagation.
        top_k: Max events to return.
        min_activation: Min activation to be a candidate.
        type_mult: Edge type conductance multipliers.
        rng: Random generator for reproducible selection.

    Returns:
        List of (event_id, activation) tuples, descending by activation.
    """
    act = seed_activation(query_vec, pool, shield_after=shield_after)
    if len(act) == 0:
        return []

    act = spread_activation(
        act, pool,
        steps=steps, decay=decay, inhibition=inhibition,
        type_mult=type_mult, threshold=threshold,
    )

    return select_events(
        act, pool,
        top_k=top_k, min_activation=min_activation, rng=rng,
    )
