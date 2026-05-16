"""AI life state: what the AI is doing with its own time.

A small, additive model — *not* a scheduler and *not* a growth/leveling
system. It records:

- ``presence``  — coarse availability (available / busy / resting / away / asleep)
- ``activity``  — the single current activity (kind + optional target/summary)
- an append-only ``activity.jsonl`` log of completed activities and discrete
  events, for the future "AI's room" and activity-timeline surfaces and for
  participation/supervision transparency.

Persisted under ``<home>/self/`` (``config.self_dir``) so it sits next to the
AI's other private state (journal, schedule, todo). Atomic writes mirror
``fiam_lib.stroll_state`` conventions so dashboard/daemon restarts never lose
or corrupt the current state.

This module deliberately does not decide *when* the AI acts. It only records
*what* it is doing. The autonomous initiative loop (if/when wired) is a
separate, gated component that calls into here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig

# Coarse presence the ambient UI can render ("she's around" feeling).
PRESENCE = {"available", "busy", "resting", "away", "asleep"}
DEFAULT_PRESENCE = "available"

# Constrained activity vocabulary. Extensible, but a closed-ish set keeps the
# timeline renderable and avoids free-text drift. "idle" is the resting default.
ACTIVITY_KINDS = {
    "idle",
    "with_zephyr",   # chatting / doing something together
    "browsing",      # roaming the web on its own
    "watching",      # video
    "reading",       # books / articles / vault
    "organizing",    # tidying files
    "writing",       # journal / notes / drafts
    "thinking",      # reflecting, no external action
    "away",          # deliberately not present
}
DEFAULT_ACTIVITY_KIND = "idle"

# Cap the on-disk log so it stays cheap to tail. Older lines are dropped on
# append once the file exceeds this many lines.
_MAX_LOG_LINES = 2000


def _now() -> float:
    return time.time()


def life_state_path(config: FiamConfig) -> Path:
    return config.self_dir / "life_state.json"


def activity_log_path(config: FiamConfig) -> Path:
    return config.self_dir / "activity.jsonl"


def _default_state() -> dict[str, Any]:
    now = _now()
    return {
        "presence": DEFAULT_PRESENCE,
        "activity": {"kind": DEFAULT_ACTIVITY_KIND, "since": now},
        "updated_at": now,
    }


def _read(config: FiamConfig) -> dict[str, Any]:
    p = life_state_path(config)
    if not p.exists():
        return _default_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()
    if not isinstance(data, dict) or "activity" not in data:
        return _default_state()
    return data


def _write(config: FiamConfig, data: dict[str, Any]) -> None:
    p = life_state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _append_log(config: FiamConfig, row: dict[str, Any]) -> None:
    p = activity_log_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    _truncate_log(p)


def _truncate_log(p: Path) -> None:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_LOG_LINES:
        return
    keep = lines[-_MAX_LOG_LINES:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(p)


def _clean_text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def _coerce_kind(value: Any) -> str:
    kind = str(value or "").strip()
    return kind if kind in ACTIVITY_KINDS else DEFAULT_ACTIVITY_KIND


def get_state(config: FiamConfig) -> dict[str, Any]:
    """Return the current life state (never raises; falls back to default)."""
    return _read(config)


def set_presence(
    config: FiamConfig, presence: str, *, note: str = ""
) -> dict[str, Any]:
    """Update coarse presence. Unknown values fall back to the default."""
    data = _read(config)
    p = str(presence or "").strip()
    data["presence"] = p if p in PRESENCE else DEFAULT_PRESENCE
    note_txt = _clean_text(note)
    if note_txt:
        data["presence_note"] = note_txt
    else:
        data.pop("presence_note", None)
    data["updated_at"] = _now()
    _write(config, data)
    return data


def set_activity(
    config: FiamConfig,
    kind: str,
    *,
    summary: str = "",
    target: str = "",
    surface: str = "",
) -> dict[str, Any]:
    """Switch the current activity.

    The previously running activity is closed out and appended to
    ``activity.jsonl`` with its start/end span so the timeline can render
    completed spans without the UI having to diff snapshots.
    """
    data = _read(config)
    now = _now()
    prev = data.get("activity") if isinstance(data.get("activity"), dict) else None

    new_kind = _coerce_kind(kind)
    if prev and prev.get("kind") and prev.get("kind") != new_kind:
        started = float(prev.get("since") or now)
        _append_log(
            config,
            {
                "type": "activity_span",
                "kind": prev.get("kind"),
                "summary": _clean_text(prev.get("summary")),
                "target": _clean_text(prev.get("target"), 160),
                "surface": _clean_text(prev.get("surface"), 60),
                "started_at": started,
                "ended_at": now,
                "duration_s": round(max(0.0, now - started), 1),
            },
        )

    activity: dict[str, Any] = {"kind": new_kind, "since": now}
    summary_txt = _clean_text(summary)
    if summary_txt:
        activity["summary"] = summary_txt
    target_txt = _clean_text(target, 160)
    if target_txt:
        activity["target"] = target_txt
    surface_txt = _clean_text(surface, 60)
    if surface_txt:
        activity["surface"] = surface_txt

    data["activity"] = activity
    data["updated_at"] = now
    _write(config, data)
    return data


def note_event(
    config: FiamConfig, kind: str, summary: str, **meta: Any
) -> dict[str, Any]:
    """Record a discrete event without changing the current activity.

    Use for point-in-time happenings the timeline / supervision view should
    show — e.g. "organized 12 files", "posted a comment", "left a note for
    Zephyr". ``meta`` is stored as-is (keep it JSON-serializable and small).
    """
    row: dict[str, Any] = {
        "type": "event",
        "kind": _clean_text(kind, 60) or "event",
        "summary": _clean_text(summary),
        "at": _now(),
    }
    if meta:
        try:
            json.dumps(meta)
            row["meta"] = meta
        except (TypeError, ValueError):
            pass
    _append_log(config, row)
    return row


def recent_activity(config: FiamConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent log rows (newest last), capped at ``limit``."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(n, _MAX_LOG_LINES))
    p = activity_log_path(config)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
