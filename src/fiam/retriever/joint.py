"""
Joint retriever — semantic + retention + graph activation + integrated MMR.

Scoring formula per event (4-factor):
  base  = w_sem   * cosine(query_vec, event_vec)
        + w_rec   * retention(event)              # Ebbinghaus curve
        + w_graph * graph_activation(event)       # spreading activation

Selection uses greedy MMR (Maximal Marginal Relevance):
  mmr(e) = λ * base(e)  −  (1−λ) * max_sim(e, selected)

This integrates diversity INTO the ranking rather than filtering afterward.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from fiam.config import FiamConfig
from fiam.retriever.decay import compute_retention, diversity_penalty, record_access
from fiam.retriever.embedder import Embedder
from fiam.retriever.graph import MemoryGraph
from fiam.store.formats import EventRecord
from fiam.store.home import HomeStore

# MMR trade-off: 1.0 = pure relevance, 0.0 = pure diversity
_MMR_LAMBDA = 0.7


def search(
    conversation_text: str,
    store: HomeStore,
    config: FiamConfig,
    *,
    top_k: int | None = None,
) -> list[EventRecord]:
    """Retrieve the most relevant events for the upcoming conversation.

    Uses integrated MMR: diversity is part of the selection loop,
    not a post-hoc filter. Each step picks the candidate that maximises
    λ·relevance − (1−λ)·max_similarity_to_already_selected.
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

    # --- Prepare query vector ---
    query_vec: np.ndarray | None = None
    if conversation_text.strip():
        embedder = Embedder(config)
        query_vec = embedder.embed(conversation_text)

    # --- Score each event ---
    w_sem = config.semantic_weight
    w_rec = config.recency_weight
    w_graph = config.temporal_link_weight  # reused weight slot for graph activation

    if query_vec is None:
        w_rec = w_rec + w_sem
        w_sem = 0.0

    # --- First pass: base scores (semantic + retention) + cache vectors ---
    candidates: list[tuple[EventRecord, float, np.ndarray | None]] = []

    for event in eligible:
        sem_score = 0.0
        event_vec = None
        if query_vec is not None and event.embedding:
            event_vec = _load_vec(event, config)
            if event_vec is not None:
                sem_score = _cosine(query_vec, event_vec)

        retention = compute_retention(event, now, half_life_base=config.half_life_base)
        base = w_sem * sem_score + w_rec * retention

        # Apply user feedback weight (persistent score multiplier)
        base *= event.user_weight

        penalty = diversity_penalty(event, now, recent_days=3)
        candidates.append((event, base * penalty, event_vec))

    # --- Second pass: graph-based spreading activation ---
    # Build graph from all events + graph.jsonl edges
    from fiam.store.graph_store import GraphStore
    graph = MemoryGraph()
    graph_store = GraphStore(config.graph_jsonl_path)
    graph.build(all_events, now=now, edges=graph_store.load_as_dicts())

    # Seed = top candidates by base score
    candidates.sort(key=lambda t: t[1], reverse=True)
    seed_n = min(effective_top_k * 2, len(candidates))
    seed_ids = [e.event_id for e, _, _ in candidates[:seed_n]]
    seed_scores = [s for _, s, _ in candidates[:seed_n]]

    # Spread activation through the graph
    activation = graph.spread(seed_ids, seed_scores) if graph.node_count > 0 else {}

    # Merge: base + graph activation
    scored: list[tuple[EventRecord, float, np.ndarray | None]] = []
    for event, base_score, vec in candidates:
        graph_score = activation.get(event.event_id, 0.0)
        final = base_score + w_graph * graph_score
        scored.append((event, final, vec))

    scored.sort(key=lambda t: t[1], reverse=True)

    # --- Floor gate: min_score is the primary recall control ---
    # Only events above min_score are eligible; top_k is a safety cap.
    min_score = config.min_score
    scored = [(e, s, v) for e, s, v in scored if s >= min_score]

    # --- Greedy MMR selection ---
    # Pool = all qualifying events; top_k caps the final count.
    pool = scored
    n_select = min(effective_top_k, len(pool)) if effective_top_k else len(pool)

    selected = _mmr_select(pool, n_select, _MMR_LAMBDA)

    # Record access for selected events
    for event in selected:
        record_access(event, now)
        store.update_metadata(event)

    if config.debug_mode:
        print(f"[joint] {len(all_events)} total → {len(eligible)} eligible "
              f"→ {len(selected)} selected (top_k={effective_top_k}, λ={_MMR_LAMBDA})")
        print(f"[joint] graph: {graph.node_count} nodes, {graph.edge_count} edges")
        for ev in selected:
            g_score = activation.get(ev.event_id, 0.0)
            print(f"  {ev.filename}  str={ev.strength:.2f}  "
                  f"acc={ev.access_count}  i={ev.intensity:.2f}  g={g_score:.3f}")
        # Dump interactive HTML graph for inspection
        try:
            from fiam.retriever.graph_viz import render_html
            viz_path = config.logs_dir / "graph_debug.html"
            render_html(graph, viz_path)
            print(f"[joint] graph viz → {viz_path}")
        except Exception as e:
            print(f"[joint] graph viz failed: {e}")

    return selected


# ------------------------------------------------------------------
# MMR: Maximal Marginal Relevance (integrated diversity)
# ------------------------------------------------------------------

def _mmr_select(
    pool: list[tuple[EventRecord, float, np.ndarray | None]],
    top_k: int,
    lam: float,
) -> list[EventRecord]:
    """Greedy MMR selection.

    At each step, pick the candidate maximising:
      mmr(c) = λ * norm_score(c) − (1−λ) * max_cos(c, selected)

    Candidates without embeddings are accepted with max_sim = 0.
    """
    if not pool:
        return []

    # Normalise scores to [0, 1] for fair λ trade-off
    max_score = max(s for _, s, _ in pool)
    min_score = min(s for _, s, _ in pool)
    score_range = max_score - min_score if max_score > min_score else 1.0

    remaining = list(range(len(pool)))
    selected_idx: list[int] = []
    selected_vecs: list[np.ndarray] = []

    for _ in range(min(top_k, len(pool))):
        best_i = -1
        best_mmr = -float("inf")

        for idx in remaining:
            event, score, vec = pool[idx]
            norm_score = (score - min_score) / score_range

            # Max similarity to already-selected
            max_sim = 0.0
            if vec is not None and selected_vecs:
                for sv in selected_vecs:
                    sim = _cosine(vec, sv)
                    if sim > max_sim:
                        max_sim = sim

            mmr_val = lam * norm_score - (1.0 - lam) * max_sim

            if mmr_val > best_mmr:
                best_mmr = mmr_val
                best_i = idx

        if best_i < 0:
            break

        selected_idx.append(best_i)
        remaining.remove(best_i)
        vec = pool[best_i][2]
        if vec is not None:
            selected_vecs.append(vec)

    return [pool[i][0] for i in selected_idx]


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
        return None
    return vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
