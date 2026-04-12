"""
One-shot script: process all existing events through the new graph logic.

Read-only against event files on disk. Outputs:
  1. Console stats (links found, graph structure)
  2. logs/graph_debug.html — interactive visualization
  3. logs/graph_snapshot.json — machine-readable snapshot
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
from fiam.config import FiamConfig
from fiam.store.home import HomeStore
from fiam.retriever.graph import MemoryGraph
from fiam.retriever.graph_viz import render_html


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
    print()

    # --- Existing link stats (after normalisation) ---
    old_link_count = sum(len(e.links) for e in events)
    print(f"Existing links (migrated to dict format): {old_link_count}")

    # --- Compute semantic similarities (all pairs, read-only) ---
    vecs: dict[str, np.ndarray] = {}
    for e in events:
        v = _load_vec(e, config)
        if v is not None:
            vecs[e.event_id] = v

    print(f"Events with embeddings: {len(vecs)}/{len(events)}")

    # Find semantic pairs above threshold
    SEM_THRESHOLD = 0.75
    sem_pairs: list[tuple[str, str, float]] = []
    event_ids = sorted(vecs.keys())
    for i, a_id in enumerate(event_ids):
        for b_id in event_ids[i + 1:]:
            sim = _cosine(vecs[a_id], vecs[b_id])
            if sim >= SEM_THRESHOLD:
                sem_pairs.append((a_id, b_id, sim))

    print(f"\nSemantic pairs (cosine >= {SEM_THRESHOLD}): {len(sem_pairs)}")
    if sem_pairs:
        sem_pairs.sort(key=lambda t: t[2], reverse=True)
        print("  Top 10:")
        for a, b, s in sem_pairs[:10]:
            print(f"    {a} ↔ {b}  cos={s:.4f}")

    # --- Build enriched links IN MEMORY (not writing to disk) ---
    ev_map = {e.event_id: e for e in events}

    # Add semantic links to in-memory copies
    for a_id, b_id, sim in sem_pairs:
        w = round(sim, 4)
        ea, eb = ev_map[a_id], ev_map[b_id]
        linked_a = {l["id"] for l in ea.links if isinstance(l, dict)}
        linked_b = {l["id"] for l in eb.links if isinstance(l, dict)}
        if b_id not in linked_a:
            ea.links.append({"id": b_id, "type": "semantic", "weight": w})
        if a_id not in linked_b:
            eb.links.append({"id": a_id, "type": "semantic", "weight": w})

    new_link_count = sum(len(e.links) for e in events)
    print(f"\nTotal links after semantic enrichment: {new_link_count} "
          f"(+{new_link_count - old_link_count} semantic)")

    # --- Build MemoryGraph (ALL links, legacy) ---
    graph = MemoryGraph()
    graph.build(events, now=now)
    print(f"\nGraph (all links): {graph.node_count} nodes, {graph.edge_count} edges")

    type_counts: dict[str, int] = {}
    for _, _, data in graph.G.edges(data=True):
        t = data.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c} edges")

    # --- Build a CLEAN graph: strip legacy temporal, keep only semantic ---
    print("\n" + "=" * 60)
    print("CLEAN VIEW: semantic links only (what new sessions will look like)")
    print("=" * 60)

    for e in events:
        e.links = [l for l in e.links if isinstance(l, dict) and l.get("type") == "semantic"]
    # Re-add semantic pairs that were blocked by existing temporal links
    for a_id, b_id, sim in sem_pairs:
        w = round(sim, 4)
        ea, eb = ev_map[a_id], ev_map[b_id]
        linked_a = {l["id"] for l in ea.links}
        linked_b = {l["id"] for l in eb.links}
        if b_id not in linked_a:
            ea.links.append({"id": b_id, "type": "semantic", "weight": w})
        if a_id not in linked_b:
            eb.links.append({"id": a_id, "type": "semantic", "weight": w})

    sem_link_count = sum(len(e.links) for e in events)
    print(f"Semantic-only links: {sem_link_count}")

    clean_graph = MemoryGraph()
    clean_graph.build(events, now=now)
    print(f"Clean graph: {clean_graph.node_count} nodes, {clean_graph.edge_count} edges")

    # --- Test spreading activation on CLEAN graph ---
    seed = events[0]
    print(f"\nSpreading activation from {seed.event_id} (clean graph):")
    activation = clean_graph.spread([seed.event_id], [1.0])
    top_activated = sorted(activation.items(), key=lambda t: t[1], reverse=True)[:10]
    for eid, score in top_activated:
        print(f"  {eid}: {score:.4f}")

    # Also test multi-seed (simulate retrieval: top 3 by some arbitrary score)
    seed_ids = [events[0].event_id, events[20].event_id, events[50].event_id]
    seed_scores = [1.0, 0.8, 0.6]
    print(f"\nMulti-seed spreading ({seed_ids}):")
    activation2 = clean_graph.spread(seed_ids, seed_scores)
    top2 = sorted(activation2.items(), key=lambda t: t[1], reverse=True)[:10]
    for eid, score in top2:
        print(f"  {eid}: {score:.4f}")

    # --- Dump HTML viz (clean graph) ---
    viz_path = config.logs_dir / "graph_debug.html"
    viz_path.parent.mkdir(parents=True, exist_ok=True)
    render_html(clean_graph, viz_path)
    print(f"\nVisualization: {viz_path}")

    # --- Dump JSON snapshot ---
    snap_path = config.logs_dir / "graph_snapshot.json"
    snap = graph.to_debug_dict()
    snap["stats"] = {
        "events": len(events),
        "edges": graph.edge_count,
        "semantic_pairs": len(sem_pairs),
        "edge_types": type_counts,
    }
    snap_path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    print(f"Snapshot: {snap_path}")


if __name__ == "__main__":
    main()
