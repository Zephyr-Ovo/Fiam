"""
Debug dashboard server for fiam daemon.

Serves the dashboard HTML and provides data endpoints by reading
daemon state, pipeline log, recall.md, events, schedule, and cost.

Usage:
    python scripts/dashboard_server.py [--port 8766]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from io import BytesIO

logger = logging.getLogger(__name__)

# Resolve paths relative to fiam-code root
_ROOT = Path(__file__).resolve().parent.parent
_LOGS = _ROOT / "logs"
_CONFIG = None
_POOL = None        # Pool instance (lazy)
_EMBEDDER = None    # Embedder instance (lazy)
_BUS = None         # Bus instance (lazy, for /api/capture publishing)
_COMPUTE_LOCK = threading.Lock()  # gate concurrent mutations

# Fix sys.path: add src/, remove scripts/ (fiam.py shadows fiam package)
_src_dir = str(_ROOT / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_scripts_dir = str(_ROOT / "scripts")
if _scripts_dir in sys.path:
    sys.path.remove(_scripts_dir)


def _load_config():
    global _CONFIG, _POOL
    from fiam.config import FiamConfig
    toml_path = _ROOT / "fiam.toml"
    if toml_path.exists():
        _CONFIG = FiamConfig.from_toml(toml_path, _ROOT)
    # Init Pool
    if _CONFIG:
        from fiam.store.pool import Pool
        _POOL = Pool(_CONFIG.pool_dir)
        _POOL.ensure_dirs()


def _get_embedder():
    """Lazy-init embedder — may fail if torch not installed."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    if not _CONFIG:
        return None
    try:
        from fiam.retriever.embedder import Embedder
        _EMBEDDER = Embedder(_CONFIG)
        return _EMBEDDER
    except Exception as exc:
        logger.warning("embedder init failed (re-embed disabled): %s", exc)
        return None


def _get_bus():
    """Lazy-init MQTT bus client. Returns None if broker unreachable."""
    global _BUS
    if _BUS is not None:
        return _BUS
    if not _CONFIG:
        return None
    try:
        from fiam.bus import Bus
        _BUS = Bus(client_id="fiam-dashboard")
        _BUS.connect(_CONFIG.mqtt_host, _CONFIG.mqtt_port, _CONFIG.mqtt_keepalive)
        _BUS.loop_start()
        logger.info("bus connected to %s:%d", _CONFIG.mqtt_host, _CONFIG.mqtt_port)
        return _BUS
    except Exception as exc:
        logger.warning("bus init failed (capture disabled): %s", exc)
        _BUS = None
        return None


def _pipeline_tail(n: int = 40) -> str:
    """Return last n lines of pipeline.log."""
    path = _LOGS / "pipeline.log"
    if not path.exists():
        return "(no pipeline.log)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


# ----------------------------------------------------------------------
# /api/* helpers
# ----------------------------------------------------------------------

def _api_status() -> dict:
    """Daemon state + store counts."""
    pid = None
    daemon = "stopped"
    pidfile = None
    if _CONFIG:
        pidfile = _CONFIG.store_dir / ".fiam.pid"
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
                # check alive
                try:
                    import os
                    os.kill(pid, 0)
                    daemon = "running"
                except (OSError, ProcessLookupError):
                    # Stale pidfile — process is gone. Clean it up.
                    pid = None
                    try:
                        pidfile.unlink()
                    except OSError:
                        pass
            except ValueError:
                pid = None

    events = 0
    embeddings = 0
    last_processed = None
    home = str(_CONFIG.home_path) if _CONFIG else ""
    if _CONFIG:
        if _POOL:
            events = _POOL.event_count
        feature_vectors = _CONFIG.feature_dir / "flow_vectors.npy"
        if feature_vectors.exists():
            try:
                import numpy as np
                embeddings = int(np.load(feature_vectors, mmap_mode="r").shape[0])
            except Exception:
                embeddings = 0
        cursor = _CONFIG.store_dir / "cursor.json"
        if cursor.exists():
            try:
                obj = json.loads(cursor.read_text(encoding="utf-8"))
                last_processed = obj.get("last_processed_at")
            except (json.JSONDecodeError, OSError):
                pass

    return {
        "daemon": daemon,
        "pid": pid,
        "events": events,
        "embeddings": embeddings,
        "last_processed": last_processed,
        "home": home,
        "uptime_sec": None,
    }


def _api_events(limit: int = 50) -> list[dict]:
    """Events from Pool metadata + body preview."""
    if not _POOL:
        return []
    events = _POOL.load_events()
    if not events:
        return []
    out: list[dict] = []
    for ev in events:
        body = _POOL.read_body(ev.id)
        preview = " ".join(body.split())[:140]
        # Intensity from access_count (0→0.3, 10+→1.0)
        intensity = min(1.0, 0.3 + ev.access_count * 0.07)
        out.append({
            "id": ev.id,
            "time": ev.t.isoformat(),
            "intensity": intensity,
            "last_accessed": "",
            "access_count": ev.access_count,
            "preview": preview,
        })
    out.sort(key=lambda e: e["time"], reverse=True)
    return out[:limit]


