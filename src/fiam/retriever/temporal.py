"""
Temporal co-occurrence linker.

Events that share a session (consecutive gap < 30 min) are linked
via the `links` field with type "temporal".  This captures causal
chains like 淋雨→发烧 that semantic similarity alone would miss.

Used in two places:
  - post_session: link_new_events() adds links when new events are stored
  - joint retriever: temporal_boost() gives linked events a retrieval bonus
"""

from __future__ import annotations

from datetime import timedelta

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord

# Gap between two events that counts as "same session"
_SESSION_GAP = timedelta(minutes=30)


def _linked_ids(event: EventRecord) -> set[str]:
    """Return set of event IDs already linked from *event*."""
    return {link["id"] for link in event.links if isinstance(link, dict)}


def link_new_events(
    new_events: list[EventRecord],
    all_events: list[EventRecord],
    config: FiamConfig,
) -> list[EventRecord]:
    """Add temporal links between new events and existing events in the same session.

    Two events are considered same-session if their timestamps differ by
    less than 30 minutes.  Links are bidirectional with type "temporal"
    and weight based on gap closeness: w = 1 - gap/30min.

    Mutates the `links` field of *new_events* (and any existing events
    that fall within the window). Returns existing events whose links
    were modified (caller should persist them).
    """
    modified_existing: list[EventRecord] = []

    for new_ev in new_events:
        for existing in all_events:
            if existing.event_id == new_ev.event_id:
                continue
            delta = abs(new_ev.time - existing.time)
            if delta <= _SESSION_GAP:
                weight = round(1.0 - delta.total_seconds() / _SESSION_GAP.total_seconds(), 4)
                weight = max(weight, 0.1)  # floor

                # Bidirectional link
                if existing.event_id not in _linked_ids(new_ev):
                    new_ev.links.append({
                        "id": existing.event_id,
                        "type": "temporal",
                        "weight": weight,
                    })
                if new_ev.event_id not in _linked_ids(existing):
                    existing.links.append({
                        "id": new_ev.event_id,
                        "type": "temporal",
                        "weight": weight,
                    })
                    if existing not in modified_existing:
                        modified_existing.append(existing)

    return modified_existing


def temporal_boost(
    event: EventRecord,
    scored_ids: set[str],
) -> float:
    """Compute temporal link bonus for an event during retrieval.

    Returns a value in [0.0, 1.0]. Events that share links with other
    high-scoring candidates get a boost — if a linked event is already
    in the retrieved set, this event gets pulled along.
    """
    if not event.links:
        return 0.0

    overlap = sum(
        link.get("weight", 0.5)
        for link in event.links
        if isinstance(link, dict) and link.get("id") in scored_ids
    )
    # Diminishing returns: sigmoid-like clamping
    return min(1.0, overlap) if overlap > 0 else 0.0
