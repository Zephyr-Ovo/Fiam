"""
Silent training data collector for future personalized retrieval model.

Collects two types of signals into store/training.jsonl:

1. **Explicit cohort feedback** — from `fiam feedback` TUI.
   Instead of 1-to-1 ratings, we record the whole "cohort" of events presented
   to the user at once, plus which ones they positively or negatively voted on.
   Format: { ts, type="explicit_cohort", trigger_context, candidates: [{event_id, label, features...}] }

2. **Implicit recall signal** — from daemon recall cycle.
   When events are recalled and the user continues the topic (cosine > drift threshold),
   those events get label=1. Events recalled but ignored get label=0.

This data builds a rich, context-aware dataset for future Learning-to-Rank or
Graph Neural Network adaptation without needing a massive dense N x N matrix in real-time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _training_path(code_path: Path) -> Path:
    return code_path / "store" / "training.jsonl"


def log_feedback_cohort(
    code_path: Path,
    trigger_context: str,
    candidates: list[dict],
) -> None:
    """Append an explicit feedback signal for a cohort of events.
    
    Reflects the reality of retrieval: a user makes relative choices
    among a presented group of candidate events (+1, -1, or 0) rather
    than evaluating them strictly in isolation.
    """
    _append(code_path, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "explicit_cohort",
        "trigger_context": trigger_context[:500],
        "candidates": candidates
    })


def log_implicit_recall(
    code_path: Path,
    event_id: str,
    label: int,
    context: str = "",
    *,
    event_arousal: float = 0.0,
    event_valence: float = 0.0,
    event_age_hours: float = 0.0,
    user_weight: float = 1.0,
) -> None:
    """Append an implicit recall signal (from daemon recall cycle)."""
    _append(code_path, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "implicit",
        "event_id": event_id,
        "label": label,
        "context": context[:500],  # truncate to save space
        "event_arousal": round(event_arousal, 4),
        "event_valence": round(event_valence, 4),
        "event_age_hours": round(event_age_hours, 2),
        "user_weight": round(user_weight, 4),
    })


def _append(code_path: Path, row: dict) -> None:
    path = _training_path(code_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def count_samples(code_path: Path) -> int:
    """Return the number of training samples collected so far."""
    path = _training_path(code_path)
    if not path.exists():
        return 0
    return sum(1 for _ in open(path, encoding="utf-8"))
