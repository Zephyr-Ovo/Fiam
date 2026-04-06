"""
Relevance scorer — delegates to joint retriever.

Kept for backward compatibility. Real scoring logic lives in
decay.py (retention/strength) and joint.py (composite scoring).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fiam.retriever.decay import compute_retention

if TYPE_CHECKING:
    from fiam.store.formats import EventRecord


def score(event: EventRecord, current_context: str) -> float:
    """Score relevance of *event* based on retention curve.

    For full composite scoring (semantic + emotion + decay + unresolved),
    use joint.search() instead.
    """
    now = datetime.now(timezone.utc)
    return compute_retention(event, now)