def _api_event(event_id: str) -> dict | None:
    """Full content of one event from Pool."""
    if not _POOL:
        return None
    ev = _POOL.get_event(event_id)
    if ev is None:
        return None
    body = _POOL.read_body(event_id)
    return {
        "id": ev.id,
        "frontmatter": {
            "time": ev.t.isoformat(),
            "access_count": str(ev.access_count),
            "fingerprint_idx": str(ev.fingerprint_idx),
        },
        "body": body,
    }


def _api_schedule() -> list[dict]:
    """Pending wakes from schedule.jsonl."""
    if not _CONFIG:
        return []
    path = _CONFIG.schedule_path
    if not path.exists():
        return []
    now = datetime.now(timezone.utc)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            wake_at = datetime.fromisoformat(entry["wake_at"])
            if wake_at.tzinfo is None:
                wake_at = wake_at.replace(tzinfo=timezone.utc)
            if wake_at > now:
                out.append({
                    "wake_at": entry["wake_at"],
                    "type": entry.get("type", "private"),
                    "reason": entry.get("reason", ""),
                })
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    out.sort(key=lambda e: e["wake_at"])
    return out


def _api_health() -> dict:
    """Aggregate fault-tolerance signals — daemon, scheduler, budget."""
    status = _api_status()
    out: dict = {
        "daemon": status["daemon"],
        "pid": status["pid"],
        "events": status["events"],
        "last_processed": status["last_processed"],
        "missed_wakes": 0,
        "failed_wakes": 0,
        "pending_wakes": 0,
        "retry_wakes": 0,
        "budget": None,
        "budget_ok": True,
        "last_pipeline_error": None,
    }
    if not _CONFIG:
        return out

    # Pending + retry counts
    sched = _CONFIG.schedule_path
    if sched.exists():
        for line in sched.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["pending_wakes"] += 1
            if int(e.get("attempts", 0)) > 0:
                out["retry_wakes"] += 1

    # Missed / failed archives
    for kind in ("missed", "failed"):
        p = _CONFIG.self_dir / f"schedule_{kind}.jsonl"
        if p.exists():
            out[f"{kind}_wakes"] = sum(
                1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()
            )

    # Budget
    try:
        # fiam_lib lives under scripts/; path was removed at startup to avoid
        # fiam.py shadowing the fiam package, so re-add briefly for this import.
        _scripts_str = str(_ROOT / "scripts")
        _added = False
        if _scripts_str not in sys.path:
            sys.path.insert(0, _scripts_str)
            _added = True
        try:
            from fiam_lib.cost import check_budget, daily_spend
            ok, reason = check_budget(_CONFIG)
            out["budget_ok"] = ok
            out["budget"] = {"daily_spend": daily_spend(_CONFIG), "reason": reason}
        finally:
            if _added and _scripts_str in sys.path:
                sys.path.remove(_scripts_str)
    except Exception as e:  # pragma: no cover - best effort
        out["budget"] = {"error": str(e)}

    # Last pipeline error line
    tail = _pipeline_tail(200)
    for ln in reversed(tail.splitlines()):
        if "ERROR" in ln or "error" in ln.lower():
            out["last_pipeline_error"] = ln
            break

    return out


