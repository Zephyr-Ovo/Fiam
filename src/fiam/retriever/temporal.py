"""
Temporal adjacency linker.

Adjacent events within 10 minutes get a temporal edge.
Weight = 1.0 - gap_seconds / 600, clamped to [0.1, 1.0].
Gap > 10 min → no edge.

Independent of semantic / LLM edges.
"""

from __future__ import annotations

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from fiam.store.graph_store import Edge, GraphStore

# Maximum gap (seconds) for a temporal edge between adjacent events
_MAX_GAP_SECONDS = 600  # 10 minutes


def link_new_events(
    new_events: list[EventRecord],
    all_events: list[EventRecord],
    config: FiamConfig,
) -> list[Edge]:
    """Create temporal edges between each new event and its chronological neighbours.

    Only the immediately adjacent events (previous / next in time) are
    considered.  If the gap exceeds 10 minutes, no edge is created.
    Weight decays linearly: w = 1.0 − gap/600 s, floor 0.1.

    Returns list of new Edge objects (caller writes them to GraphStore).
    """
    if not new_events or not all_events:
        return []

    graph_store = GraphStore(config.graph_jsonl_path)

    # Sort all events chronologically once
    sorted_all = sorted(all_events, key=lambda e: e.time)
    id_to_pos = {e.event_id: i for i, e in enumerate(sorted_all)}

    new_edges: list[Edge] = []

    for new_ev in new_events:
        pos = id_to_pos.get(new_ev.event_id)
        if pos is None:
            continue

        # Check left and right neighbours only
        for neighbour_pos in (pos - 1, pos + 1):
            if neighbour_pos < 0 or neighbour_pos >= len(sorted_all):
                continue

            neighbour = sorted_all[neighbour_pos]
            gap = abs((new_ev.time - neighbour.time).total_seconds())

            if gap > _MAX_GAP_SECONDS:
                continue

            weight = round(1.0 - gap / _MAX_GAP_SECONDS, 4)
            weight = max(weight, 0.1)

            if not graph_store.has_edge(new_ev.event_id, neighbour.event_id):
                new_edges.append(Edge(
                    src=new_ev.event_id,
                    dst=neighbour.event_id,
                    type="temporal",
                    weight=weight,
                ))

    return new_edges


def temporal_boost(
    event_id: str,
    scored_ids: set[str],
    graph_store: GraphStore,
) -> float:
    """Compute temporal link bonus for an event during retrieval.

    Returns a value in [0.0, 1.0]. Events that share links with other
    high-scoring candidates get a boost — if a linked event is already
    in the retrieved set, this event gets pulled along.
    """
    edges = graph_store.edges_for(event_id)
    if not edges:
        return 0.0

    overlap = sum(
        e.weight
        for e in edges
        if (e.src if e.dst == event_id else e.dst) in scored_ids
    )
    return min(1.0, overlap) if overlap > 0 else 0.0
