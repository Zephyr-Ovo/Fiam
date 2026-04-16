"""
Trajectory recorder — logs (state, action, reward, state') tuples for
future Offline RL training.

Format: one JSONL file per day in ``store/trajectories/YYYY-MM-DD.jsonl``.
Each line is a single transition produced by a post_session / store_segment
run. The format intentionally mirrors what HuggingFace ``trl`` and
standard Offline RL datasets expect, so no conversion is needed later.

We store references (event_ids, content hashes) rather than raw content —
the original data lives in ``store/events/`` and can be rehydrated.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord


def _parse_state_md(path: Path) -> dict[str, Any]:
    """Parse mood + tension from a state.md file. Returns {} if absent."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    result: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("mood:"):
            result["mood"] = line.split(":", 1)[1].strip()
        elif line.startswith("tension:"):
            try:
                result["tension"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return result


def record_transition(
    config: FiamConfig,
    *,
    state_before: dict[str, Any],
    events: list[EventRecord],
    signals: dict[str, Any],
    trigger: str,
) -> None:
    """Append one transition to today's trajectory JSONL.

    Args:
        config: Active FiamConfig.
        state_before: Snapshot of mood/tension/etc before post_session ran.
        events: Events produced this turn (may be empty).
        signals: Session signal dict (SessionSignals.to_dict()).
        trigger: Why post_session ran ("scan" | "idle" | "segment" | ...).
    """
    now = datetime.now(timezone.utc)
    traj_dir = config.store_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    path = traj_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"

    # state_after: re-read state.md (appraisal may have updated it)
    state_after = _parse_state_md(config.state_path)

    # Reward signals (multiple channels, can be combined later)
    max_novelty = 0.0
    mean_intensity = 0.0
    if events:
        mean_intensity = sum(e.intensity for e in events) / len(events)
        # significance was computed during extraction but not stored on the
        # EventRecord — we approximate novelty from intensity here
        max_novelty = max(e.intensity for e in events)

    tension_delta = 0.0
    if "tension" in state_before and "tension" in state_after:
        tension_delta = state_after["tension"] - state_before["tension"]

    transition = {
        "timestamp": now.isoformat(),
        "trigger": trigger,
        "state_before": state_before,
        "action": {
            "type": "store_events" if events else "silent",
            "event_ids": [e.event_id for e in events],
            "event_count": len(events),
        },
        "reward_signals": {
            "intrinsic_novelty": round(max_novelty, 4),
            "mean_intensity": round(mean_intensity, 4),
            "tension_delta": round(tension_delta, 4),
            "volatility": signals.get("volatility", 0.0),
            "temperature_gap": signals.get("temperature_gap", 0.0),
        },
        "state_after": state_after,
    }

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(transition, ensure_ascii=False) + "\n")


def snapshot_state_before(config: FiamConfig) -> dict[str, Any]:
    """Capture state.md snapshot + metadata before a session runs."""
    state = _parse_state_md(config.state_path)
    goals_hash = ""
    if config.goals_path.exists():
        goals_hash = hashlib.sha256(
            config.goals_path.read_bytes()).hexdigest()[:16]
    return {
        "mood": state.get("mood", ""),
        "tension": state.get("tension", 0.5),
        "goals_hash": goals_hash,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
