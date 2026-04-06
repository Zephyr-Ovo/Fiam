"""
Joint retriever \u2014 semantic + retention + temporal linking.

Scoring formula per event:
  score = w_sem  * cosine(query_vec, event_vec)
        + w_rec  * retention(event)              # Ebbinghaus curve
        + w_link * temporal_bonus(event)          # co-occurrence boost

After scoring: diversity penalty → greedy embedding dedup → access record update.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from fiam.config import FiamConfig
from fiam.retriever.decay import compute_retention, diversity_penalty, record_access
from fiam.retriever.diversity import diversify
from fiam.retriever.embedder import Embedder
from fiam.retriever.temporal import temporal_boost
from fiam.store.formats import EventRecord
from fiam.store.home import HomeStore


def search(
    conversation_text: str,
    store: HomeStore,
    config: FiamConfig,
    *,
    top_k: int | None = None,
) -> list[EventRecord]:
    """Retrieve the most relevant events for the upcoming conversation.

    If *conversation_text* is empty (cold start), ranking uses only
    recency weight (no semantic component).
    """
    now = datetime.now(timezone.utc)
    all_events = store.all_events()

    if not all_events:
        return []

    effective_top_k = top_k if top_k is not None else config.top_k

    # Filter out events that are too recent (avoid overlap with CC context)
    min_age = timedelta(hours=config.min_event_age_hours)
    eligible = [e for e in all_events if (now - e.time) > min_age]

    if not eligible:
        return []

    # --- Prepare query vectors (only if conversation_text is non-empty) ---
    query_vec: np.ndarray | None = None
    if conversation_text.strip():
        embedder = Embedder(config)
        query_vec = embedder.embed(conversation_text)

    # --- Score each event ---
    w_sem = config.semantic_weight
    w_rec = config.recency_weight
    w_link = config.temporal_link_weight

    # If no query text, redistribute semantic weight to recency
    if query_vec is None:
        w_rec = w_rec + w_sem
        w_sem = 0.0

    # --- First pass: score without temporal links ---
    first_pass: list[tuple[EventRecord, float]] = []

    for event in eligible:
        # Semantic similarity
        sem_score = 0.0
        if query_vec is not None and event.embedding:
            event_vec = _load_vec(event, config)
            if event_vec is not None:
                sem_score = _cosine(query_vec, event_vec)

        # Retention (Ebbinghaus decay)
        retention = compute_retention(event, now, half_life_base=config.half_life_base)

        base = w_sem * sem_score + w_rec * retention

        # Apply diversity penalty for recently-accessed events
        penalty = diversity_penalty(event, now, recent_days=3)
        first_pass.append((event, base * penalty))

    # --- Second pass: temporal link boost ---
    # Top candidates from first pass seed the link bonus
    first_pass.sort(key=lambda t: t[1], reverse=True)
    seed_ids = {e.event_id for e, _ in first_pass[:effective_top_k * 2]}

    scored: list[tuple[EventRecord, float]] = []
    for event, base_score in first_pass:
        link_bonus = temporal_boost(event, seed_ids)
        final = base_score + w_link * link_bonus
        scored.append((event, final))

    # Sort descending by score
    scored.sort(key=lambda t: t[1], reverse=True)

    # Coarse cut before expensive diversity filter
    coarse_k = effective_top_k * 4
    top_candidates = [e for e, _ in scored[:coarse_k]]

    # Embedding-based diversity filter
    selected = diversify(
        top_candidates,
        config.embeddings_dir,
        top_k=effective_top_k,
        similarity_threshold=config.diversity_threshold,
    )

    # Record access for selected events (strength boost + persist)
    for event in selected:
        record_access(event, now)
        store.update_metadata(event)

    if config.debug_mode:
        print(f"[joint] {len(all_events)} total → {len(eligible)} eligible "
              f"→ {len(selected)} selected (top_k={effective_top_k})")
        for ev in selected:
            print(f"  {ev.filename}  str={ev.strength:.2f}  "
                  f"acc={ev.access_count}  a={ev.arousal:.2f}")

    return selected


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _load_vec(event: EventRecord, config: FiamConfig) -> np.ndarray | None:
    """Load the .npy embedding for *event*.

    Returns None if the file doesn't exist or the dimension doesn't match
    the current embedding_dim (legacy 384-dim events gracefully degrade).
    """
    if not event.embedding:
        return None
    npy_path = config.embeddings_dir.parent / event.embedding
    if not npy_path.exists():
        return None
    vec = np.load(npy_path).astype(np.float32).flatten()
    if vec.shape[0] != config.embedding_dim:
        return None  # dimension mismatch → skip semantic score
    return vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
