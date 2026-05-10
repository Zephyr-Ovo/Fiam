"""Stroll runtime state: a single-active-walk flag with location + tick.

Persisted at ``<home>/stroll/state.json`` (where ``<home>`` is
``config.home_path``) so dashboard restarts don't
lose an in-flight walk. Only one walk active at a time. Heartbeat must
refresh within ``HEARTBEAT_TIMEOUT_S`` or the walk auto-ends on read.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig
from fiam_lib.stroll_store import stroll_dir

HEARTBEAT_TIMEOUT_S = 90
DEFAULT_TICK_INTERVAL_S = 60
MIN_TICK_INTERVAL_S = 20
MAX_TICK_INTERVAL_S = 600


def state_path(config: FiamConfig) -> Path:
    return stroll_dir(config) / "state.json"


def _now() -> float:
    return time.time()


def _read(config: FiamConfig) -> dict[str, Any]:
    p = state_path(config)
    if not p.exists():
        return {"active": False}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {"active": False}
    except (json.JSONDecodeError, OSError):
        return {"active": False}


def _write(config: FiamConfig, data: dict[str, Any]) -> None:
    p = state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _clamp_interval(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_TICK_INTERVAL_S
    if n < MIN_TICK_INTERVAL_S:
        n = MIN_TICK_INTERVAL_S
    if n > MAX_TICK_INTERVAL_S:
        n = MAX_TICK_INTERVAL_S
    return n


def _sanitize_location(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        lat = float(value.get("lat"))
        lng = float(value.get("lng"))
    except (TypeError, ValueError):
        return None
    out = {"lat": lat, "lng": lng}
    acc = value.get("accuracy")
    if acc is not None:
        try:
            out["accuracy"] = float(acc)
        except (TypeError, ValueError):
            pass
    return out


def get_state(config: FiamConfig) -> dict[str, Any]:
    """Return current state, auto-deactivating on stale heartbeat."""
    data = _read(config)
    if not data.get("active"):
        return data
    last_hb = float(data.get("last_heartbeat") or 0)
    if _now() - last_hb > HEARTBEAT_TIMEOUT_S:
        data["active"] = False
        data["ended_at"] = _now()
        data["end_reason"] = "heartbeat_timeout"
        _write(config, data)
    return data


def start(config: FiamConfig, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    data: dict[str, Any] = {
        "active": True,
        "started_at": now,
        "last_heartbeat": now,
        "last_tick_at": 0.0,
        "interval_seconds": _clamp_interval(payload.get("interval_seconds")),
    }
    loc = _sanitize_location(payload.get("location"))
    if loc:
        data["location"] = loc
    limen_url = str(payload.get("limen_url") or "").strip()
    if limen_url:
        data["limen_url"] = limen_url
    _write(config, data)
    return data


def heartbeat(config: FiamConfig, payload: dict[str, Any]) -> dict[str, Any]:
    data = _read(config)
    if not data.get("active"):
        # Auto-revive if the phone keeps pinging — treat as implicit start.
        return start(config, payload)
    data["last_heartbeat"] = _now()
    loc = _sanitize_location(payload.get("location"))
    if loc:
        data["location"] = loc
    if payload.get("interval_seconds") is not None:
        data["interval_seconds"] = _clamp_interval(payload.get("interval_seconds"))
    limen_url = payload.get("limen_url")
    if limen_url is not None:
        s = str(limen_url).strip()
        if s:
            data["limen_url"] = s
        else:
            data.pop("limen_url", None)
    _write(config, data)
    return data


def stop(config: FiamConfig, reason: str = "user_stop") -> dict[str, Any]:
    data = _read(config)
    data["active"] = False
    data["ended_at"] = _now()
    data["end_reason"] = reason
    _write(config, data)
    return data


def mark_tick(config: FiamConfig, at: float | None = None) -> None:
    """Stamp last_tick_at after a successful tick. No-op if inactive."""
    data = _read(config)
    if not data.get("active"):
        return
    data["last_tick_at"] = float(at if at is not None else _now())
    _write(config, data)