def _api_state() -> dict | None:
    """Parse state.md frontmatter."""
    if not _CONFIG:
        return None
    state_path = Path(_CONFIG.home_path) / "self" / "state.md"
    if not state_path.exists():
        return None
    text = state_path.read_text(encoding="utf-8", errors="replace")
    mood = ""
    tension = 0.0
    reflection = ""
    updated_at = ""
    in_fm = False
    body: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if s == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            if s.startswith("mood:"):
                mood = s.split(":", 1)[1].strip().strip('"')
            elif s.startswith("tension:"):
                try:
                    tension = float(s.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif s.startswith("updated_at:"):
                updated_at = s.split(":", 1)[1].strip().strip('"')
        else:
            body.append(line)
    reflection = " ".join(l.strip() for l in body if l.strip())[:400]
    return {"mood": mood, "tension": tension, "reflection": reflection, "updated_at": updated_at}


def _api_config() -> dict:
    """Runtime/editable dashboard config."""
    if not _CONFIG:
        return {"memory_mode": "manual", "annotation": {"processed_until": 0}}
    return {
        "memory_mode": _CONFIG.memory_mode,
        "annotation": _annotation_state(),
    }


def _api_plugins() -> dict:
    """Return registered functional plugins."""
    if not _CONFIG:
        return {"plugins": []}
    from fiam.plugins import load_plugins
    return {
        "plugins": [
            {
                "id": plugin.id,
                "name": plugin.name,
                "enabled": plugin.enabled,
                "status": plugin.status,
                "kind": plugin.kind,
                "description": plugin.description,
                "transports": list(plugin.transports),
                "capabilities": list(plugin.capabilities),
                "receive_sources": list(plugin.receive_sources),
                "dispatch_targets": list(plugin.dispatch_targets),
                "entrypoint": plugin.entrypoint,
                "auth": plugin.auth,
                "latency": plugin.latency,
                "env": list(plugin.env),
                "replaces": list(plugin.replaces),
                "notes": list(plugin.notes),
            }
            for plugin in load_plugins(_CONFIG)
        ]
    }


def _update_memory_mode(payload: dict) -> dict:
    """Persist conductor.memory_mode in fiam.toml."""
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    mode = str(payload.get("memory_mode", "")).strip().lower()
    if mode not in ("manual", "auto"):
        raise ValueError("memory_mode must be manual or auto")

    path = _CONFIG.toml_path
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    out: list[str] = []
    in_conductor = False
    saw_conductor = False
    wrote_mode = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_conductor and not wrote_mode:
                out.append(f'memory_mode = "{mode}"')
                wrote_mode = True
            in_conductor = stripped == "[conductor]"
            saw_conductor = saw_conductor or in_conductor
            out.append(line)
            continue
        if in_conductor and stripped.startswith("memory_mode"):
            out.append(f'memory_mode = "{mode}"')
            wrote_mode = True
        else:
            out.append(line)

    if not saw_conductor:
        if out and out[-1].strip():
            out.append("")
        out.extend(["[conductor]", f'memory_mode = "{mode}"'])
    elif in_conductor and not wrote_mode:
        out.append(f'memory_mode = "{mode}"')

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    _CONFIG.memory_mode = mode
    return {"ok": True, "memory_mode": mode}


def _update_plugin(payload: dict) -> dict:
    """Enable or disable one plugin manifest."""
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    plugin_id = str(payload.get("id", "")).strip()
    if not plugin_id:
        raise ValueError("missing plugin id")
    enabled = bool(payload.get("enabled"))
    from fiam.plugins import set_plugin_enabled
    plugin = set_plugin_enabled(_CONFIG, plugin_id, enabled)
    return {"ok": True, "id": plugin.id, "enabled": plugin.enabled}


def _api_capture(payload: dict) -> dict:
    """Forward a mobile/quick-capture event to the MQTT bus.

    The daemon subscribes to ``fiam/receive/favilla`` and handles
    ingestion (embed + gorge + pool) through the unified Conductor.
    Dashboard no longer touches Pool directly — it's a pure HTTP→MQTT
    bridge for clients that can't speak MQTT (e.g. the Android app).

    Expected payload keys: text (required), source (optional),
    url (optional), tags (optional list).
    """
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    source = (payload.get("source") or "favilla").strip()
    url = (payload.get("url") or "").strip()
    from fiam.plugins import is_receive_enabled
    if not is_receive_enabled(_CONFIG, "favilla"):
        raise RuntimeError("favilla plugin disabled")

    bus = _get_bus()
    if bus is None:
        raise RuntimeError("MQTT bus unavailable")
    ok = bus.publish_receive("favilla", {
        "text": text,
        "source": "favilla",
        "from_name": source,
        "url": url,
        "tags": payload.get("tags") or [],
        "t": datetime.now(timezone.utc),
    })
    if not ok:
        raise RuntimeError("publish rejected")
    return {"ok": True, "queued": True}


# ------------------------------------------------------------------
# Pool-based APIs
# ------------------------------------------------------------------

def _pool_graph() -> dict:
    """Return nodes/edges from Pool for visualization."""
    if not _POOL:
        return {"nodes": [], "edges": []}
    from fiam.store.pool import Pool

    events = _POOL.load_events()
    if not events:
        return {"nodes": [], "edges": []}

    # Build idx→id map and node list
    idx_to_id: dict[int, str] = {}
    nodes: list[dict] = []
    for ev in events:
        idx_to_id[ev.fingerprint_idx] = ev.id
        # Label = pretty version of DS-given event name
        label = ev.id.replace("_", " ")
        # Intensity from access_count (0→0.3, 10+→1.0)
        intensity = min(1.0, 0.3 + ev.access_count * 0.07)
        nodes.append({
            "id": ev.id,
            "label": label,
            "intensity": intensity,
            "time": ev.t.isoformat(),
            "last_accessed": "",  # TODO: track in Event if needed
            "access_count": ev.access_count,
        })

    # Convert PyG edges to {source, target, kind, weight}
    ei, ea = _POOL.load_edges()
    edge_list: list[dict] = []
    for i in range(ei.shape[1]):
        src_idx = int(ei[0, i])
        dst_idx = int(ei[1, i])
        src_id = idx_to_id.get(src_idx)
        dst_id = idx_to_id.get(dst_idx)
        if not src_id or not dst_id:
            continue
        type_id = int(ea[i, 0])
        weight = float(ea[i, 1])
        edge_list.append({
            "source": src_id,
            "target": dst_id,
            "kind": Pool.edge_type_name(type_id),
            "weight": weight,
        })

    return {"nodes": nodes, "edges": edge_list}


def _pool_event_detail(event_id: str) -> dict | None:
    """Full event detail for editing."""
    if not _POOL:
        return None
    ev = _POOL.get_event(event_id)
    if not ev:
        return None
    body = _POOL.read_body(event_id)
    return {
        "id": ev.id,
        "body": body,
        "time": ev.t.isoformat(),
        "access_count": ev.access_count,
        "fingerprint_idx": ev.fingerprint_idx,
    }


def _pool_update_event(event_id: str, payload: dict) -> dict:
    """Update event body. Re-embed if embedder available."""
    if not _POOL:
        raise RuntimeError("pool not loaded")
    ev = _POOL.get_event(event_id)
    if not ev:
        raise ValueError(f"event not found: {event_id}")

    new_body = payload.get("body")
    if new_body is None:
        raise ValueError("missing body")

    with _COMPUTE_LOCK:
        _POOL.write_body(event_id, new_body)
        re_embedded = False
        embedder = _get_embedder()
        if embedder and ev.fingerprint_idx >= 0:
            try:
                import numpy as np
                vec = embedder.embed(new_body)
                _POOL.update_fingerprint(ev.fingerprint_idx, vec)
                _POOL.rebuild_cosine()
                re_embedded = True
            except Exception as exc:
                logger.warning("re-embed failed for %s: %s", event_id, exc)
    return {"ok": True, "id": event_id, "re_embedded": re_embedded}


def _pool_create_edge(payload: dict) -> dict:
    """Create an edge between two events."""
    if not _POOL:
        raise RuntimeError("pool not loaded")
    from fiam.store.pool import Pool

    src_id = payload.get("source")
    dst_id = payload.get("target")
    kind = payload.get("kind", "semantic")
    weight = float(payload.get("weight", 0.5))

    if not src_id or not dst_id:
        raise ValueError("missing source or target")

    src_ev = _POOL.get_event(src_id)
    dst_ev = _POOL.get_event(dst_id)
    if not src_ev or not dst_ev:
        raise ValueError("event not found")
    if src_ev.fingerprint_idx < 0 or dst_ev.fingerprint_idx < 0:
        raise ValueError("event not embedded")

    type_id = Pool.edge_type_id(kind)
    with _COMPUTE_LOCK:
        _POOL.add_edge(src_ev.fingerprint_idx, dst_ev.fingerprint_idx, type_id, weight)
    return {"ok": True}


def _pool_update_edge(payload: dict) -> dict:
    """Update an existing edge's type and/or weight."""
    if not _POOL:
        raise RuntimeError("pool not loaded")
    from fiam.store.pool import Pool

    src_id = payload.get("source")
    dst_id = payload.get("target")
    if not src_id or not dst_id:
        raise ValueError("missing source or target")

    src_ev = _POOL.get_event(src_id)
    dst_ev = _POOL.get_event(dst_id)
    if not src_ev or not dst_ev:
        raise ValueError("event not found")

    import numpy as np
    ei, ea = _POOL.load_edges()
    mask = (ei[0] == src_ev.fingerprint_idx) & (ei[1] == dst_ev.fingerprint_idx)
    if not mask.any():
        raise ValueError("edge not found")

    with _COMPUTE_LOCK:
        if "kind" in payload:
            ea[mask, 0] = Pool.edge_type_id(payload["kind"])
        if "weight" in payload:
            ea[mask, 1] = float(payload["weight"])
        _POOL._save_edges()
    return {"ok": True}


def _pool_delete_edge(payload: dict) -> dict:
    """Remove an edge."""
    if not _POOL:
        raise RuntimeError("pool not loaded")

    src_id = payload.get("source")
    dst_id = payload.get("target")
    if not src_id or not dst_id:
        raise ValueError("missing source or target")

    src_ev = _POOL.get_event(src_id)
    dst_ev = _POOL.get_event(dst_id)
    if not src_ev or not dst_ev:
        raise ValueError("event not found")

    import numpy as np
    with _COMPUTE_LOCK:
        ei, ea = _POOL.load_edges()
        mask = ~((ei[0] == src_ev.fingerprint_idx) & (ei[1] == dst_ev.fingerprint_idx))
        _POOL._edge_index = ei[:, mask]
        _POOL._edge_attr = ea[mask]
        _POOL._save_edges()
    return {"ok": True}


def _pool_delete_event(event_id: str) -> dict:
    """Delete an event and all related data."""
    if not _POOL:
        raise RuntimeError("pool not loaded")
    with _COMPUTE_LOCK:
        ok = _POOL.delete_event(event_id)
    if not ok:
        raise ValueError(f"event not found: {event_id}")
    return {"ok": True, "id": event_id}


# ------------------------------------------------------------------
# Annotation endpoints
# ------------------------------------------------------------------

_ANNOTATION_PROPOSAL: dict | None = None  # in-memory pending proposal


def _annotation_state() -> dict:
    if not _CONFIG:
        return {"processed_until": 0}
    path = _CONFIG.annotation_state_path
    if not path.exists():
        return {"processed_until": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"processed_until": 0}
    return {"processed_until": int(data.get("processed_until", 0))}


def _save_annotation_state(processed_until: int) -> None:
    if not _CONFIG:
        return
    path = _CONFIG.annotation_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"processed_until": int(processed_until)}, indent=2),
        encoding="utf-8",
    )


