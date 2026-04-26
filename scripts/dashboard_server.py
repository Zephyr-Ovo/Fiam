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
_WEARABLE_LOCK = threading.Lock()

from fiam_lib.dashboard_annotation import (
    configure as _configure_annotation,
    annotation_state as _annotation_state,
    annotate_confirm as _annotate_confirm,
    annotate_edges as _annotate_edges,
    annotate_proposal as _annotate_proposal,
    annotate_request as _annotate_request,
)
from fiam_lib.flow_text import normalize_beats

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
    _configure_annotation(
        root=_ROOT,
        config=_CONFIG,
        pool=_POOL,
        compute_lock=_COMPUTE_LOCK,
        get_embedder=_get_embedder,
        logger=logger,
    )


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
        _BUS.subscribe("fiam/dispatch/xiao", _on_wearable_dispatch)
        _BUS.subscribe("fiam/dispatch/limen", _on_wearable_dispatch)
        _BUS.connect(_CONFIG.mqtt_host, _CONFIG.mqtt_port, _CONFIG.mqtt_keepalive)
        _BUS.loop_start()
        logger.info("bus connected to %s:%d", _CONFIG.mqtt_host, _CONFIG.mqtt_port)
        return _BUS
    except Exception as exc:
        logger.warning("bus init failed (capture/wearable disabled): %s", exc)
        _BUS = None
        return None


def _wearable_queue_path() -> Path:
    base = (_CONFIG.store_dir if _CONFIG else (_ROOT / "store")) / "wearable"
    base.mkdir(parents=True, exist_ok=True)
    return base / "xiao_queue.jsonl"


def _splash_line() -> str:
    if not _CONFIG:
        return "今天也一起散步"
    daily = _CONFIG.daily_summary_path
    if daily.exists():
        for line in daily.read_text(encoding="utf-8", errors="replace").splitlines():
            clean = line.strip().lstrip("#- ").strip()
            if clean:
                return clean[:80]
    return "今天也一起散步"


def _api_app_splash() -> dict:
    return {
        "ok": True,
        "mode": "stroll",
        "label": "散步",
        "tagline": "a spark → fiam",
        "line": _splash_line(),
    }


def _vision_route() -> dict:
    if not _CONFIG:
        return {"provider": "default", "model": "", "base_url": ""}
    return {
        "provider": getattr(_CONFIG, "vision_provider", "openai_compatible"),
        "model": getattr(_CONFIG, "vision_model", ""),
        "base_url": getattr(_CONFIG, "vision_base_url", ""),
        "api_key_env": getattr(_CONFIG, "vision_api_key_env", "FIAM_VISION_API_KEY"),
    }


def _voice_routes() -> dict:
    if not _CONFIG:
        return {"stt": {}, "tts": {}}
    return {
        "stt": {
            "provider": getattr(_CONFIG, "stt_provider", "openai_compatible"),
            "model": getattr(_CONFIG, "stt_model", ""),
            "base_url": getattr(_CONFIG, "stt_base_url", ""),
            "api_key_env": getattr(_CONFIG, "stt_api_key_env", "FIAM_STT_API_KEY"),
        },
        "tts": {
            "provider": getattr(_CONFIG, "tts_provider", "openai_compatible"),
            "model": getattr(_CONFIG, "tts_model", ""),
            "base_url": getattr(_CONFIG, "tts_base_url", ""),
            "api_key_env": getattr(_CONFIG, "tts_api_key_env", "FIAM_TTS_API_KEY"),
        },
    }


def _display_type_from_text(text: str, explicit: str = "") -> tuple[str, str]:
    raw = (text or "").strip()
    prefix, sep, rest = raw.partition(":")
    if sep and prefix.strip().lower() in {"message", "msg", "kaomoji", "emoji", "status"}:
        explicit = prefix.strip().lower()
        raw = rest.strip()
    kind = (explicit or "").strip().lower()
    aliases = {"msg": "message", "text": "message", "face": "kaomoji"}
    kind = aliases.get(kind, kind)
    if kind not in {"message", "kaomoji", "emoji", "status"}:
        if len(raw) <= 12 and any(ch in raw for ch in "()_^;><=-~*"):
            kind = "kaomoji"
        elif len(raw) <= 8 and any(ord(ch) > 0x2600 for ch in raw):
            kind = "emoji"
        else:
            kind = "message"
    return kind, raw[:240]


