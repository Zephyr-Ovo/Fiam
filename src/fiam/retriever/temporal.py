"""
Temporal co-occurrence linker.

Events that occur within a configurable time window (default 4 hours)
are linked via the `links` field. This captures causal chains like
淋雨→发烧 that semantic similarity alone would miss.

Used in two places:
  - post_session: link_new_events() adds links when new events are stored
  - joint retriever: temporal_boost() gives linked events a retrieval bonus
"""

from __future__ import annotations

from datetime import timedelta

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord


def link_new_events(
    new_events: list[EventRecord],
    all_events: list[EventRecord],
    config: FiamConfig,
) -> list[EventRecord]:
    """Add temporal links between new events and existing events within the window.

    Mutates the `links` field of *new_events* (and any existing events
    that fall within the window). Returns the list of existing events
    whose links were modified (caller should persist them).
    """
    window = timedelta(hours=config.temporal_window_hours)
    modified_existing: list[EventRecord] = []

    for new_ev in new_events:
        for existing in all_events:
            if existing.event_id == new_ev.event_id:
                continue
            delta = abs(new_ev.time - existing.time)
            if delta <= window:
                # Bidirectional link
                if existing.event_id not in new_ev.links:
                    new_ev.links.append(existing.event_id)
                if new_ev.event_id not in existing.links:
                    existing.links.append(new_ev.event_id)
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

    overlap = sum(1 for link_id in event.links if link_id in scored_ids)
    # Diminishing returns: 1 link → 0.5, 2 → 0.75, 3+ → ~0.88
    return min(1.0, 1.0 - 1.0 / (1.0 + overlap)) if overlap > 0 else 0.0