def _safe_event_id(raw: str, fallback: str, reserved: set[str] | None = None) -> str:
    import re
    reserved = reserved or set()
    name = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(raw or "").strip())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = fallback
    if len(name) > 60:
        name = name[:60].rstrip("_")
    if name not in reserved and (_POOL is None or _POOL.get_event(name) is None):
        return name
    base = name
    i = 2
    while (_POOL and _POOL.get_event(f"{base}_{i}") is not None) or f"{base}_{i}" in reserved:
        i += 1
    return f"{base}_{i}"


def _beat_vectors_from_store(beats: list[dict]) -> list:
    if not _CONFIG:
        return []
    try:
        from fiam.store.beat import Beat
        from fiam.store.features import FeatureStore
        store = FeatureStore(_CONFIG.feature_dir, dim=_CONFIG.embedding_dim)
        vectors = []
        for raw in beats:
            try:
                vectors.append(store.get_beat_vector(Beat.from_dict(raw)))
            except Exception:
                vectors.append(None)
        return vectors
    except Exception:
        return []


def _parse_beat_time(raw: str):
    from datetime import datetime as _dt, timezone as _tz
    try:
        dt = _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt
    except (TypeError, ValueError):
        return _dt.now(_tz.utc)


def _annotate_request(payload: dict) -> dict:
    """Load unprocessed flow beats for manual annotation.

    payload: {"offset"?: int, "limit"?: int} — defaults to last 100 beats.
    Returns: {"beats": [...], "cuts": [...], "drift_cuts": [...], ...}
    """
    global _ANNOTATION_PROPOSAL
    if not _CONFIG:
        raise RuntimeError("config not loaded")

    flow_path = _CONFIG.flow_path
    if not flow_path.exists():
        raise ValueError("flow.jsonl not found")

    lines = flow_path.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    state = _annotation_state()
    limit = max(1, int(payload.get("limit", 100)))
    requested_offset = int(payload.get("offset", state["processed_until"]))
    offset = max(requested_offset, state["processed_until"])
    end = min(offset + limit, total)

    beats: list[dict] = []
    for line in lines[offset:end]:
        line = line.strip()
        if not line:
            continue
        try:
            beats.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not beats:
        raise ValueError("no beats to annotate")

    cuts = [0] * max(0, len(beats) - 1)
    drift_cuts = [0] * max(0, len(beats) - 1)

    _ANNOTATION_PROPOSAL = {
        "beats": beats,
        "cuts": cuts,
        "drift_cuts": drift_cuts,
        "edges": [],
        "names": {},
        "flow_offset": offset,
        "flow_end": end,
        "processed_until": state["processed_until"],
        "status": "cuts_proposed",
    }
    return _ANNOTATION_PROPOSAL


