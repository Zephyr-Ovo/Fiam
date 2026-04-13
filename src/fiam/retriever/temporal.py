"""
Temporal co-occurrence linker.

Events that share a session (consecutive gap < 30 min) are linked
via store/graph.jsonl with type "temporal".  This captures causal
chains like 淋雨→发烧 that semantic similarity alone would miss.

Used in two places:
  - post_session: link_new_events() creates edges when new events are stored
  - joint retriever: temporal_boost() gives linked events a retrieval bonus
"""

from __future__ import annotations

from datetime import timedelta

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from fiam.store.graph_store import Edge, GraphStore

# Gap between two events that counts as "same session"
_SESSION_GAP = timedelta(minutes=30)


def link_new_events(
    new_events: list[EventRecord],
    all_events: list[EventRecord],
    config: FiamConfig,
) -> list[Edge]:
    """Create temporal edges between new events and existing events in the same session.

    Two events are considered same-session if their timestamps differ by
    less than 30 minutes.  Edges are bidirectional with type "temporal"
    and weight based on gap closeness: w = 1 - gap/30min.

    Returns list of new Edge objects (caller writes them to GraphStore).
    """
    graph_store = GraphStore(config.graph_jsonl_path)
    new_edges: list[Edge] = []

    for new_ev in new_events:
        for existing in all_events:
            if existing.event_id == new_ev.event_id:
                continue
            delta = abs(new_ev.time - existing.time)
            if delta <= _SESSION_GAP:
                weight = round(1.0 - delta.total_seconds() / _SESSION_GAP.total_seconds(), 4)
                weight = max(weight, 0.1)  # floor

                # Bidirectional: only add if not already present
                if not graph_store.has_edge(new_ev.event_id, existing.event_id):
                    new_edges.append(Edge(
                        src=new_ev.event_id,
                        dst=existing.event_id,
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