def _normalize_wearable_payload(payload: dict) -> dict:
    text = str(payload.get("text") or payload.get("body") or "").strip()
    if not text:
        raise ValueError("missing text")
    explicit = str(payload.get("type") or payload.get("display_type") or "")
    kind, text = _display_type_from_text(text, explicit)
    recipient = str(payload.get("recipient") or "screen").strip() or "screen"
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"xiao-{int(time.time() * 1000)}",
        "t": now,
        "recipient": recipient,
        "type": kind,
        "text": text,
        "ttl_ms": int(payload.get("ttl_ms") or 30000),
        "source": str(payload.get("source") or "dispatch"),
    }


def _enqueue_wearable_message(payload: dict) -> dict:
    item = _normalize_wearable_payload(payload)
    path = _wearable_queue_path()
    with _WEARABLE_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return {"ok": True, "queued": True, "id": item["id"], "type": item["type"]}


def _api_wearable_reply() -> dict:
    path = _wearable_queue_path()
    with _WEARABLE_LOCK:
        rows: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        item = rows[0] if rows else None
        rest = rows[1:] if rows else []
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rest),
            encoding="utf-8",
        )
    if item is None:
        return {"ok": True, "has_message": False}
    return {"ok": True, "has_message": True, **item}


def _on_wearable_dispatch(target: str, payload: dict) -> None:
    try:
        enriched = dict(payload)
        enriched.setdefault("source", f"dispatch/{target}")
        _enqueue_wearable_message(enriched)
        logger.info("wearable queued target=%s type=%s", target, enriched.get("type", "message"))
    except Exception:
        logger.error("wearable dispatch failed target=%s payload=%r", target, payload, exc_info=True)


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
        try:
            from fiam.store.features import FeatureStore
            embeddings = FeatureStore(_CONFIG.feature_dir, dim=_CONFIG.embedding_dim).count()
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
        "multimodal": {
            "vision": _vision_route(),
            "voice": _voice_routes(),
            "stroll": {"internal_name": "stroll", "label": "散步"},
        },
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
    url (optional), tags (optional list), kind/interaction/session_id/meta.
    """
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    source = (payload.get("source") or "favilla").strip()
    url = (payload.get("url") or "").strip()
    meta = dict(payload.get("meta") or {})
    tags = payload.get("tags") or []
    for key in ("kind", "interaction", "session_id", "phase"):
        value = payload.get(key)
        if value not in (None, "", []):
            meta[key] = value
    if any(str(tag).lower() in {"image", "vision", "vision_pending"} for tag in tags):
        meta.setdefault("kind", "action")
        meta["route"] = "vision"
        meta["vision"] = _vision_route()
    from fiam.plugins import is_receive_enabled
    receive_source = source.lower() if source.lower() in {"xiao", "limen"} else "favilla"
    if not is_receive_enabled(_CONFIG, receive_source):
        raise RuntimeError(f"{receive_source} plugin disabled")

    bus = _get_bus()
    if bus is None:
        raise RuntimeError("MQTT bus unavailable")
    ok = bus.publish_receive(receive_source, {
        "text": text,
        "source": receive_source,
        "from_name": source,
        "url": url,
        "tags": tags,
        "kind": meta.get("kind"),
        "interaction": meta.get("interaction"),
        "session_id": meta.get("session_id"),
        "phase": meta.get("phase"),
        "meta": meta,
        "t": datetime.now(timezone.utc),
    })
    if not ok:
        raise RuntimeError("publish rejected")
    return {"ok": True, "queued": True}


def _api_app_status() -> dict:
    status = _api_status()
    flow_count = 0
    thinking_count = 0
    interaction_count = 0
    if _CONFIG and _CONFIG.flow_path.exists():
        try:
            for line in _CONFIG.flow_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                flow_count += 1
                obj = json.loads(line)
                meta = obj.get("meta") or {}
                if meta.get("kind") == "thinking":
                    thinking_count += 1
                if meta.get("kind") == "interaction" or meta.get("interaction"):
                    interaction_count += 1
        except Exception:
            pass
    return {
        "daemon": status.get("daemon"),
        "events": status.get("events"),
        "embeddings": status.get("embeddings"),
        "flow_beats": flow_count,
        "thinking_beats": thinking_count,
        "interaction_beats": interaction_count,
        "home": status.get("home"),
    }


def _api_app_chat(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    source = str(payload.get("source") or "favilla").strip() or "favilla"
    backend = str(payload.get("backend") or "cc").strip().lower() or "cc"
    if backend != "cc":
        raise ValueError("server chat backend must be cc")
    return _run_cc_app_chat(text=text, source=source)


def _run_cc_app_chat(*, text: str, source: str) -> dict:
    import subprocess

    pending_recall = _pending_recall_for_app()
    session = _load_app_active_session()
    command = [
        "claude", "-p", f"[app:{source}] {text}",
        "--output-format", "json",
        "--max-turns", "10",
    ]
    if _CONFIG.cc_model:
        command.extend(["--model", _CONFIG.cc_model])
    if _CONFIG.cc_disallowed_tools:
        command.extend([
            "--disallowedTools",
            *[tool.strip() for tool in _CONFIG.cc_disallowed_tools.split(",") if tool.strip()],
        ])
    if session:
        command.extend(["--resume", session["session_id"]])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=240,
            cwd=str(_CONFIG.home_path),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("claude chat timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("claude not found on server PATH") from exc

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(f"bad claude json: {detail}") from exc

    is_partial_success = data.get("subtype") == "error_max_turns"
    is_error = bool(data.get("is_error")) or result.returncode != 0
    if is_error and not is_partial_success:
        detail = (data.get("error") or data.get("result") or result.stderr or result.stdout or "claude failed")
        raise RuntimeError(str(detail).strip()[:500])

    session_id = str(data.get("session_id") or "").strip()
    if session_id:
        _save_app_active_session(session_id)

    reply = str(data.get("result") or "").strip()
    return {
        "ok": True,
        "backend": "cc",
        "reply": reply,
        "recall": pending_recall,
        "session_id": session_id,
        "subtype": data.get("subtype"),
        "cost_usd": data.get("total_cost_usd", 0),
    }


def _load_app_active_session() -> dict | None:
    path = _CONFIG.active_session_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    session_id = str(data.get("session_id") or "").strip()
    return data if session_id else None


def _save_app_active_session(session_id: str) -> None:
    path = _CONFIG.active_session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _pending_recall_for_app(max_chars: int = 1400) -> str:
    if not _CONFIG:
        return ""
    dirty = _CONFIG.home_path / ".recall_dirty"
    recall_path = _CONFIG.background_path
    if not dirty.exists() or not recall_path.exists():
        return ""
    try:
        text = recall_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


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
    beats = normalize_beats(beats, config=_CONFIG, root=_ROOT)
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
    0. Header  X-Forwarded-User   (trusted local Caddy basic-auth proxy)
    1. Cookie  fiam_view=<token>   (set by /login redirect)
    2. Query   ?token=<token>       (one-shot, used by /login)
    3. Header  X-Fiam-View-Token    (programmatic clients)
    Returns True if any source matches.
    """
    import os
    import hmac
    forwarded_user = handler.headers.get("X-Forwarded-User", "").lower()
    if forwarded_user in {"iris", "ai", "fiet"}:
        return True
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

        if path == "/api/app/splash":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_api_app_splash())
            return

        if path == "/api/app/status":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_api_app_status())
            return

        if path == "/api/wearable/reply":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_api_wearable_reply())
            return

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

        if path == "/api/app/chat":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _api_app_chat(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("app chat error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path == "/api/wearable/message":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 16 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
                result = _enqueue_wearable_message(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("wearable message error")
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
    _get_bus()
    import os
    if not os.environ.get("FIAM_VIEW_TOKEN"):
        print("WARN: FIAM_VIEW_TOKEN not set — direct GET requests will return 401 unless proxied by Caddy auth.",
              file=sys.stderr)
    server = HTTPServer((args.bind, args.port), DashboardHandler)
    print(f"Dashboard: http://{args.bind}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