def _annotate_edges(payload: dict) -> dict:
    """Phase 2: After human reviews cuts, request edge proposals."""
    global _ANNOTATION_PROPOSAL
    if not _CONFIG or not _POOL:
        raise RuntimeError("config/pool not loaded")
    if not _ANNOTATION_PROPOSAL:
        raise ValueError("no active proposal — run /annotate/request first")

    # Accept human-corrected cuts; this is the first point where DS is called.
    cuts = payload.get("cuts", _ANNOTATION_PROPOSAL.get("cuts", []))
    drift_cuts = payload.get("drift_cuts", _ANNOTATION_PROPOSAL.get("drift_cuts", []))
    beats = _ANNOTATION_PROPOSAL["beats"]

    from fiam.annotator import cuts_to_segments, propose_edges
    segments = cuts_to_segments(beats, cuts)

    # Build new events from confirmed segments
    new_events: list[dict] = []
    for i, seg in enumerate(segments):
        start, end = seg["start"], seg["end"]
        body_lines = [b.get("text", "") for b in beats[start:end + 1]]
        new_events.append({
            "id": f"seg_{i}",
            "time": beats[start].get("t", ""),
            "body": "\n".join(body_lines),
        })

    # Existing events from pool. During data collection DS is allowed to read
    # the current graph context; candidate pruning can be trained later.
    existing_events: list[dict] = []
    if _POOL:
        pool_events = _POOL.load_events()
        for ev in pool_events:
            body = _POOL.read_body(ev.id)
            existing_events.append({
                "id": ev.id,
                "time": ev.t.isoformat(),
                "body": body[:400],
            })

    result = propose_edges(new_events, existing_events, _CONFIG)

    _ANNOTATION_PROPOSAL["cuts"] = cuts
    _ANNOTATION_PROPOSAL["drift_cuts"] = drift_cuts
    _ANNOTATION_PROPOSAL["edges"] = result["edges"]
    _ANNOTATION_PROPOSAL["names"] = result.get("names", {})
    _ANNOTATION_PROPOSAL["status"] = "edges_proposed"
    return _ANNOTATION_PROPOSAL


