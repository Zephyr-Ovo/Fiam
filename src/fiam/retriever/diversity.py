"""
Greedy diversity filter for retrieved events.

Avoids returning events whose embeddings are nearly identical
by iterating candidates (already ranked by score) and skipping
any that are too similar to an already-selected event.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fiam.store.formats import EventRecord


def _load_embedding(event: EventRecord, embeddings_dir: Path) -> np.ndarray | None:
    """Load the .npy embedding for *event*.  Returns None on failure."""
    if not event.embedding:
        return None
    npy_path = embeddings_dir.parent / event.embedding  # relative to store/
    if not npy_path.exists():
        return None
    return np.load(npy_path).astype(np.float32).flatten()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def diversify(
    candidates: list[EventRecord],
    embeddings_dir: Path,
    *,
    top_k: int = 5,
    similarity_threshold: float = 0.88,
) -> list[EventRecord]:
    """Select up to *top_k* events with pairwise cosine < *similarity_threshold*.

    *candidates* must already be ordered by descending score.
    Events whose .npy cannot be loaded are included without similarity checks.
    """
    if len(candidates) <= top_k:
        return candidates

    selected: list[EventRecord] = []
    selected_vecs: list[np.ndarray] = []

    for cand in candidates:
        if len(selected) >= top_k:
            break

        vec = _load_embedding(cand, embeddings_dir)

        # If no embedding available, accept unconditionally
        if vec is None:
            selected.append(cand)
            continue

        # Check against all already-selected embeddings
        redundant = False
        for sv in selected_vecs:
            if _cosine(vec, sv) > similarity_threshold:
                redundant = True
                break

        if not redundant:
            selected.append(cand)
            selected_vecs.append(vec)

    return selected
