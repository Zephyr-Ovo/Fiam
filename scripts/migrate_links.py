"""
Migrate old event links to the new graph format.

What this does:
  1. Strip ALL old bare-string temporal links (the 4h-window era)
  2. Recompute temporal links with the new 30min session gap
  3. Add semantic links (cosine > 0.75)
  4. Write updated events back to disk

This is a one-time migration. Safe to run multiple times (idempotent).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
from fiam.config import FiamConfig
from fiam.store.home import HomeStore

_SESSION_GAP = timedelta(minutes=30)
_SEM_THRESHOLD = 0.75


def _load_vec(event, config):
    if not event.embedding:
        return None
    npy_path = config.embeddings_dir.parent / event.embedding
    if not npy_path.exists():
        return None
    vec = np.load(npy_path).astype(np.float32).flatten()
    if vec.shape[0] != config.embedding_dim:
        return None
    return vec


def _cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def main():
    code_path = Path(__file__).resolve().parent.parent
    toml_path = code_path / "fiam.toml"
    config = FiamConfig.from_toml(toml_path, code_path)
    store = HomeStore(config)
    events = store.all_events()
    now = datetime.now(timezone.utc)

    print(f"Loaded {len(events)} events")
    old_total = sum(len(e.links) for e in events)
    print(f"Old links total: {old_total}")

    # Step 1: Clear all links
    for e in events:
        e.links = []

    # Step 2: Temporal links (30min session gap)
    temporal_count = 0
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            delta = abs(a.time - b.time)
            if delta <= _SESSION_GAP:
                weight = round(1.0 - delta.total_seconds() / _SESSION_GAP.total_seconds(), 4)
                weight = max(weight, 0.1)
                a.links.append({"id": b.event_id, "type": "temporal", "weight": weight})
                b.links.append({"id": a.event_id, "type": "temporal", "weight": weight})
                temporal_count += 1

    print(f"Temporal links (30min gap): {temporal_count} pairs")

    # Step 3: Semantic links (cosine > 0.75)
    vecs = {}
    for e in events:
        v = _load_vec(e, config)
        if v is not None:
            vecs[e.event_id] = v

    sem_count = 0
    eids = sorted(vecs.keys())
    for i, a_id in enumerate(eids):
        for b_id in eids[i + 1:]:
            sim = _cosine(vecs[a_id], vecs[b_id])
            if sim >= _SEM_THRESHOLD:
                w = round(sim, 4)
                ev_a = next(e for e in events if e.event_id == a_id)
                ev_b = next(e for e in events if e.event_id == b_id)
                ev_a.links.append({"id": b_id, "type": "semantic", "weight": w})
                ev_b.links.append({"id": a_id, "type": "semantic", "weight": w})
                sem_count += 1

    print(f"Semantic links (cos >= {_SEM_THRESHOLD}): {sem_count} pairs")

    new_total = sum(len(e.links) for e in events)
    print(f"\nNew links total: {new_total} (was {old_total})")

    # Step 4: Write back
    for e in events:
        store.update_metadata(e)

    print(f"Written {len(events)} events to disk.")

    # Verify
    sample = store.read_event(events[0].event_id)
    print(f"\nVerify {sample.event_id}: {len(sample.links)} links")
    for l in sample.links[:5]:
        print(f"  {l}")


if __name__ == "__main__":
    main()