def _annotate_confirm(payload: dict) -> dict:
    """Confirm annotations: save training data (with vectors) + create events."""
    global _ANNOTATION_PROPOSAL
    if not _CONFIG or not _POOL:
        raise RuntimeError("config/pool not loaded")
    if not _ANNOTATION_PROPOSAL:
        raise ValueError("no active proposal")

    beats = _ANNOTATION_PROPOSAL["beats"]
    cuts = payload.get("cuts", _ANNOTATION_PROPOSAL.get("cuts", []))
    drift_cuts = payload.get("drift_cuts", _ANNOTATION_PROPOSAL.get("drift_cuts", []))
    edges = payload.get("edges", _ANNOTATION_PROPOSAL.get("edges", []))
    names = _ANNOTATION_PROPOSAL.get("names", {})

    import numpy as np
    from fiam.store.pool import Pool, Event
    from fiam.annotator import save_training_data, cuts_to_segments

    # 1. Load frozen beat vectors. If this batch predates the feature store,
    # fall back to embedding once so the annotation still produces training data.
    beat_vectors: list | None = _beat_vectors_from_store(beats)
    with _COMPUTE_LOCK:
        if not beat_vectors or not any(v is not None for v in beat_vectors):
            embedder = _get_embedder()
            vecs = []
            if embedder:
                for b in beats:
                    text = b.get("text", "").strip()
                    if text:
                        try:
                            vecs.append(embedder.embed(text))
                        except Exception:
                            vecs.append(None)
                    else:
                        vecs.append(None)
            beat_vectors = vecs

    # 2. Decide final event ids before saving labels/edges.
    segments = cuts_to_segments(beats, cuts)
    seg_to_event_id: dict[str, str] = {}
    reserved_ids: set[str] = set()
    for i, _seg in enumerate(segments):
        seg_id = f"seg_{i}"
        fallback = f"ann_{_ANNOTATION_PROPOSAL['flow_offset']}_{i}"
        event_id = _safe_event_id(names.get(seg_id, ""), fallback, reserved_ids)
        reserved_ids.add(event_id)
        seg_to_event_id[seg_id] = event_id

    normalized_edges = []
    for e in edges:
        normalized = dict(e)
        normalized["src"] = seg_to_event_id.get(str(e.get("src", "")), e.get("src"))
        normalized["dst"] = seg_to_event_id.get(str(e.get("dst", "")), e.get("dst"))
        normalized_edges.append(normalized)

    # 3. Save training data (text + vectors + event/drift cuts)
    training_dir = _ROOT / "training_data"
    stats = save_training_data(
        beats, cuts, normalized_edges, training_dir,
        beat_vectors=beat_vectors,
        drift_cuts=drift_cuts,
    )

    # 4. Create events in pool from confirmed segments
    created_events: list[str] = []
    created_event_times: dict[str, tuple] = {}

    with _COMPUTE_LOCK:
        for i, seg in enumerate(segments):
            start, end = seg["start"], seg["end"]
            body_lines = [b.get("text", "") for b in beats[start:end + 1]]
            body = "\n".join(body_lines)
            event_id = seg_to_event_id[f"seg_{i}"]

            t_start = _parse_beat_time(beats[start].get("t", ""))
            t_end = _parse_beat_time(beats[end].get("t", ""))

            seg_vecs = []
            if beat_vectors:
                for idx in range(start, end + 1):
                    if beat_vectors[idx] is not None:
                        seg_vecs.append(beat_vectors[idx])
            fingerprint = np.mean(seg_vecs, axis=0).astype(np.float32) if seg_vecs else None
            if fingerprint is not None:
                norm = np.linalg.norm(fingerprint)
                if norm > 1e-9:
                    fingerprint = (fingerprint / norm).astype(np.float32)

            _POOL.write_body(event_id, body)
            fp_idx = -1
            if fingerprint is not None:
                fp_idx = _POOL.append_fingerprint(fingerprint)

            ev = Event(id=event_id, t=t_start, access_count=0, fingerprint_idx=fp_idx)
            _POOL.append_event(ev)
            created_events.append(event_id)
            created_event_times[event_id] = (t_start, t_end)

        _POOL.rebuild_cosine()

        # 5. Create weak temporal edges first; DS edges override same pair.
        edge_map: dict[tuple[str, str], tuple[str, float]] = {}
        for a, b in zip(created_events, created_events[1:]):
            _a_start, a_end = created_event_times[a]
            b_start, _b_end = created_event_times[b]
            gap = max(0.0, (b_start - a_end).total_seconds())
            if gap <= 1800:
                weight = max(0.05, 0.2 * (1.0 - gap / 1800.0))
                edge_map[(a, b)] = ("temporal", weight)

        for e in normalized_edges:
            src = str(e.get("src", ""))
            dst = str(e.get("dst", ""))
            if not src or not dst or src == dst:
                continue
            edge_map[(src, dst)] = (str(e.get("type", "semantic")), float(e.get("weight", 0.5)))

        created_edges = 0
        for (src, dst), (kind, weight) in edge_map.items():
            try:
                src_ev = _POOL.get_event(src)
                dst_ev = _POOL.get_event(dst)
                if src_ev and dst_ev and src_ev.fingerprint_idx >= 0 and dst_ev.fingerprint_idx >= 0:
                    _POOL.add_edge(
                        src_ev.fingerprint_idx,
                        dst_ev.fingerprint_idx,
                        Pool.edge_type_id(kind),
                        weight,
                    )
                    created_edges += 1
            except Exception as exc:
                logger.warning("edge creation failed: %s", exc)

    _save_annotation_state(int(_ANNOTATION_PROPOSAL.get("flow_end", 0)))
    _ANNOTATION_PROPOSAL = None

    return {
        "ok": True,
        "events_created": created_events,
        "edges_created": created_edges,
        **stats,
    }


def _annotate_proposal() -> dict:
    """Return current pending proposal."""
    if not _ANNOTATION_PROPOSAL:
        return {"status": "none"}
    return _ANNOTATION_PROPOSAL


