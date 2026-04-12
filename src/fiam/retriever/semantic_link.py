"""
Semantic co-occurrence linker.

After a new event is embedded, compare its vector to all existing events.
Add bidirectional "semantic" links for pairs with cosine similarity > threshold.
Weight = cosine similarity itself (already in [0, 1]).
"""

from __future__ import annotations

import numpy as np

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord

_DEFAULT_THRESHOLD = 0.75


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _linked_ids(event: EventRecord) -> set[str]:
    return {link["id"] for link in event.links if isinstance(link, dict)}


def _load_vec(event: EventRecord, config: FiamConfig) -> np.ndarray | None:
    if not event.embedding:
        return None
    npy_path = config.embeddings_dir.parent / event.embedding
    if not npy_path.exists():
        return None
    vec = np.load(npy_path).astype(np.float32).flatten()
    if vec.shape[0] != config.embedding_dim:
        return None
    return vec


def link_semantic(
    new_events: list[EventRecord],
    all_events: list[EventRecord],
    config: FiamConfig,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[EventRecord]:
    """Add semantic links between new events and all events above threshold.

    Returns existing events whose links were modified (caller persists them).
    """
    modified_existing: list[EventRecord] = []

    # Pre-load new event vectors
    new_vecs: dict[str, np.ndarray] = {}
    for ev in new_events:
        vec = _load_vec(ev, config)
        if vec is not None:
            new_vecs[ev.event_id] = vec

    if not new_vecs:
        return modified_existing

    for existing in all_events:
        existing_vec = _load_vec(existing, config)
        if existing_vec is None:
            continue

        for new_ev in new_events:
            if new_ev.event_id == existing.event_id:
                continue
            new_vec = new_vecs.get(new_ev.event_id)
            if new_vec is None:
                continue

            sim = _cosine(new_vec, existing_vec)
            if sim < threshold:
                continue

            weight = round(sim, 4)

            if existing.event_id not in _linked_ids(new_ev):
                new_ev.links.append({
                    "id": existing.event_id,
                    "type": "semantic",
                    "weight": weight,
                })
            if new_ev.event_id not in _linked_ids(existing):
                existing.links.append({
                    "id": new_ev.event_id,
                    "type": "semantic",
                    "weight": weight,
                })
                if existing not in modified_existing:
                    modified_existing.append(existing)

    return modified_existing
