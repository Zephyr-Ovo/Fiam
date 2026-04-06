"""
Memory decay and access reinforcement.

Implements Ebbinghaus-inspired forgetting curve and strength
boosting on recall. Used by joint retriever to rank events.

Core formula:
  retention R(t) = exp(-t / (S * 14))
  where t = days since event, S = strength [1.0, 3.0]

  strength=1.0 → half-life ≈ 10 days
  strength=2.0 → half-life ≈ 19 days
  strength=3.0 → half-life ≈ 29 days
"""

from __future__ import annotations

import math
from datetime import datetime

from fiam.store.formats import EventRecord


_HALF_LIFE_BASE = 14.0   # days — controls base decay speed
_STRENGTH_CAP = 3.0


def compute_retention(event: EventRecord, now: datetime, *, half_life_base: float = _HALF_LIFE_BASE) -> float:
    """Compute memory retention in [0, 1].

    Higher strength slows the decay curve.
    """
    days_since = max((now - event.time).total_seconds() / 86400, 0.0)
    return math.exp(-days_since / (event.strength * half_life_base))


def boost_strength(event: EventRecord, now: datetime) -> float:
    """Compute new strength after a recall.

    - Base boost scales with arousal (high-emotion events reinforce more).
    - Diminishing returns from repeated access.
    - Freshness bonus for events < 7 days old.
    - Capped at _STRENGTH_CAP.
    """
    # Base boost: 0.1 .. 0.3
    base = 0.1 + event.arousal * 0.2

    # Diminishing factor
    diminishing = 1.0 / (1.0 + event.access_count * 0.1)

    # Freshness bonus (events < 7 days get up to +0.2)
    days_since = max((now - event.time).total_seconds() / 86400, 0.0)
    freshness = max(0.0, (7.0 - days_since) / 7.0) * 0.2

    return min(event.strength + (base + freshness) * diminishing, _STRENGTH_CAP)


def record_access(event: EventRecord, now: datetime) -> None:
    """Update *event* in-place after it has been recalled."""
    event.strength = boost_strength(event, now)
    event.last_accessed = now
    event.access_count += 1


def diversity_penalty(event: EventRecord, now: datetime, *, recent_days: int = 3) -> float:
    """Return a multiplier in (0, 1] penalising recently accessed events.

    Events not accessed within *recent_days* get 1.0 (no penalty).
    Events accessed very recently get 0.7.
    """
    if event.last_accessed is None:
        return 1.0
    days_since = (now - event.last_accessed).total_seconds() / 86400
    if days_since >= recent_days:
        return 1.0
    # Linear ramp: 0.7 at day 0 → 1.0 at recent_days
    return 0.7 + 0.3 * (days_since / recent_days)