def _api_flow(offset: int = 0, limit: int = 50) -> dict:
    """Read beats from flow.jsonl with pagination."""
    if not _CONFIG:
        return {"beats": [], "offset": 0, "total": 0}
    flow_path = _CONFIG.flow_path
    if not flow_path.exists():
        return {"beats": [], "offset": 0, "total": 0}

    lines = flow_path.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    # Return from the end (most recent) if offset is 0
    if offset <= 0:
        start = max(0, total - limit)
    else:
        start = offset
    end = min(start + limit, total)

    beats: list[dict] = []
    for line in lines[start:end]:
        line = line.strip()
        if not line:
            continue
        try:
            beats.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"beats": beats, "offset": start, "total": total}


def _ingest_token_ok(handler) -> bool:
    """Constant-time comparison of X-Fiam-Token header against env secret."""
    import os
    import hmac
    expected = os.environ.get("FIAM_INGEST_TOKEN", "")
    if not expected:
        return False
    got = handler.headers.get("X-Fiam-Token", "")
    if not got:
        return False
    return hmac.compare_digest(got, expected)


def _viewer_token_ok(handler) -> bool:
    """Auth for dashboard viewing. Accepts FIAM_VIEW_TOKEN via:
    1. Cookie  fiam_view=<token>   (set by /login redirect)
    2. Query   ?token=<token>       (one-shot, used by /login)
    3. Header  X-Fiam-View-Token    (programmatic clients)
    Returns True if any source matches.
    """
    import os
    import hmac
    expected = os.environ.get("FIAM_VIEW_TOKEN", "")
    if not expected:
        return False
    # cookie
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("fiam_view="):
            got = part[len("fiam_view="):]
            if got and hmac.compare_digest(got, expected):
                return True
    # header
    got = handler.headers.get("X-Fiam-View-Token", "")
    if got and hmac.compare_digest(got, expected):
        return True
    # query (only used by /login below)
    raw = handler.path
    if "?" in raw:
        import urllib.parse as _u
        qs = dict(_u.parse_qsl(raw.split("?", 1)[1]))
        got = qs.get("token", "")
        if got and hmac.compare_digest(got, expected):
            return True
    return False


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serve dashboard HTML and dynamic data endpoints."""

    # ------------------------------------------------------------------
    # /api/* — JSON endpoints for SvelteKit dashboard
    # ------------------------------------------------------------------
    def _handle_api(self, path: str, raw: str):
        import urllib.parse as _u
        query = {}
        if "?" in raw:
            query = dict(_u.parse_qsl(raw.split("?", 1)[1]))

        try:
            if path == "/api/status":
                self._serve_json(_api_status())
            elif path == "/api/events":
                limit = int(query.get("limit", 50))
                self._serve_json(_api_events(limit))
            elif path.startswith("/api/event/"):
                ev_id = path[len("/api/event/") :]
                ev = _api_event(ev_id)
                if ev is None:
                    self.send_error(404)
                else:
                    self._serve_json(ev)
            elif path == "/api/schedule":
                self._serve_json(_api_schedule())
            elif path == "/api/state":
                self._serve_json(_api_state())
            elif path == "/api/graph":
                self._serve_json(_pool_graph())
            elif path == "/api/pool/graph":
                self._serve_json(_pool_graph())
            elif path.startswith("/api/pool/event/"):
                ev_id = path[len("/api/pool/event/"):]
                ev = _pool_event_detail(ev_id)
                if ev is None:
                    self.send_error(404)
                else:
                    self._serve_json(ev)
            elif path == "/api/flow":
                offset = int(query.get("offset", 0))
                limit = int(query.get("limit", 50))
                self._serve_json(_api_flow(offset, limit))
            elif path == "/api/pipeline":
                self._serve_json({"lines": _pipeline_tail(200).splitlines()})
            elif path == "/api/whoami":
                # Determine role from Caddy-forwarded header (basic-auth user)
                user = self.headers.get("X-Forwarded-User", "anon").lower()
                role = user if user in ("iris", "ai", "fiet") else "anon"
                self._serve_json({"role": role})
            elif path == "/api/health":
                self._serve_json(_api_health())
            elif path == "/api/config":
                self._serve_json(_api_config())
            elif path == "/api/plugins":
                self._serve_json(_api_plugins())
            elif path == "/api/pool/edge-types":
                from fiam.store.pool import Pool
                self._serve_json({"types": list(Pool.EDGE_TYPE_NAMES.values())})
            elif path == "/api/annotate/proposal":
                self._serve_json(_annotate_proposal())
            else:
                self.send_error(404)
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)


    def do_GET(self):
        # Strip query string
        raw = self.path
        path = raw.split("?")[0]

        # /login?token=<view_token> → set cookie + redirect to /
        if path == "/login":
            if _viewer_token_ok(self):
                import os
                tok = os.environ.get("FIAM_VIEW_TOKEN", "")
                self.send_response(302)
                # 30-day cookie, HttpOnly, SameSite=Lax. Secure inferred from CF tunnel TLS.
                self.send_header(
                    "Set-Cookie",
                    f"fiam_view={tok}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax; Secure",
                )
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._serve_json({"error": "unauthorized"}, status=401)
            return

        # All other GETs require viewer auth
        if not _viewer_token_ok(self):
            self._serve_json({"error": "unauthorized"}, status=401)
            return

        # /api/* JSON endpoints used by the SvelteKit SPA
        if path.startswith("/api/"):
            return self._handle_api(path, raw)

        # Everything else → SvelteKit static build (SPA with index.html fallback)
        return self._serve_spa(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        # Mutation endpoints require ingest token
        pool_write_paths = {"/api/pool/edge", "/api/pool/edge/delete"}
        pool_write_prefixes = ("/api/pool/event/",)
        annotate_paths = {"/api/annotate/request", "/api/annotate/edges", "/api/annotate/confirm"}
        config_paths = {"/api/config/memory-mode", "/api/config/plugin"}
        is_pool_write = path in pool_write_paths or any(path.startswith(p) for p in pool_write_prefixes)
        is_annotate = path in annotate_paths
        is_config_write = path in config_paths

        if path == "/api/capture":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 256 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _api_capture(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if is_pool_write:
            # Pool mutations — require viewer auth (console user)
            if not _viewer_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > 1024 * 1024:
                self._serve_json({"error": "payload too large"}, status=400)
                return
            payload = {}
            if length > 0:
                try:
                    body = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._serve_json({"error": f"bad json: {e}"}, status=400)
                    return
            try:
                if path.startswith("/api/pool/event/delete/"):
                    ev_id = path[len("/api/pool/event/delete/"):]
                    result = _pool_delete_event(ev_id)
                elif path.startswith("/api/pool/event/"):
                    ev_id = path[len("/api/pool/event/"):]
                    result = _pool_update_event(ev_id, payload)
                elif path == "/api/pool/edge":
                    result = _pool_create_edge(payload)
                elif path == "/api/pool/edge/delete":
                    result = _pool_delete_edge(payload)
                else:
                    self.send_error(404)
                    return
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if is_annotate:
            if not _viewer_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            payload = {}
            if length > 0:
                try:
                    body = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._serve_json({"error": f"bad json: {e}"}, status=400)
                    return
            try:
                if path == "/api/annotate/request":
                    result = _annotate_request(payload)
                elif path == "/api/annotate/edges":
                    result = _annotate_edges(payload)
                elif path == "/api/annotate/confirm":
                    result = _annotate_confirm(payload)
                else:
                    self.send_error(404)
                    return
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("annotate error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if is_config_write:
            if not _viewer_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            payload = {}
            if length > 0:
                try:
                    body = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._serve_json({"error": f"bad json: {e}"}, status=400)
                    return
            try:
                if path == "/api/config/memory-mode":
                    result = _update_memory_mode(payload)
                elif path == "/api/config/plugin":
                    result = _update_plugin(payload)
                else:
                    self.send_error(404)
                    return
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        self.send_error(404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if not _viewer_token_ok(self):
            self._serve_json({"error": "unauthorized"}, status=401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1024 * 1024:
            self._serve_json({"error": "bad payload"}, status=400)
            return
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._serve_json({"error": f"bad json: {e}"}, status=400)
            return
        try:
            if path.startswith("/api/pool/event/"):
                ev_id = path[len("/api/pool/event/"):]
                result = _pool_update_event(ev_id, payload)
            elif path == "/api/pool/edge":
                result = _pool_update_edge(payload)
            else:
                self.send_error(404)
                return
        except ValueError as e:
            self._serve_json({"error": str(e)}, status=400)
            return
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)
            return
        self._serve_json(result)

    def do_OPTIONS(self):
        """CORS preflight for mutation endpoints."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Fiam-Token, X-Fiam-View-Token")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _serve_spa(self, path: str):
        """Serve files from dashboard/build/ with SPA fallback to index.html."""
        build_dir = _ROOT / "dashboard" / "build"
        if not build_dir.is_dir():
            self.send_error(404, "dashboard build missing — run `npm run build`")
            return
        # Strip leading slash, block path traversal
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            self.send_error(403)
            return
        target = (build_dir / rel) if rel else (build_dir / "index.html")
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            # SPA fallback
            target = build_dir / "index.html"
        if not target.exists():
            self.send_error(404)
            return
        import mimetypes
        ctype, _ = mimetypes.guess_type(str(target))
        if not ctype:
            ctype = "application/octet-stream"
        self._serve_file(target, ctype)

    def _serve_file(self, filepath: Path, content_type: str):
        if not filepath.exists():
            self.send_error(404)
            return
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_text(self, text: str, content_type: str):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_json(self, obj, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # suppress access logs


def main():
    parser = argparse.ArgumentParser(description="fiam debug dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address (default 127.0.0.1; use 0.0.0.0 only behind a trusted proxy)")
    args = parser.parse_args()

    _load_config()
    import os
    if not os.environ.get("FIAM_VIEW_TOKEN"):
        print("WARN: FIAM_VIEW_TOKEN not set — all GET requests will return 401.",
              file=sys.stderr)
    server = HTTPServer((args.bind, args.port), DashboardHandler)
    print(f"Dashboard: http://{args.bind}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
