"""
Debug dashboard server for fiam daemon.

Serves the dashboard HTML and provides data endpoints by reading
daemon state, pipeline log, events, todo queue, and cost.

Usage:
    python scripts/dashboard_server.py [--port 8766]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
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
_CC_RUNTIME_LOCK = threading.Lock()  # Claude Code uses a single local session lock
_CC_WARM_RUNNER = None
_CC_WARM_IDLE_SECONDS = 60 * 60
_CC_WARM_FAILURE_PREFIX = "__fiam_cc_warm_failed__:"
_CC_HOOK_TAG_RE = re.compile(r"\s*<user-prompt-submit-hook\b[^>]*>.*?(?:</user-prompt-submit-hook>|\Z)\s*", re.DOTALL)
_CC_TRANSCRIPT_SCRUB_TOKENS = ("hook_additional_context", "<user-prompt-submit-hook")
_WEARABLE_LOCK = threading.Lock()
_RECALL_LOCK = threading.Lock()
_PENDING_RECALL_CONTEXT = None
_ROUTE_STICK_TURNS = 3
POE_KNOWN_MODELS = [
    "Claude-Opus-4.6",
    "Claude-Sonnet-4.6",
    "Claude-Haiku-4.5",
    "GPT-5.1",
    "GPT-5-mini",
]

# Fix sys.path before importing helpers that may themselves import fiam.*.
_src_dir = str(_ROOT / "src")
_scripts_dir = str(_ROOT / "scripts")
sys.path = [p for p in sys.path if p not in {_src_dir, _scripts_dir}]
sys.path.insert(0, _src_dir)
sys.path.insert(1, _scripts_dir)

from fiam_lib.dashboard_annotation import (
    configure as _configure_annotation,
    annotation_state as _annotation_state,
    annotate_confirm as _annotate_confirm,
    annotate_edges as _annotate_edges,
    annotate_proposal as _annotate_proposal,
    annotate_request as _annotate_request,
)
from fiam_lib.app_markers import parse_app_cot, summarize_cot_steps


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
        root=_CONFIG.home_path if _CONFIG else _ROOT,
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


def _ring_today_path() -> Path:
    base = (_CONFIG.store_dir if _CONFIG else (_ROOT / "store")) / "wearable"
    base.mkdir(parents=True, exist_ok=True)
    return base / "ring_today.json"


def _favilla_ring_today() -> dict:
    path = _ring_today_path()
    if not path.exists():
        return {"ok": False, "error": "no ring data"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **data}


def _favilla_ring_sync(payload: dict) -> dict:
    """Accept ring data payload from sync_ring.py and write to store."""
    required = ("date",)
    for key in required:
        if not payload.get(key):
            raise ValueError(f"missing field: {key}")
    # Sanitize: only keep known numeric/string fields
    clean: dict = {
        "date": str(payload["date"])[:12],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    for field in ("current_hr", "resting_hr", "max_hr", "steps", "calories", "distance_m"):
        value = payload.get(field)
        if value is not None:
            try:
                clean[field] = int(value)
            except (TypeError, ValueError):
                pass
    hr_series = payload.get("hr_series")
    if isinstance(hr_series, list):
        clean["hr_series"] = [
            {"time": str(item.get("time", "")), "hr": int(item.get("hr", 0))}
            for item in hr_series[:300]
            if isinstance(item, dict)
        ]
    path = _ring_today_path()
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "date": clean["date"], "synced_at": clean["synced_at"]}


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


def _favilla_splash() -> dict:
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
        "channel": str(payload.get("channel") or "dispatch"),
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
        enriched.setdefault("channel", f"dispatch/{target}")
        _enqueue_wearable_message(enriched)
        logger.info("wearable queued target=%s type=%s", target, enriched.get("type", "message"))
    except Exception:
        logger.error("wearable dispatch failed target=%s payload=%r", target, payload, exc_info=True)


def _file_tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError as exc:
        return [f"(read failed: {exc})"]


def _pipeline_tail(n: int = 200) -> str:
    """Return current dashboard/runtime logs.

    The old console only tailed pipeline.log, which can be days stale while
    dashboard_server.py is the live service handling Favilla requests. Prefer
    dashboard_server.log when present, then include pipeline.log as secondary
    context so the Logs page reflects the ISP service that users are actually
    hitting.
    """
    chunks: list[str] = []
    per_file = max(20, n // 2)
    for name in ("dashboard_server.log", "pipeline.log"):
        path = _LOGS / name
        if not path.exists():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            mtime = "unknown"
        chunks.append(f"== {name} · mtime {mtime} ==")
        chunks.extend(_file_tail(path, per_file))
    if not chunks:
        return "(no logs found under logs/)"
    return "\n".join(chunks[-n:])


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


def _api_todo() -> list[dict]:
    """Pending delayed work from todo.jsonl."""
    if not _CONFIG:
        return []
    path = _CONFIG.todo_path
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
            at = datetime.fromisoformat(entry["at"])
            at = _CONFIG.ensure_timezone(at)
            if at > now:
                out.append({
                    "at": entry["at"],
                    "type": entry.get("type", "private"),
                    "reason": entry.get("reason", ""),
                })
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    out.sort(key=lambda e: e["at"])
    return out


def _api_health() -> dict:
    """Aggregate fault-tolerance signals — daemon, todo queue, budget."""
    status = _api_status()
    out: dict = {
        "daemon": status["daemon"],
        "pid": status["pid"],
        "events": status["events"],
        "last_processed": status["last_processed"],
        "missed_todos": 0,
        "failed_todos": 0,
        "pending_todos": 0,
        "retry_todos": 0,
        "budget": None,
        "budget_ok": True,
        "last_pipeline_error": None,
    }
    if not _CONFIG:
        return out

    # Pending + retry counts
    todo_path = _CONFIG.todo_path
    if todo_path.exists():
        for line in todo_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["pending_todos"] += 1
            if int(e.get("attempts", 0)) > 0:
                out["retry_todos"] += 1

    # Missed / failed archives
    for kind in ("missed", "failed"):
        p = _CONFIG.self_dir / f"todo_{kind}.jsonl"
        if p.exists():
            out[f"{kind}_todos"] = sum(
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
        "catalog": {
            family: _catalog_item_to_dict(item)
            for family, item in sorted((getattr(_CONFIG, "catalog", {}) or {}).items())
        },
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
                "receive_channels": list(plugin.receive_channels),
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


def _catalog_item_to_dict(item) -> dict:
    return {
        "provider": str(getattr(item, "provider", "") or ""),
        "model": str(getattr(item, "model", "") or ""),
        "fallbacks": list(getattr(item, "fallbacks", []) or []),
        "extended_thinking": bool(getattr(item, "extended_thinking", False)),
        "budget_tokens": int(getattr(item, "budget_tokens", 0) or 0),
    }


def _provider_api_settings(provider: str) -> dict:
    provider = (provider or "").strip().lower()
    if provider == "poe":
        return {"api_provider": "openai_compatible", "api_base_url": "https://api.poe.com/v1", "api_key_env": "POE_API_KEY"}
    if provider == "anthropic":
        return {"api_provider": "anthropic", "api_base_url": "https://api.anthropic.com/v1", "api_key_env": "ANTHROPIC_API_KEY"}
    if provider == "aistudio":
        return {"api_provider": "google_openai", "api_base_url": "", "api_key_env": "GEMINI_API_KEY"}
    if provider == "vertex":
        return {"api_provider": "vertex_openai", "api_base_url": "", "api_key_env": "GOOGLE_APPLICATION_CREDENTIALS"}
    return {"api_provider": provider or "openai_compatible", "api_base_url": "", "api_key_env": "FIAM_API_KEY"}


def _config_for_catalog_family(config, family: str):
    family = (family or "").strip().lower()
    item = (getattr(config, "catalog", {}) or {}).get(family)
    if not item:
        return config
    settings = _provider_api_settings(str(getattr(item, "provider", "") or ""))
    fallbacks = list(getattr(item, "fallbacks", []) or [])
    fallback_model = fallbacks[0] if fallbacks else ""
    fallback_provider = str(getattr(item, "provider", "") or "")
    return replace(
        config,
        api_provider=settings["api_provider"],
        api_model=str(getattr(item, "model", "") or getattr(config, "api_model", "")),
        api_base_url=settings["api_base_url"],
        api_key_env=settings["api_key_env"],
        api_fallback_provider=settings["api_provider"] if fallback_model else "",
        api_fallback_model=fallback_model,
        api_fallback_base_url=settings["api_base_url"] if fallback_model else "",
        api_fallback_key_env=settings["api_key_env"] if fallback_model else "",
    )


def _route_state_path() -> Path:
    return _CONFIG.home_path / ".model_route_state.json"


def _load_route_state() -> dict:
    if not _CONFIG:
        return {}
    path = _route_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_route_family(family: str, *, reason: str = "", turns: int = _ROUTE_STICK_TURNS) -> None:
    if not _CONFIG:
        return
    family = family.strip().lower()
    if not family:
        return
    path = _route_state_path()
    path.write_text(json.dumps({
        "family": family,
        "reason": reason,
        "remaining_turns": max(1, int(turns)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")


def _consume_route_family() -> str:
    if not _CONFIG:
        return ""
    state = _load_route_state()
    family = str(state.get("family") or "").strip().lower()
    remaining = int(state.get("remaining_turns") or 0)
    if not family or remaining <= 0:
        _route_state_path().unlink(missing_ok=True)
        return ""
    state["remaining_turns"] = remaining - 1
    if state["remaining_turns"] <= 0:
        _route_state_path().unlink(missing_ok=True)
    else:
        _route_state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    return family


def _runtime_for_family(family: str, *, fallback: str = "cc") -> str:
    family = (family or "").strip().lower()
    if family == "gemini":
        return "api"
    if family == "claude":
        return fallback if fallback in {"api", "cc"} else "cc"
    return fallback if fallback in {"api", "cc"} else "cc"


def _select_favilla_chat_route(text: str, attachments: list[dict] | None = None) -> dict:
    if attachments:
        return {"runtime": "cc", "family": "claude", "source": "attachments"}
    lowered = text.lower()
    api_token = r"(?<![a-z0-9])api(?![a-z0-9])|gemini"
    cc_token = r"(?<![a-z0-9])cc(?![a-z0-9])|claude\s*code"
    if re.search(rf"runtime\s*(=|:|：)\s*({api_token})|(换|切|切换|转|去|到|用|走).{{0,8}}({api_token})|({api_token}).{{0,8}}(模式|运行|runtime)", lowered):
        family = "gemini" if "gemini" in lowered else "claude"
        return {"runtime": "api", "family": family, "source": "user_text"}
    if re.search(rf"runtime\s*(=|:|：)\s*({cc_token})|(换|切|切换|转|去|到|用|走).{{0,8}}({cc_token})|({cc_token}).{{0,8}}(模式|运行|runtime)", lowered):
        return {"runtime": "cc", "family": "claude", "source": "user_text"}
    if re.search(r"(另一边|另一侧|另一端|切换过去|换过去|切过去)", lowered):
        return {"runtime": "api", "family": "claude", "source": "user_text"}
    family = _consume_route_family()
    if family:
        return {"runtime": _runtime_for_family(family), "family": family, "source": "route_state"}
    return {"runtime": "cc", "family": "claude", "source": "default"}


def _apply_route_from_result(result: dict) -> None:
    route = result.get("route") if isinstance(result.get("route"), dict) else None
    family = str((route or {}).get("family") or "").strip().lower()
    if family:
        _save_route_family(family, reason=str((route or {}).get("reason") or ""))


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


def _catalog_cache_path() -> Path:
    return _CONFIG.home_path / ".catalog_cache.json"


def _api_catalog_list() -> dict:
    if not _CONFIG:
        return {"catalog": {}, "cache": {}}
    cache = {}
    path = _catalog_cache_path()
    if path.exists():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    return {
        "catalog": {
            family: _catalog_item_to_dict(item)
            for family, item in sorted((getattr(_CONFIG, "catalog", {}) or {}).items())
        },
        "cache": cache if isinstance(cache, dict) else {},
        "providers": ["poe", "anthropic", "aistudio"],
        "families": ["claude", "gemini"],
    }


def _fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"catalog refresh failed ({exc.code}): {detail}") from exc


def _fetch_anthropic_models() -> list[str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing env var: ANTHROPIC_API_KEY")
    data = _fetch_json(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    rows = data.get("data") if isinstance(data, dict) else []
    models = [str(row.get("id") or "").strip() for row in rows if isinstance(row, dict) and row.get("id")]
    return sorted(set(models))


def _fetch_aistudio_models() -> list[str]:
    key = os.environ.get("GOOGLE_AI_STUDIO_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing env var: GOOGLE_AI_STUDIO_KEY or GEMINI_API_KEY")
    data = _fetch_json(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
    rows = data.get("models") if isinstance(data, dict) else []
    models: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        methods = row.get("supportedGenerationMethods") or []
        if methods and "generateContent" not in methods:
            continue
        name = str(row.get("name") or "").strip()
        if name.startswith("models/"):
            name = name[len("models/"):]
        if name:
            models.append(name)
    return sorted(set(models))


def _api_catalog_refresh(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in {"poe", "anthropic", "aistudio"}:
        raise ValueError("provider must be poe, anthropic, or aistudio")
    if provider == "poe":
        models = list(POE_KNOWN_MODELS)
    elif provider == "anthropic":
        models = _fetch_anthropic_models()
    else:
        models = _fetch_aistudio_models()
    cache = {}
    path = _catalog_cache_path()
    if path.exists():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    cache[provider] = {"models": models, "refreshed_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return {"ok": True, "provider": provider, "models": models}


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _catalog_section_lines(family: str, item: dict) -> list[str]:
    fallbacks = item.get("fallbacks") if isinstance(item.get("fallbacks"), list) else []
    fallback_text = ", ".join(_toml_quote(str(value)) for value in fallbacks)
    return [
        f"[catalog.{family}]",
        f"provider = {_toml_quote(str(item.get('provider') or ''))}",
        f"model = {_toml_quote(str(item.get('model') or ''))}",
        f"fallbacks = [{fallback_text}]",
        f"extended_thinking = {str(bool(item.get('extended_thinking'))).lower()}",
        f"budget_tokens = {int(item.get('budget_tokens') or 0)}",
    ]


def _replace_toml_section(text: str, section: str, new_lines: list[str]) -> str:
    lines = text.splitlines()
    header = f"[{section}]"
    start = -1
    end = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start = idx
            break
    if start >= 0:
        for idx in range(start + 1, len(lines)):
            stripped = lines[idx].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end = idx
                break
        out = lines[:start] + new_lines + lines[end:]
    else:
        out = list(lines)
        if out and out[-1].strip():
            out.append("")
        out.extend(new_lines)
    return "\n".join(out).rstrip() + "\n"


def _update_catalog(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    family = str(payload.get("family") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", family):
        raise ValueError("family must be a simple identifier")
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in {"poe", "anthropic", "aistudio", "vertex"}:
        raise ValueError("provider must be poe, anthropic, aistudio, or vertex")
    model = str(payload.get("model") or "").strip()
    if not model:
        raise ValueError("model is required")
    fallbacks_raw = payload.get("fallbacks", [])
    if isinstance(fallbacks_raw, str):
        fallbacks = [item.strip() for item in fallbacks_raw.split(",") if item.strip()]
    elif isinstance(fallbacks_raw, list):
        fallbacks = [str(item).strip() for item in fallbacks_raw if str(item).strip()]
    else:
        fallbacks = []
    item = {
        "provider": provider,
        "model": model,
        "fallbacks": fallbacks,
        "extended_thinking": bool(payload.get("extended_thinking")),
        "budget_tokens": int(payload.get("budget_tokens") or 0),
    }
    path = _CONFIG.toml_path
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(_replace_toml_section(text, f"catalog.{family}", _catalog_section_lines(family, item)), encoding="utf-8")
    from fiam.config import Catalog
    _CONFIG.catalog[family] = Catalog(**item)
    return {"ok": True, "family": family, "catalog": _catalog_item_to_dict(_CONFIG.catalog[family])}


def _api_capture(payload: dict) -> dict:
    """Forward a mobile/quick-capture event to the MQTT bus.

    The daemon subscribes to ``fiam/receive/<channel>`` and handles
    ingestion (embed + gorge + pool) through the unified Conductor.
    Dashboard no longer touches Pool directly — it's a pure HTTP→MQTT
    bridge for clients that can't speak MQTT (e.g. the Android app).

    Expected payload keys: text (required), channel (optional),
    url (optional), tags (optional list), kind/interaction/session_id/meta.
    """
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    raw_channel = str(payload.get("channel") or "chat").strip().lower()
    channel = raw_channel if raw_channel in {"chat", "studio", "stroll", "browser", "email", "schedule", "limen", "ring"} else "chat"
    surface = _surface_for_app_channel(channel, str(payload.get("surface") or "favilla"))
    url = (payload.get("url") or "").strip()
    meta = dict(payload.get("meta") or {})
    if raw_channel and raw_channel != channel:
        meta.setdefault("source_channel", raw_channel)
    meta.setdefault("surface", surface)
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
    receive_channel = channel
    if not is_receive_enabled(_CONFIG, receive_channel):
        raise RuntimeError(f"{receive_channel} plugin disabled")

    bus = _get_bus()
    if bus is None:
        raise RuntimeError("MQTT bus unavailable")
    ok = bus.publish_receive(receive_channel, {
        "text": text,
        "channel": channel,
        "surface": surface,
        "from_name": surface,
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


def _browser_snapshot_payload(payload: dict) -> tuple[str, dict]:
    from fiam.browser_bridge import browser_snapshot_meta, format_browser_snapshot

    text = format_browser_snapshot(payload)
    meta = browser_snapshot_meta(payload)
    return text, meta


# Reverse channel: AI / user pushes a wakeup; extension long-polls (short-polls).
_BROWSER_WAKEUP_QUEUE: list[dict] = []
_BROWSER_WAKEUP_LIMIT = 16

_ATRIUM_HTTP_BASE = os.environ.get("FIAM_ATRIUM_HTTP", "http://127.0.0.1:8767")


def _atrium_dispatch(capability: str, payload: dict, *, reason: str = "browser_wakeup") -> dict:
    """Best-effort POST to the Atrium tauri local HTTP bridge. Failures are
    swallowed (Atrium may not be running yet); caller treats as advisory."""
    token = os.environ.get("FIAM_INGEST_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "no token"}
    try:
        import urllib.request
        body = json.dumps({
            "capability": capability,
            "reason": reason,
            "payload": payload,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_ATRIUM_HTTP_BASE}/dispatch",
            data=body,
            headers={"Content-Type": "application/json", "X-Fiam-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — advisory only
        return {"ok": False, "error": str(exc)}


def _browser_wakeup_push(payload: dict) -> dict:
    url = str((payload or {}).get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("wakeup url must be http(s)")
    item = {
        "url": url[:2000],
        "reason": str((payload or {}).get("reason") or "ai_request")[:120],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    _BROWSER_WAKEUP_QUEUE.append(item)
    if len(_BROWSER_WAKEUP_QUEUE) > _BROWSER_WAKEUP_LIMIT:
        del _BROWSER_WAKEUP_QUEUE[: len(_BROWSER_WAKEUP_QUEUE) - _BROWSER_WAKEUP_LIMIT]
    # Best-effort: also poke Atrium to make sure firefox is running. Idempotent
    # — firefox single-instance dedupes; if Atrium is down, extension polling
    # still picks up the queue when the user has firefox open.
    spawn_target = str((payload or {}).get("spawn") or os.environ.get("FIAM_BROWSER_SPAWN_ALIAS") or "firefox-dev").strip().lower()
    atrium_result = None
    if spawn_target and spawn_target != "none":
        atrium_result = _atrium_dispatch("process.spawn", {"alias": spawn_target})
    return {"ok": True, "queued": item, "atrium": atrium_result}


def _browser_wakeup_pop_all() -> list[dict]:
    items = list(_BROWSER_WAKEUP_QUEUE)
    _BROWSER_WAKEUP_QUEUE.clear()
    return items


def _browser_screenshot_attachments(payload: dict) -> list[dict]:
    if not _CONFIG:
        return []
    import base64
    import hashlib

    items: list[tuple[str, str]] = []  # (kind, dataUrl)
    screenshot = payload.get("screenshot") if isinstance(payload.get("screenshot"), dict) else None
    if screenshot:
        data_url = str(screenshot.get("dataUrl") or screenshot.get("data_url") or "")
        if data_url:
            items.append(("browser_screenshot", data_url))
    video_frames = payload.get("videoFrames") if isinstance(payload.get("videoFrames"), list) else None
    if video_frames:
        for frame in video_frames[:5]:
            if not isinstance(frame, dict):
                continue
            data_url = str(frame.get("dataUrl") or frame.get("data_url") or "")
            if data_url:
                items.append(("browser_video_frame", data_url))
    if not items:
        return []
    out: list[dict] = []
    snapshot_payload = payload.get("snapshot")
    snapshot_url = str(snapshot_payload.get("url") or "") if isinstance(snapshot_payload, dict) else ""
    from fiam.store.objects import ObjectStore

    object_store = ObjectStore(_CONFIG.object_dir)
    manifest = _CONFIG.home_path / "uploads" / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    for kind, data_url in items:
        if not data_url.startswith("data:image/") or "," not in data_url:
            continue
        header, b64 = data_url.split(",", 1)
        mime = header[5:].split(";", 1)[0].lower()
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            continue
        if not raw or len(raw) > 6 * 1024 * 1024:
            continue
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
        digest = object_store.put_bytes(raw, suffix="")
        slug = "viewport" if kind == "browser_screenshot" else "frame"
        name = f"browser-{slug}-{digest[:12]}.{ext}"
        target = object_store.path_for_hash(digest, suffix="")
        record = {"object_hash": digest, "path": str(target), "name": name, "mime": mime, "size": len(raw)}
        with manifest.open("a", encoding="utf-8") as mf:
            mf.write(json.dumps({
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "reason": str((screenshot or {}).get("reason") or payload.get("reason") or ""),
                "url": snapshot_url,
                "sha256": digest,
                **record,
            }, ensure_ascii=False) + "\n")
        out.append(record)
    return out


def _recent_browser_action_trail(limit: int = 8) -> list[dict]:
    if not _CONFIG:
        return []
    try:
        from fiam.store.beat import read_beats
        recent = read_beats(_CONFIG.flow_path)[-120:]
    except Exception:
        return []
    trail: list[dict] = []
    action_re = re.compile(r"^browser_action\s+(\w+):\s+(\w+)\s+(\S+)\s+(.+)$")
    for beat in recent:
        if beat.actor != "ai" or beat.channel != "browser" or beat.kind != "action":
            continue
        text = str(beat.content or "")
        match = action_re.match(text)
        if not match:
            continue
        status, action, node_id, name = match.groups()
        trail.append({"action": action, "nodeId": node_id, "name": name, "result": status})
    return trail[-limit:]


def _action_signature(action: dict) -> tuple[str, str]:
    return (str(action.get("action") or "click").lower(), str(action.get("name") or action.get("nodeId") or "").casefold())


def _suppress_repeated_browser_actions(actions: list[dict], trail: list[dict]) -> list[dict]:
    if not actions or not trail:
        return actions
    latest = _action_signature(trail[-1])
    filtered = []
    for action in actions:
        sig = _action_signature(action)
        recent_count = sum(1 for item in trail[-6:] if _action_signature(item) == sig)
        if sig == latest or recent_count >= 2:
            continue
        filtered.append(action)
    return filtered


def _append_browser_flow_text(text: str) -> None:
    if not _CONFIG:
        return
    from fiam.runtime.turns import user_beat
    from fiam.store.beat import append_beat

    append_beat(_CONFIG.flow_path, user_beat(
        text,
        t=datetime.now(timezone.utc),
        channel="browser",
        user_name=getattr(_CONFIG, "user_name", "") or "zephyr",
        surface="atrium",
    ))


def _append_browser_action_flow(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam.store.beat import Beat, append_beat

    raw_action = payload.get("action")
    raw_result = payload.get("result")
    action: dict = raw_action if isinstance(raw_action, dict) else {}
    result: dict = raw_result if isinstance(raw_result, dict) else {}
    node_id = str(action.get("nodeId") or action.get("node") or "").strip()
    action_kind = str(action.get("action") or result.get("action") or "browser_action").strip()
    label = str(action.get("name") or result.get("label") or node_id or "target").strip()
    status = "ok" if result.get("ok", True) else "error"
    # Trail regex requires a non-whitespace nodeId token; use placeholder for actions without a node (goto, page-scroll).
    node_token = node_id or "_"
    text = f"browser_action {status}: {action_kind} {node_token} {label}".strip()
    append_beat(_CONFIG.flow_path, Beat(
        t=datetime.now(timezone.utc),
        actor="ai",
        channel="browser",
        kind="action",
        content=text,
        surface="atrium",
    ))
    try:
        from fiam_lib.computer_events import get_bus as _ce_bus
        _ce_bus().publish("act", {
            "surface": "b",
            "kind": action_kind,
            "label": label,
            "node": node_id,
            "ok": status == "ok",
            "text": text,
        })
    except Exception:
        logger.exception("computer bus publish (action) failed")
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None
    snapshot_result = None
    if snapshot:
        try:
            text_snapshot, _meta = _browser_snapshot_payload({"snapshot": snapshot})
            _append_browser_flow_text(text_snapshot)
            snapshot_result = {"recorded": True, "chars": len(text_snapshot)}
        except Exception as exc:
            snapshot_result = {"recorded": False, "error": str(exc)}
    return {"ok": True, "recorded": True, "snapshot": snapshot_result}


def _append_browser_ai_decision_flow(reply: str, actions: list[dict], done: dict | None, *, runtime: str) -> None:
    if not _CONFIG:
        return
    from fiam.store.beat import Beat, append_beat

    clean_reply = " ".join(str(reply or "").split())
    if done:
        kind = "done"
        text = f"browser_control_done: {done.get('reason') or 'done'}"
        if clean_reply:
            text = f"{text}\n{clean_reply}"
    elif actions:
        kind = "decision"
        action_bits = ", ".join(f"{item.get('action')} {item.get('nodeId')} {item.get('name')}".strip() for item in actions)
        text = f"browser_control_decision: {action_bits}"
        if clean_reply:
            text = f"{text}\n{clean_reply}"
    elif clean_reply:
        kind = "note"
        text = f"browser_control_note: {clean_reply}"
    else:
        return
    append_beat(_CONFIG.flow_path, Beat(
        t=datetime.now(timezone.utc),
        actor="ai",
        channel="browser",
        kind="message",
        content=text,
        runtime=runtime,
        surface="atrium",
    ))
    try:
        from fiam_lib.computer_events import get_bus as _ce_bus
        _ce_bus().publish("info", {
            "surface": "b",
            "kind": kind,
            "reply": clean_reply,
            "actions": [
                {"action": a.get("action"), "node": a.get("nodeId"), "name": a.get("name")}
                for a in (actions or [])
            ],
            "done": done or None,
            "text": text,
        })
    except Exception:
        logger.exception("computer bus publish (decision) failed")


def _browser_snapshot(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam.plugins import is_receive_enabled

    if not is_receive_enabled(_CONFIG, "browser"):
        raise RuntimeError("browser plugin disabled")
    text, meta = _browser_snapshot_payload(payload)
    bus = _get_bus()
    if bus is not None:
        ok = bus.publish_receive("browser", {
            "text": text,
            "channel": "browser",
            "surface": "atrium",
            "from_name": meta.get("browser") or "browser",
            "url": meta.get("url") or "",
            "tags": ["browser", "snapshot"],
            "kind": "browser_snapshot",
            "meta": meta,
            "t": datetime.now(timezone.utc),
        })
        if not ok:
            raise RuntimeError("publish rejected")
        return {"ok": True, "queued": True, "channel": "browser", "meta": meta, "chars": len(text)}
    _append_browser_flow_text(text)
    return {"ok": True, "queued": False, "recorded": True, "channel": "browser", "meta": meta, "chars": len(text)}


def _browser_ask(payload: dict) -> dict:
    question = str(payload.get("question") or payload.get("text") or "").strip()
    runtime = str(payload.get("runtime") or "api").strip().lower() or "api"
    record_turn = payload.get("record", True) is not False
    if runtime not in {"api", "cc"}:
        raise ValueError("browser runtime must be api or cc")
    from fiam.browser_bridge import browser_snapshot_meta, build_browser_runtime_text, extract_browser_actions, strip_browser_action_markers

    runtime_text = build_browser_runtime_text(question, payload)
    result = _favilla_chat_send({
        "text": runtime_text,
        "channel": "browser",
        "surface": "atrium",
        "runtime": runtime,
        "record_turn": record_turn,
    })
    cleaned_reply, browser_actions = extract_browser_actions(str(result.get("reply") or ""), payload)
    cleaned_segments = []
    for segment in result.get("segments") or []:
        if isinstance(segment, dict) and "text" in segment:
            segment = dict(segment)
            segment["text"] = strip_browser_action_markers(str(segment.get("text") or ""))
        cleaned_segments.append(segment)
    result["segments"] = cleaned_segments
    result["reply"] = cleaned_reply
    if browser_actions:
        result["browser_actions"] = browser_actions
    else:
        result["browser_actions"] = []
    result["browser"] = browser_snapshot_meta(payload)
    result["context_chars"] = len(runtime_text)
    return result


def _browser_control_tick(payload: dict) -> dict:
    runtime = str(payload.get("runtime") or "api").strip().lower() or "api"
    if runtime not in {"api", "cc"}:
        raise ValueError("browser runtime must be api or cc")
    from fiam.browser_bridge import browser_snapshot_meta, build_browser_control_text, extract_browser_actions, extract_browser_done, extract_and_save_browser_profile, media_policy_for_payload, strip_browser_action_markers

    payload = dict(payload)
    recent_trail = _recent_browser_action_trail()
    raw_payload_trail = payload.get("controlTrail")
    payload_trail = raw_payload_trail if isinstance(raw_payload_trail, list) else []
    payload["controlTrail"] = [*recent_trail, *payload_trail][-12:]
    runtime_text = build_browser_control_text(payload)
    media_policy = media_policy_for_payload(payload)
    # Gate attachments by per-host profile policy. "never" → drop before AI sees them.
    if media_policy["screenshot"] == "never":
        payload.pop("screenshot", None)
    if media_policy["videoFrames"] == "never":
        payload.pop("videoFrames", None)
    attachments = _browser_screenshot_attachments(payload)
    has_video_frames = bool(payload.get("videoFrames"))
    if attachments:
        if has_video_frames:
            runtime_text = f"{runtime_text}\n\n[browser_screenshot]\nViewport screenshot and {sum(1 for a in attachments if 'frame' in a.get('name',''))} sampled video frames are attached for visual reasoning."
        else:
            runtime_text = f"{runtime_text}\n\n[browser_screenshot]\nA current viewport screenshot is attached for visual reasoning."
    screenshot_error = ""
    send_text = runtime_text
    try:
        result = _favilla_chat_send({
            "text": send_text,
            "channel": "browser",
            "surface": "atrium",
            "runtime": runtime,
            "record_turn": False,
            "attachments": attachments,
        })
    except Exception as exc:
        if not attachments:
            raise
        screenshot_error = str(exc)[:240]
        logger.warning("browser screenshot tick failed; retrying without screenshot: %s", screenshot_error)
        runtime_text = build_browser_control_text(payload)
        send_text = f"{runtime_text}\n\n[browser_screenshot]\nA screenshot was attempted but unavailable for this tick; rely on DOM context."
        result = _favilla_chat_send({
            "text": send_text,
            "channel": "browser",
            "surface": "atrium",
            "runtime": runtime,
            "record_turn": False,
            "attachments": [],
        })
    raw_reply = str(result.get("reply") or "")
    cleaned_reply, saved_profiles = extract_and_save_browser_profile(raw_reply)
    cleaned_reply, browser_done = extract_browser_done(cleaned_reply)
    cleaned_reply, browser_actions = extract_browser_actions(cleaned_reply, payload)
    browser_actions = _suppress_repeated_browser_actions(browser_actions[:1], payload["controlTrail"])
    cleaned_segments = []
    for segment in result.get("segments") or []:
        if isinstance(segment, dict) and "text" in segment:
            segment = dict(segment)
            segment["text"] = strip_browser_action_markers(str(segment.get("text") or ""))
        cleaned_segments.append(segment)
    result["reply"] = cleaned_reply
    result["browser_actions"] = browser_actions
    result["browser_done"] = browser_done
    result["browser_profiles_saved"] = saved_profiles
    result["segments"] = cleaned_segments
    result["browser"] = browser_snapshot_meta(payload)
    result["context_chars"] = len(send_text)
    # Debug dump: write every prompt sent to AI for inspection.
    try:
        dump_dir = Path("logs/browser-prompts")
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dump_file = dump_dir / f"tick-{ts}.txt"
        dump_file.write_text(
            f"# url: {payload.get('snapshot', {}).get('url', '?')}\n"
            f"# reason: {payload.get('reason', '?')}\n"
            f"# attachments: {len(attachments)}\n"
            f"# context_chars: {len(send_text)}\n"
            f"---\n{send_text}\n---\n"
            f"# AI reply:\n{raw_reply}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    result["mode"] = "autonomous"
    result["screenshot_attempted"] = bool(attachments)
    result["screenshot_attached"] = bool(attachments) and not screenshot_error
    if screenshot_error:
        result["screenshot_fallback_error"] = screenshot_error
    _append_browser_ai_decision_flow(cleaned_reply, browser_actions, browser_done, runtime=runtime)
    return result


def _favilla_status() -> dict:
    status = _api_status()
    flow_count = 0
    thinking_count = 0
    interaction_count = 0
    if _CONFIG:
        try:
            from fiam.store.beat import read_beats
            for beat in read_beats(_CONFIG.flow_path):
                flow_count += 1
                meta = beat.meta or {}
                if beat.kind == "think":
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


def _favilla_message_units(text: str) -> int:
    units = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text or "")
    return len(units)


def _favilla_transcript_digest(channel: str, limit: int = 500) -> dict:
    messages = _favilla_transcript_load(channel=channel, limit=limit).get("messages") or []
    digest = {
        "turns": 0,
        "user_turns": 0,
        "ai_turns": 0,
        "words": 0,
        "user_words": 0,
        "ai_words": 0,
        "by_day": {},
    }
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        text = str(message.get("text") or "")
        units = _favilla_message_units(text)
        digest["turns"] += 1
        digest["words"] += units
        if role == "user":
            digest["user_turns"] += 1
            digest["user_words"] += units
        elif role == "ai":
            digest["ai_turns"] += 1
            digest["ai_words"] += units
        try:
            minute = int(message.get("t") or 0)
            dt = datetime.fromtimestamp(minute * 60, timezone.utc)
            day = dt.astimezone(_CONFIG.project_tz()).date().isoformat() if _CONFIG else dt.date().isoformat()
            bucket = digest["by_day"].setdefault(day, {"turns": 0, "user_words": 0, "ai_words": 0})
            bucket["turns"] += 1
            if role == "user":
                bucket["user_words"] += units
            elif role == "ai":
                bucket["ai_words"] += units
        except (TypeError, ValueError, OSError):
            pass
    return digest


def _favilla_plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _favilla_local_day_from_ms(value) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    seconds = number / 1000 if number > 10_000_000_000 else number
    try:
        dt = datetime.fromtimestamp(seconds, timezone.utc)
    except (OSError, ValueError):
        return None
    return dt.astimezone(_CONFIG.project_tz()).date().isoformat() if _CONFIG else dt.date().isoformat()


def _favilla_local_day_from_iso(value: str) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None and _CONFIG:
        dt = _CONFIG.ensure_timezone(dt)
    return dt.astimezone(_CONFIG.project_tz()).date().isoformat() if _CONFIG else dt.date().isoformat()


def _favilla_studio_digest(limit: int = 500) -> dict:
    try:
        state = _favilla_studio_load().get("state") or {}
    except Exception:
        state = {}
    timeline = state.get("timeline") if isinstance(state.get("timeline"), list) else []
    fallback_day = _favilla_local_day_from_iso(str(state.get("updated_at") or ""))
    digest = {
        "turns": 0,
        "user_turns": 0,
        "ai_turns": 0,
        "words": 0,
        "user_words": 0,
        "ai_words": 0,
        "by_day": {},
        "events": [],
    }
    for event in timeline[-max(1, min(5000, limit)):]:
        if not isinstance(event, dict):
            continue
        role = str(event.get("type") or "user")
        title = str(event.get("title") or "")
        summary = str(event.get("summary") or "")
        try:
            units = int(event.get("units") or 0)
        except (TypeError, ValueError):
            units = 0
        if units <= 0:
            units = _favilla_message_units(" ".join(part for part in (title, summary) if part)) or 1
        day = _favilla_local_day_from_ms(event.get("at")) or fallback_day
        digest["turns"] += 1
        digest["words"] += units
        if role == "ai":
            digest["ai_turns"] += 1
            digest["ai_words"] += units
        else:
            digest["user_turns"] += 1
            digest["user_words"] += units
        if day:
            bucket = digest["by_day"].setdefault(day, {"turns": 0, "user_words": 0, "ai_words": 0, "emoji": ""})
            bucket["turns"] += 1
            if role == "ai":
                bucket["ai_words"] += units
                bucket["emoji"] = "✨"
            else:
                bucket["user_words"] += units
                if not bucket.get("emoji"):
                    bucket["emoji"] = "✍️"
        digest["events"].append({
            "id": str(event.get("id") or ""),
            "title": title,
            "type": role,
            "kind": str(event.get("kind") or ""),
            "at": event.get("at"),
            "units": units,
            "fileId": event.get("fileId"),
            "fileName": event.get("fileName"),
            "location": event.get("location") if isinstance(event.get("location"), dict) else None,
        })
    content_units = _favilla_message_units(_favilla_plain_text(str(state.get("activeNoteContent") or "")))
    digest["content_units"] = content_units
    return digest


def _favilla_stroll_record_rows(limit: int = 5000) -> list[dict]:
    if not _CONFIG:
        return []
    path = _CONFIG.home_path / "stroll" / "spatial_records.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(20000, limit)):]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _favilla_location_name(row: dict, prefix: str) -> str:
    location = row.get("location") if isinstance(row.get("location"), dict) else row
    label = str(location.get("label") or "").strip()
    if label:
        return label[:48]
    place_kind = str(location.get("placeKind") or row.get("placeKind") or "unknown")
    try:
        lat = float(location.get("lat"))
        lng = float(location.get("lng"))
        return f"{prefix} {place_kind} · {lat:.4f},{lng:.4f}"
    except (TypeError, ValueError):
        return prefix


def _favilla_location_digest() -> list[dict]:
    buckets: dict[str, dict] = {}

    def add(key: str, name: str, *, units: int, emoji: str, latest_at=None, place_kind: str = "unknown") -> None:
        bucket = buckets.setdefault(key, {"name": name, "words": 0, "count": 0, "emoji": emoji, "latest_at": latest_at, "placeKind": place_kind})
        bucket["words"] += max(1, units)
        bucket["count"] += 1
        if emoji and not bucket.get("emoji"):
            bucket["emoji"] = emoji
        if latest_at and (not bucket.get("latest_at") or str(latest_at) > str(bucket.get("latest_at"))):
            bucket["latest_at"] = latest_at

    for row in _favilla_stroll_record_rows():
        try:
            lat = float(row.get("lat"))
            lng = float(row.get("lng"))
        except (TypeError, ValueError):
            continue
        key = str(row.get("cellId") or f"stroll:{lat:.4f}:{lng:.4f}")
        name = _favilla_location_name(row, "Stroll")
        units = _favilla_message_units(str(row.get("text") or "")) or 1
        add(key, name, units=units, emoji=str(row.get("emoji") or "📍"), latest_at=row.get("updatedAt") or row.get("createdAt"), place_kind=str(row.get("placeKind") or "unknown"))

    studio = _favilla_studio_digest()
    for event in studio.get("events") or []:
        if not isinstance(event, dict):
            continue
        location = event.get("location") if isinstance(event.get("location"), dict) else None
        if location:
            try:
                lat = float(location.get("lat"))
                lng = float(location.get("lng"))
                key = str(location.get("cellId") or f"studio:{lat:.4f}:{lng:.4f}")
            except (TypeError, ValueError):
                key = "studio"
        else:
            key = "studio"
        name = _favilla_location_name({"location": location or {}, "placeKind": (location or {}).get("placeKind", "studio")}, "Studio")
        add(key, name, units=int(event.get("units") or 1), emoji="✨" if event.get("type") == "ai" else "✍️", latest_at=event.get("at"), place_kind=str((location or {}).get("placeKind") or "studio"))

    total = sum(bucket["words"] for bucket in buckets.values()) or 1
    out = []
    for bucket in buckets.values():
        out.append({**bucket, "percent": max(5, round((bucket["words"] / total) * 100))})
    out.sort(key=lambda item: (int(item.get("words") or 0), int(item.get("count") or 0)), reverse=True)
    return out[:8]


def _favilla_dashboard() -> dict:
    ring = _favilla_ring_today()
    return {
        "ok": True,
        "status": _favilla_status(),
        "health": _api_health(),
        "events": _api_events(40),
        "todos": _api_todo()[:20],
        "chat": _favilla_transcript_digest("chat"),
        "stroll": _favilla_transcript_digest("stroll"),
        "studio": _favilla_studio_digest(),
        "locations": _favilla_location_digest(),
        "ring": ring if ring.get("ok") else None,
    }


def _favilla_studio_path() -> Path:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    return _CONFIG.home_path / "app_studio" / "state.json"


def _favilla_studio_load() -> dict:
    path = _favilla_studio_path()
    if not path.exists():
        return {"ok": True, "state": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"bad studio state: {exc}") from exc
    return {"ok": True, "state": state}


def _favilla_studio_save(payload: dict) -> dict:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    if not isinstance(state, dict):
        raise ValueError("missing studio state")
    files = state.get("files") if isinstance(state.get("files"), list) else []
    timeline = state.get("timeline") if isinstance(state.get("timeline"), list) else []
    file_contents = state.get("fileContents") if isinstance(state.get("fileContents"), dict) else {}
    clean_state = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": files[:500],
        "activeFileId": str(state.get("activeFileId") or ""),
        "activeNoteContent": str(state.get("activeNoteContent") or "")[:1024 * 1024],
        "fileContents": {str(key): str(value)[:1024 * 1024] for key, value in list(file_contents.items())[:500]},
        "timeline": timeline[-500:],
    }
    path = _favilla_studio_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean_state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return {"ok": True, "state": clean_state}


_STUDIO_EDIT_OPS = {"replace", "insert_after", "insert_before", "delete", "append", "prepend"}


def _studio_edit_prompt(payload: dict) -> str:
    instruction = str(payload.get("instruction") or "").strip()
    content = str(payload.get("content") or "")[:120_000]
    file_name = str(payload.get("fileName") or payload.get("file_id") or payload.get("fileId") or "current note")
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    return "\n".join([
        "[studio_edit_contract]",
        "Return only JSON. Do not use XML, Markdown fences, or prose outside JSON.",
        "Produce an edit script, not a rewritten full document. Prefer the smallest command that preserves the user's existing text.",
        "Allowed commands:",
        '{"op":"replace","target":"exact existing substring","text":"replacement"}',
        '{"op":"insert_after","target":"exact existing substring","text":"inserted text"}',
        '{"op":"insert_before","target":"exact existing substring","text":"inserted text"}',
        '{"op":"delete","target":"exact existing substring"}',
        '{"op":"append","text":"text or HTML to add at the end"}',
        '{"op":"prepend","text":"text or HTML to add at the beginning"}',
        'Example: changing AAA into AABA is {"op":"replace","target":"AAA","text":"AABA"}, not delete+append.',
        'For new full HTML blocks, include data-author="AI" on the new block when natural.',
        "JSON shape: {\"summary\":\"short visible summary\",\"author\":\"AI\",\"edits\":[...commands...]}",
        "",
        f"[file]\n{file_name}",
        f"[location]\n{json.dumps(location, ensure_ascii=False)}",
        f"[instruction]\n{instruction}",
        f"[document_html]\n{content}",
    ])


def _clean_json_text(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return raw


def _sanitize_studio_edits(raw_edits) -> list[dict]:
    if not isinstance(raw_edits, list):
        return []
    edits: list[dict] = []
    aliases = {"remove": "delete", "del": "delete", "insertAfter": "insert_after", "insertBefore": "insert_before"}
    for item in raw_edits[:80]:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or item.get("command") or "").strip()
        op = aliases.get(op, op).lower().replace("-", "_")
        if op not in _STUDIO_EDIT_OPS:
            continue
        target = str(item.get("target") or item.get("find") or item.get("search") or "")[:20_000]
        text = str(item.get("text") if item.get("text") is not None else item.get("replacement") if item.get("replacement") is not None else item.get("insert") if item.get("insert") is not None else "")[:40_000]
        if op in {"replace", "insert_after", "insert_before", "delete"} and not target:
            continue
        if op in {"replace", "insert_after", "insert_before", "append", "prepend"} and not text:
            continue
        clean = {"op": op}
        if target:
            clean["target"] = target
        if text:
            clean["text"] = text
        note = str(item.get("note") or item.get("reason") or "").strip()
        if note:
            clean["note"] = note[:240]
        edits.append(clean)
    return edits


def _parse_studio_edit_response(text: str) -> dict:
    try:
        data = json.loads(_clean_json_text(text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"studio edit returned non-json: {text[:240]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("studio edit JSON must be an object")
    edits = _sanitize_studio_edits(data.get("edits") or data.get("operations") or data.get("commands"))
    if not edits:
        raise RuntimeError("studio edit returned no usable edit commands")
    return {
        "summary": str(data.get("summary") or data.get("title") or "Prepared edit script")[:500],
        "author": str(data.get("author") or "AI")[:80],
        "edits": edits,
    }


def _run_api_studio_edit(prompt: str) -> dict:
    from fiam.runtime.api import OpenAICompatibleClient
    from fiam.runtime.prompt import build_api_messages

    client = OpenAICompatibleClient.from_config(_CONFIG)
    messages = build_api_messages(
        _CONFIG,
        prompt,
        channel="studio",
        include_recall=True,
        extra_context=_app_runtime_context(),
    )
    completion = client.complete(
        messages=messages,
        model=_CONFIG.api_model,
        temperature=0.2,
        max_tokens=max(1200, min(4096, _CONFIG.api_max_tokens)),
        tools=None,
    )
    parsed = _parse_studio_edit_response(completion.text)
    return {**parsed, "runtime": "api", "model": completion.model, "usage": completion.usage}


def _run_cc_studio_edit(prompt: str) -> dict:
    import subprocess

    warm_system_context, warm_user_prompt = _build_cc_favilla_prompt_parts(
        prompt,
        channel="studio",
        recall_context=None,
        warm=True,
    )

    def parse_stream_result(result) -> dict:
        data, _action_events, _thinking_events = _parse_cc_stream(result.stdout or "")
        if not data:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            raise RuntimeError(f"bad claude stream-json: {detail}")
        if bool(data.get("is_error")) or result.returncode != 0:
            detail = data.get("error") or data.get("result") or result.stderr or result.stdout or "claude failed"
            raise RuntimeError(str(detail).strip()[:500])
        parsed = _parse_studio_edit_response(str(data.get("result") or ""))
        return {**parsed, "runtime": "cc", "session_id": str(data.get("session_id") or ""), "cost_usd": data.get("total_cost_usd", 0)}

    with _CC_RUNTIME_LOCK:
        if _cc_channel_transport_enabled():
            session = _load_app_active_session()
            resume_id = str((session or {}).get("session_id") or "").strip()
            try:
                result = _run_cc_channel_turn_result_locked(
                    warm_user_prompt,
                    warm_system_context,
                    resume_session_id=resume_id,
                    max_turns=4,
                )
            except RuntimeError as exc:
                raise RuntimeError(f"claude studio channel failed: {str(exc).strip()[:500]}") from exc
            parsed = parse_stream_result(result)
            session_id = str(parsed.get("session_id") or "").strip()
            if session_id:
                _save_app_active_session(session_id)
            return parsed
        if _cc_warm_enabled():
            try:
                return parse_stream_result(_run_cc_warm_turn_result_locked(warm_user_prompt, warm_system_context))
            except RuntimeError as exc:
                if str(exc).startswith(_CC_WARM_FAILURE_PREFIX):
                    _cc_warm_shutdown_locked(str(exc), scrub=True)
                else:
                    raise
        if _CC_WARM_RUNNER:
            _cc_warm_shutdown_locked("cold studio fallback")

        system_context, user_prompt = _build_cc_favilla_prompt_parts(
            prompt,
            channel="studio",
            recall_context=None,
            warm=False,
        )
        _cc_scrub_hook_transcript(_load_app_active_session())

        command = [
            "claude", "-p", user_prompt,
            "--output-format", "json",
            "--max-turns", "4",
            "--setting-sources", "user,project,local",
            "--exclude-dynamic-system-prompt-sections",
            "--permission-mode", "bypassPermissions",
        ]
        if system_context:
            command.extend(["--append-system-prompt", system_context])
        if _CONFIG.cc_model:
            command.extend(["--model", _CONFIG.cc_model])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180, cwd=str(_CONFIG.home_path))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("claude studio edit timeout") from exc
        except FileNotFoundError as exc:
            raise RuntimeError("claude not found on server PATH") from exc
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(f"bad claude json: {detail}") from exc
    if bool(data.get("is_error")) or result.returncode != 0:
        detail = data.get("error") or data.get("result") or result.stderr or result.stdout or "claude failed"
        raise RuntimeError(str(detail).strip()[:500])
    parsed = _parse_studio_edit_response(str(data.get("result") or ""))
    return {**parsed, "runtime": "cc", "session_id": str(data.get("session_id") or ""), "cost_usd": data.get("total_cost_usd", 0)}


def _run_studio_edit_model(prompt: str, runtime: str) -> dict:
    if runtime == "cc":
        return _run_cc_studio_edit(prompt)
    return _run_api_studio_edit(prompt)


def _favilla_studio_edit(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    instruction = str(payload.get("instruction") or "").strip()
    content = str(payload.get("content") or "")
    if not instruction:
        raise ValueError("missing instruction")
    if not content:
        raise ValueError("missing content")
    default_runtime = getattr(_CONFIG, "app_default_runtime", "auto") or "auto"
    runtime = str(payload.get("runtime") or default_runtime).strip().lower() or "auto"
    if runtime == "auto":
        runtime = "api"
    if runtime not in {"api", "cc"}:
        raise ValueError("Studio edit runtime must be auto, api, or cc")
    prompt = _studio_edit_prompt(payload)
    result = _run_studio_edit_model(prompt, runtime)
    _append_transcript("studio", {
        "role": "user",
        "text": f"Studio edit request: {instruction[:500]}",
        "runtime": runtime,
    })
    _append_transcript("studio", {
        "role": "ai",
        "text": f"Studio edit script: {result.get('summary', '')}",
        "runtime": runtime,
    })
    return {"ok": True, **result}


def _select_favilla_chat_runtime(text: str, attachments: list[dict] | None = None) -> str:
    return str(_select_favilla_chat_route(text, attachments).get("runtime") or "cc")


def _transcript_source(channel: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", (channel or "chat").strip().lower()).strip("_")
    return clean or "chat"


def _surface_for_app_channel(channel: str, explicit: str = "") -> str:
    surface = (explicit or "").strip().lower()
    if surface:
        if surface in {"app", "favilla"} or surface.startswith("favilla."):
            return "favilla"
        if surface == "atrium" or surface.startswith("atrium."):
            return "atrium"
        return surface
    channel = (channel or "chat").strip().lower() or "chat"
    if channel == "browser":
        return "atrium"
    if channel in {"studio", "stroll", "chat"}:
        return "favilla"
    return "favilla"


def _transcript_path(channel: str = "chat") -> Path:
    return _CONFIG.home_path / "transcript" / f"{_transcript_source(channel)}.jsonl"

def _history_attachments(attachments: list[dict]) -> list[dict]:
    out = []
    for att in attachments:
        mime = str(att.get("mime") or "")
        out.append({
            "kind": "image" if mime.startswith("image/") else "file",
            "name": str(att.get("name") or Path(str(att.get("path") or "file")).name),
            "object_hash": str(att.get("object_hash") or ""),
            "mime": mime,
            "size": att.get("size"),
        })
    return out


def _append_transcript(channel: str, message: dict) -> dict:
    from fiam.turn import UiHistoryStore

    rows = UiHistoryStore(_CONFIG.home_path).append_rows(channel, (message,))
    return rows[0] if rows else {}


def _persist_favilla_ai_transcript(channel: str, result: dict, runtime: str) -> dict:
    """Persist the final AI chat turn before trying to deliver it to a client.

    SSE delivery is best-effort; transcript is the source of truth. Keeping
    this as a small helper prevents stream and non-stream paths from drifting.
    """
    return _append_transcript(channel, {
        "role": "ai",
        "text": result.get("reply", ""),
        "raw_text": result.get("raw_reply", result.get("reply", "")),
        "runtime": result.get("runtime") or runtime,
        "thinking": result.get("thoughts") or [],
        "thinkingLocked": bool(result.get("thoughts_locked")),
        "segments": result.get("segments") or [],
        "attachments": result.get("attachments") or [],
        "hold": result.get("hold"),
        "tool_calls_summary": result.get("tool_calls_summary") or [],
        "actions": result.get("actions_list") or [],
        "metrics": result.get("metrics") or {},
        "meta": {k: v for k, v in {"trace": result.get("trace"), "surface": result.get("surface")}.items() if v},
        "presence": _current_presence(actor="ai", channel=channel),
    })


def _trace_summary(turn_id: str, request_id: str, *, runtime: str = "") -> dict:
    return {k: v for k, v in {
        "turn_id": turn_id,
        "request_id": request_id,
        "runtime": runtime,
        "trace_file": "store/turn_traces.jsonl",
    }.items() if v}


def _append_dashboard_trace_phase(
    *,
    turn_id: str,
    request_id: str,
    channel: str,
    surface: str,
    phase: str,
    started_at: datetime,
    status: str = "ok",
    error: str = "",
    refs: dict | None = None,
) -> None:
    if not _CONFIG:
        return
    from fiam.turn import TurnTraceRow, TurnTraceStore

    ended_at = datetime.now(timezone.utc)
    TurnTraceStore(_CONFIG.store_dir / "turn_traces.jsonl").append(TurnTraceRow(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase=phase,
        status=status if status in {"ok", "error", "skipped"} else "error",
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_ms=max(0, int((ended_at - started_at).total_seconds() * 1000)),
        error=error,
        refs=refs or {},
    ))


def _api_debug_trace(query: dict) -> dict:
    if not _CONFIG:
        return {"error": "config not loaded"}
    try:
        limit = max(1, min(1000, int(query.get("limit", "200") or "200")))
    except ValueError:
        limit = 200
    path = _CONFIG.store_dir / "turn_traces.jsonl"
    filters = {
        key: str(query.get(key) or "").strip()
        for key in ("turn_id", "request_id", "session_id", "phase", "status")
        if str(query.get(key) or "").strip()
    }
    if not path.exists():
        return {"rows": [], "total": 0, "returned": 0, "path": "store/turn_traces.jsonl"}
    rows: list[dict] = []
    total = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        total += 1
        if any(str(row.get(key) or "") != value for key, value in filters.items()):
            continue
        rows.append(row)
    returned_rows = rows[-limit:]
    return {
        "rows": returned_rows,
        "total": total,
        "returned": min(len(rows), limit),
        "path": "store/turn_traces.jsonl",
        "filters": filters,
        "summary": _debug_trace_aggregate(rows),
    }

def _debug_trace_aggregate(rows: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    failures: list[dict] = []
    retry_phases = 0
    total_duration_ms = 0
    slowest: list[dict] = []
    for row in rows:
        status = str(row.get("status") or "")
        phase = str(row.get("phase") or "")
        by_status[status] = by_status.get(status, 0) + 1
        by_phase[phase] = by_phase.get(phase, 0) + 1
        try:
            duration = int(row.get("duration_ms") or 0)
        except (TypeError, ValueError):
            duration = 0
        total_duration_ms += max(0, duration)
        if duration:
            slowest.append({"phase": phase, "duration_ms": duration, "turn_id": row.get("turn_id") or ""})
        if status == "error":
            failures.append({
                "phase": phase,
                "turn_id": row.get("turn_id") or "",
                "request_id": row.get("request_id") or "",
                "error": str(row.get("error") or "")[:300],
                "refs": row.get("refs") if isinstance(row.get("refs"), dict) else {},
            })
        if phase.endswith(".failed") or phase in {"queue.failed", "memory.failed", "dispatch.failed"}:
            retry_phases += 1
    return {
        "by_status": by_status,
        "by_phase": by_phase,
        "failures": failures[-20:],
        "retry_phases": retry_phases,
        "total_duration_ms": total_duration_ms,
        "slowest": sorted(slowest, key=lambda item: int(item.get("duration_ms") or 0), reverse=True)[:10],
    }


def _api_timeline(query: dict) -> dict:
    if not _CONFIG:
        return {"error": "config not loaded"}
    try:
        limit = max(1, min(100, int(query.get("limit", "20") or "20")))
    except ValueError:
        limit = 20
    q = str(query.get("q") or query.get("query") or "").strip()
    from fiam.turn import MemoryTimelineStore

    records = MemoryTimelineStore(_CONFIG.timeline_dir).query(q, limit=limit)
    return {
        "records": records,
        "returned": len(records),
        "query": q,
        "path": "store/timeline",
    }


def _api_objects(query: dict) -> dict:
    if not _CONFIG:
        return {"error": "config not loaded"}
    from fiam.store.object_catalog import ObjectCatalog

    try:
        limit = max(1, min(100, int(query.get("limit", "20") or "20")))
    except ValueError:
        limit = 20
    q = str(query.get("q") or query.get("query") or "")
    token = str(query.get("token") or "")
    catalog = ObjectCatalog.from_config(_CONFIG)
    records = [record.to_dict() for record in catalog.search(q, limit=limit)]
    payload: dict[str, object] = {
        "records": records,
        "returned": len(records),
        "query": q,
    }
    if token:
        payload["object_hash"] = catalog.resolve_token(token)
        payload["token"] = token
    return payload


_OBJECT_TOKEN_RE = re.compile(r"\bobj:([0-9a-fA-F]{8,64})\b")


def _object_path_for_hash(object_hash: str) -> Path | None:
    if not _CONFIG:
        return None
    from fiam.store.objects import ObjectStore

    clean = "".join(ch for ch in str(object_hash or "").lower() if ch in "0123456789abcdef")
    if len(clean) != 64:
        return None
    store = ObjectStore(_CONFIG.object_dir)
    for suffix in ("", ".txt"):
        try:
            path = store.path_for_hash(clean, suffix=suffix)
        except ValueError:
            continue
        if path.exists() and path.is_file():
            return path
    return None


def _resolve_object_hash(token: str) -> str:
    if not _CONFIG:
        return ""
    raw = str(token or "").strip().lower()
    if raw.startswith("obj:"):
        raw = raw[4:]
    raw = "".join(ch for ch in raw if ch in "0123456789abcdef")
    if len(raw) == 64 and _object_path_for_hash(raw):
        return raw
    if len(raw) < 8:
        return ""
    try:
        from fiam.store.object_catalog import ObjectCatalog

        resolved = ObjectCatalog.from_config(_CONFIG).resolve_token(f"obj:{raw}")
        if resolved and _object_path_for_hash(resolved):
            return resolved
    except Exception:
        pass
    root = _CONFIG.object_dir
    if not root.exists():
        return ""
    matches: set[str] = set()
    prefix_dir = root / raw[:2]
    candidates = prefix_dir.glob(f"{raw}*") if prefix_dir.exists() else root.glob(f"**/{raw}*")
    for path in candidates:
        stem = path.name.split(".", 1)[0].lower()
        if len(stem) == 64 and stem.startswith(raw):
            matches.add(stem)
    return next(iter(matches)) if len(matches) == 1 else ""


def _object_metadata(object_hash: str) -> dict:
    if not _CONFIG:
        return {}
    try:
        from fiam.store.object_catalog import ObjectCatalog

        records = ObjectCatalog.from_config(_CONFIG).search(object_hash, limit=20)
        for record in records:
            data = record.to_dict()
            if data.get("object_hash") == object_hash:
                return data
    except Exception:
        pass
    return {}


def _object_attachment_for_hash(object_hash: str) -> dict | None:
    path = _object_path_for_hash(object_hash)
    if not path:
        return None
    meta = _object_metadata(object_hash)
    name = str(meta.get("name") or "").strip() or (f"object-{object_hash[:12]}.txt" if path.suffix == ".txt" else f"object-{object_hash[:12]}")
    mime = str(meta.get("mime") or "").strip() or ("text/plain" if path.suffix == ".txt" else "application/octet-stream")
    return {
        "kind": "image" if mime.startswith("image/") else "file",
        "name": Path(name).name,
        "object_hash": object_hash,
        "mime": mime,
        "size": int(meta.get("size") or path.stat().st_size),
    }


def _extract_object_attachments(text: str) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for match in _OBJECT_TOKEN_RE.finditer(str(text or "")):
        object_hash = _resolve_object_hash(match.group(0))
        if not object_hash or object_hash in seen:
            continue
        attachment = _object_attachment_for_hash(object_hash)
        if attachment:
            out.append(attachment)
            seen.add(object_hash)
    return out


def _merge_result_object_attachments(result: dict) -> None:
    existing = result.get("attachments") if isinstance(result.get("attachments"), list) else []
    by_hash: dict[str, dict] = {}
    for item in existing:
        if isinstance(item, dict):
            object_hash = str(item.get("object_hash") or "")
            if object_hash:
                by_hash[object_hash] = item
    for item in _extract_object_attachments(str(result.get("reply") or result.get("raw_reply") or "")):
        by_hash.setdefault(str(item.get("object_hash") or ""), item)
    if by_hash:
        result["attachments"] = list(by_hash.values())


def _favilla_object_download(token: str) -> tuple[bytes, str, str]:
    object_hash = _resolve_object_hash(token)
    if not object_hash:
        raise FileNotFoundError("object not found")
    path = _object_path_for_hash(object_hash)
    if not path:
        raise FileNotFoundError("object not found")
    attachment = _object_attachment_for_hash(object_hash) or {}
    name = str(attachment.get("name") or path.name)
    mime = str(attachment.get("mime") or "application/octet-stream")
    return path.read_bytes(), name, mime


def _normalize_metrics(
    *,
    runtime: str,
    model: str,
    usage: dict | None,
    latency_ms: int | None = None,
    cost_usd=None,
) -> dict:
    """Project provider-specific usage into a unified metrics dict.

    Handles OpenAI (prompt_tokens/completion_tokens, prompt_tokens_details.cached_tokens),
    Anthropic (input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens),
    and DeepSeek (prompt_cache_hit_tokens) usage shapes. Unknown shape → store as raw_usage only.
    """
    out: dict = {
        "runtime": runtime,
        "model": str(model or ""),
    }
    if latency_ms is not None:
        out["latency_ms"] = int(latency_ms)
    if cost_usd is not None:
        try:
            out["cost_usd"] = float(cost_usd)
        except (TypeError, ValueError):
            pass
    if isinstance(usage, dict) and usage:
        out["raw_usage"] = usage
        # Unify token names
        tok_in = usage.get("prompt_tokens") or usage.get("input_tokens")
        tok_out = usage.get("completion_tokens") or usage.get("output_tokens")
        if tok_in is not None:
            try: out["tokens_in"] = int(tok_in)
            except (TypeError, ValueError): pass
        if tok_out is not None:
            try: out["tokens_out"] = int(tok_out)
            except (TypeError, ValueError): pass
        # Cache fields (provider-specific)
        cache_read = (
            usage.get("cache_read_input_tokens")
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or usage.get("prompt_cache_hit_tokens")
        )
        if cache_read is not None:
            try: out["tokens_cache_read"] = int(cache_read)
            except (TypeError, ValueError): pass
        cache_creation = usage.get("cache_creation_input_tokens")
        if cache_creation is not None:
            try: out["tokens_cache_creation"] = int(cache_creation)
            except (TypeError, ValueError): pass
    return out


def _current_presence(actor: str = "", channel: str = "") -> dict:
    """Best-effort snapshot of user/ai status + actor/channel for transcript record."""
    out: dict = {}
    if actor:
        out["actor"] = actor
    if channel:
        out["channel"] = channel
    try:
        if _CONFIG and _CONFIG.ai_state_path.exists():
            data = json.loads(_CONFIG.ai_state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("ai_status"):
                    out["ai"] = str(data.get("ai_status"))
                if data.get("user_status"):
                    out["user"] = str(data.get("user_status"))
    except (OSError, json.JSONDecodeError):
        pass
    return out


def _favilla_transcript_load(channel: str = "chat", limit: int = 200) -> dict:
    path = _transcript_path(channel)
    if not path.exists():
        return {"ok": True, "messages": []}
    cap = max(1, min(1000, limit))
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines()[-cap:]:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"ok": True, "messages": messages}


def _favilla_transcript_append(payload: dict) -> dict:
    channel = str(payload.get("channel") or "chat")
    role = str(payload.get("role") or "user")
    if role not in {"user", "ai"}:
        raise ValueError("role must be user or ai")
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    safe_attachments = _validate_app_attachments(attachments)
    text_in = str(payload.get("text") or "")
    record = _append_transcript(channel, {
        "role": role,
        "text": text_in,
        "raw_text": str(payload.get("raw_text") or text_in),
        "attachments": _history_attachments(safe_attachments),
    })
    return {"ok": True, "message": record}


def _favilla_stroll_nearby(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib.stroll_store import list_spatial_records

    current = payload.get("current") if isinstance(payload.get("current"), dict) else payload
    radius_m = float(payload.get("radiusM") or payload.get("radius_m") or 50)
    changed_since = int(payload.get("changedSince") or payload.get("changed_since") or 0)
    return list_spatial_records(_CONFIG, current=current, radius_m=radius_m, changed_since=changed_since)


def _favilla_stroll_record(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib.stroll_store import add_spatial_record
    from fiam_lib.stroll_events import get_bus

    record = add_spatial_record(_CONFIG, payload)
    text = str(record.get("text") or "").strip()
    if text:
        _append_transcript("stroll", {
            "role": "user" if record.get("origin") == "user" else "ai",
            "text": text,
            "raw_text": text,
        })
    get_bus().publish("record", record)
    return {"ok": True, "record": record}


def _favilla_stroll_action_result(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib.stroll_store import record_action_result
    from fiam_lib.stroll_events import get_bus

    record = record_action_result(_CONFIG, payload)
    get_bus().publish("action_result", record)
    return {"ok": True, "action": record}


def _favilla_stroll_state_start(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib import stroll_state

    data = stroll_state.start(_CONFIG, payload or {})
    return {"ok": True, "state": data}


def _favilla_stroll_state_heartbeat(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib import stroll_state

    data = stroll_state.heartbeat(_CONFIG, payload or {})
    return {"ok": True, "state": data}


def _favilla_stroll_state_stop(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib import stroll_state

    reason = str((payload or {}).get("reason") or "user_stop").strip() or "user_stop"
    data = stroll_state.stop(_CONFIG, reason=reason)
    return {"ok": True, "state": data}


def _favilla_stroll_state_status() -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    from fiam_lib import stroll_state

    return {"ok": True, "state": stroll_state.get_state(_CONFIG)}


def _format_sse(ev: dict) -> bytes:
    """Render one bus event as an SSE frame: id/event/data lines + blank line."""
    payload = json.dumps(ev.get("data") or {}, ensure_ascii=False)
    out = f"id: {ev['id']}\nevent: {ev.get('event') or 'message'}\ndata: {payload}\n\n"
    return out.encode("utf-8")


_STROLL_TICK_CHECK_INTERVAL_S = 10.0


def _stroll_tick_loop() -> None:
    """Background tick: while a stroll is active, wake AI every interval_seconds."""
    import time as _time

    from fiam_lib import stroll_state

    while True:
        try:
            _time.sleep(_STROLL_TICK_CHECK_INTERVAL_S)
            if not _CONFIG:
                continue
            state = stroll_state.get_state(_CONFIG)
            if not state.get("active"):
                continue
            interval = float(state.get("interval_seconds") or stroll_state.DEFAULT_TICK_INTERVAL_S)
            last_tick = float(state.get("last_tick_at") or 0.0)
            now = _time.time()
            if now - last_tick < interval:
                continue
            location = state.get("location") if isinstance(state.get("location"), dict) else None
            stroll_ctx: dict = {}
            if location and "lat" in location and "lng" in location:
                stroll_ctx["current"] = {
                    "lat": location["lat"],
                    "lng": location["lng"],
                    "accuracy": location.get("accuracy"),
                }
            payload = {
                "channel": "stroll",
                "runtime": "api",
                "text": f"[stroll_tick] 距上次 tick {int(now - last_tick)}s。看一下当前镜头/周围，决定要不要拍照、记录或给屏幕换图。",
                "stroll_context": stroll_ctx,
            }
            try:
                _favilla_chat_send(payload)
            except Exception:
                logger.exception("stroll tick send failed")
            stroll_state.mark_tick(_CONFIG, at=now)
        except Exception:
            logger.exception("stroll tick loop error")


def _start_stroll_tick_thread() -> None:
    import threading as _threading

    t = _threading.Thread(target=_stroll_tick_loop, name="stroll-tick", daemon=True)
    t.start()


def _validate_app_attachments(attachments: list) -> list[dict]:
    safe_attachments = []
    from fiam.store.objects import ObjectStore

    object_store = ObjectStore(_CONFIG.object_dir)
    for att in attachments:
        if not isinstance(att, dict):
            continue
        object_hash = "".join(ch for ch in str(att.get("object_hash") or "").lower() if ch in "0123456789abcdef")
        if len(object_hash) != 64:
            continue
        try:
            resolved = object_store.path_for_hash(object_hash, suffix="").resolve()
        except ValueError:
            continue
        if not resolved.exists():
            continue
        safe_attachments.append({
            "object_hash": object_hash,
            "path": str(resolved),
            "name": str(att.get("name") or resolved.name),
            "mime": str(att.get("mime") or ""),
            "size": int(att.get("size") or resolved.stat().st_size),
        })
    return safe_attachments


def _apply_app_control_markers(
    reply: str,
    *,
    channel: str,
    runtime: str,
    user_text: str,
    attachments: list | None = None,
) -> tuple[str, int, str, dict | None]:
    """Apply hold + control markers from an AI reply.

    Returns ``(cleaned_reply, queued_todos, hold_reason, route)`` without
    committing marker side effects. TurnCommit owns those writes.
    """
    interpretation = _interpret_app_control_markers(reply)
    return (
        interpretation.visible_reply,
        len(interpretation.todo_changes),
        interpretation.hold_reason,
        interpretation.route_hint,
    )


def _interpret_app_control_markers(reply: str):
    from fiam.turn import MarkerInterpreter

    return MarkerInterpreter(object_resolver=_object_token_resolver()).interpret(reply or "")


def _app_hold_payload(interpretation) -> dict | None:
    if interpretation.hold_request is None:
        return None
    return {
        "reason": interpretation.hold_reason,
        "status": interpretation.hold_status,
    }


def _hold_reroll_prompt(original_text: str, reason: str) -> str:
    parts = [
        "[hold_reroll]",
        "The previous assistant attempt used <hold>, so it was not sent to the user.",
        "Answer the same user turn again now. Do not repeat the held draft.",
    ]
    if reason.strip():
        parts.append(f"Hold reason: {reason.strip()}")
    parts.append("[original_user_turn]")
    parts.append(original_text.strip() or "(empty user turn)")
    return "\n".join(parts)


def _object_token_resolver():
    if not _CONFIG:
        return None
    try:
        from fiam.store.object_catalog import ObjectCatalog
        return ObjectCatalog.from_config(_CONFIG).resolve_token
    except Exception:
        return None


def _recent_uploads_block(limit: int = 12) -> str:
    if not _CONFIG:
        return ""
    manifest = _CONFIG.home_path / "uploads" / "manifest.jsonl"
    if not manifest.exists():
        return ""
    rows = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(
            f"- {rec.get('path')} (name={rec.get('name')!r}, mime={rec.get('mime')!r}, size={rec.get('size')}, uploaded_at={rec.get('uploaded_at')})"
        )
    if not rows:
        return ""
    return "[available_uploads]\nFiles are available for manual inspection; do not assume their contents without using tools.\n" + "\n".join(rows)


def _favilla_chat_send(payload: dict) -> dict:
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    received_started = datetime.now(timezone.utc)
    text = str(payload.get("text") or "").strip()
    turn_id = str(payload.get("turn_id") or f"turn_{uuid.uuid4().hex}")
    request_id = str(payload.get("request_id") or turn_id)
    client_sent_at = payload.get("client_sent_at")
    if isinstance(client_sent_at, (int, float)):
        client_sent_at = str(float(client_sent_at))
    else:
        client_sent_at = ""
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    safe_attachments = _validate_app_attachments(attachments)
    if not text and not safe_attachments:
        raise ValueError("missing text")
    if not text:
        text = "(see attached file)"
    channel = str(payload.get("channel") or "chat").strip() or "chat"
    surface = _surface_for_app_channel(channel, str(payload.get("surface") or ""))
    default_runtime = getattr(_CONFIG, "app_default_runtime", "auto") or "auto"
    runtime = str(payload.get("runtime") or default_runtime).strip().lower() or "auto"
    family = str(payload.get("family") or "").strip().lower()
    if runtime == "auto":
        route = _select_favilla_chat_route(text, safe_attachments)
        runtime = str(route.get("runtime") or "cc")
        family = family or str(route.get("family") or "")
    if payload.get("_receive_trace_recorded") is not True:
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.receive",
            started_at=received_started,
            refs={"runtime": runtime, "attachment_count": len(safe_attachments), "client_sent_at": client_sent_at},
        )
    user_text = text
    runtime_text = text
    stroll_context: dict | None = None
    if channel == "stroll":
        from fiam_lib.stroll_store import build_context_block

        context_block, stroll_context = build_context_block(_CONFIG, payload.get("stroll_context") if isinstance(payload.get("stroll_context"), dict) else payload.get("context"))
        runtime_text = f"{context_block}\n\n[user_message]\n{user_text}"
    record_turn = payload.get("record_turn", payload.get("record", True)) is not False
    if runtime == "api":
        result = _run_api_favilla_chat(text=runtime_text, channel=channel, surface=surface, attachments=safe_attachments, record_turn=record_turn, family=family, turn_id=turn_id, request_id=request_id)
    elif runtime == "cc":
        result = _run_cc_favilla_chat(text=runtime_text, channel=channel, surface=surface, attachments=safe_attachments, turn_id=turn_id, request_id=request_id)
    else:
        raise ValueError("Favilla chat runtime must be auto, cc, or api")
    _apply_route_from_result(result)
    stroll_records: list[dict] = []
    stroll_actions: list[dict] = []
    if channel == "stroll":
        from fiam_lib.stroll_store import apply_spatial_record_markers, apply_stroll_action_markers, strip_spatial_record_markers, strip_stroll_action_markers

        cleaned_reply, stroll_records = apply_spatial_record_markers(_CONFIG, str(result.get("reply") or ""), stroll_context or {})
        cleaned_reply, stroll_actions = apply_stroll_action_markers(_CONFIG, cleaned_reply, stroll_context or {})
        result["reply"] = cleaned_reply
        cleaned_segments = []
        for segment in result.get("segments") or []:
            if isinstance(segment, dict) and "text" in segment:
                segment = dict(segment)
                segment["text"] = strip_stroll_action_markers(strip_spatial_record_markers(str(segment.get("text") or "")))
                if segment.get("type") == "text" and not str(segment.get("text") or "").strip():
                    continue
            cleaned_segments.append(segment)
        result["segments"] = cleaned_segments
    _merge_result_object_attachments(result)
    _append_transcript(channel, {
        "role": "user",
        "text": user_text,
        "raw_text": user_text,
        "runtime": runtime,
        "attachments": _history_attachments(safe_attachments),
        "meta": {"turn_id": turn_id, "request_id": request_id, "surface": surface},
        "presence": _current_presence(actor="user", channel=channel),
    })
    _append_transcript(channel, {
        "role": "ai",
        "text": result.get("reply", ""),
        "raw_text": result.get("raw_reply", result.get("reply", "")),
        "runtime": runtime,
        "thinking": result.get("thoughts") or [],
        "thinkingLocked": bool(result.get("thoughts_locked")),
        "segments": result.get("segments") or [],
        "attachments": result.get("attachments") or [],
        "hold": result.get("hold"),
        "tool_calls_summary": result.get("tool_calls_summary") or [],
        "actions": result.get("actions_list") or [],
        "metrics": result.get("metrics") or {},
        "meta": {"turn_id": turn_id, "request_id": request_id, "surface": surface},
        "presence": _current_presence(actor="ai", channel=channel),
    })
    if stroll_context is not None:
        result["stroll_context"] = stroll_context
    if stroll_records:
        result["stroll_records"] = stroll_records
        try:
            from fiam_lib.stroll_events import get_bus
            bus = get_bus()
            for rec in stroll_records:
                bus.publish("record", rec)
        except Exception:
            logger.exception("stroll record publish failed")
    if channel == "stroll" and stroll_actions:
        result["stroll_actions"] = stroll_actions
        try:
            from fiam_lib.stroll_events import get_bus
            bus = get_bus()
            for act in stroll_actions:
                bus.publish("action", act)
        except Exception:
            logger.exception("stroll action publish failed")
    result["runtime"] = runtime
    result["turn_id"] = turn_id
    result["request_id"] = request_id
    try:
        rollover = _check_and_run_session_rollover(channel)
        if rollover:
            result["session_rollover"] = rollover
    except Exception:
        logger.exception("session rollover check failed")
    return result


def _favilla_chat_send_stream(payload: dict):
    """Streaming version of _favilla_chat_send. Yields {event, data} dicts.

    For runtime != cc, falls back to single-shot _favilla_chat_send and emits
    start + done. For runtime == cc, drives _iter_cc_favilla_chat_events,
    persists the final transcript, then emits done.
    """
    if not _CONFIG:
        yield {"event": "error", "data": {"message": "config not loaded"}}
        return
    turn_id = str(payload.get("turn_id") or f"turn_{uuid.uuid4().hex}")
    request_id = str(payload.get("request_id") or turn_id)
    received_started = datetime.now(timezone.utc)
    client_sent_at = ""
    client_sent_at = payload.get("client_sent_at")
    if isinstance(client_sent_at, (int, float)):
        client_sent_at = str(float(client_sent_at))
    else:
        client_sent_at = ""
    text = str(payload.get("text") or "").strip()
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    safe_attachments = _validate_app_attachments(attachments)
    if not text and not safe_attachments:
        yield {"event": "error", "data": {"message": "missing text"}}
        return
    if not text:
        text = "(see attached file)"
    channel = str(payload.get("channel") or "chat").strip() or "chat"
    surface = _surface_for_app_channel(channel, str(payload.get("surface") or ""))
    default_runtime = getattr(_CONFIG, "app_default_runtime", "auto") or "auto"
    runtime = str(payload.get("runtime") or default_runtime).strip().lower() or "auto"
    family = str(payload.get("family") or "").strip().lower()
    if runtime == "auto":
        route = _select_favilla_chat_route(text, safe_attachments)
        runtime = str(route.get("runtime") or "cc")
        family = family or str(route.get("family") or "")
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.receive",
        started_at=received_started,
        refs={"runtime": runtime, "attachment_count": len(safe_attachments), "client_sent_at": client_sent_at},
    )
    user_text = text
    runtime_text = text
    stroll_context: dict | None = None
    if channel == "stroll":
        from fiam_lib.stroll_store import build_context_block

        context_block, stroll_context = build_context_block(
            _CONFIG,
            payload.get("stroll_context") if isinstance(payload.get("stroll_context"), dict) else payload.get("context"),
        )
        runtime_text = f"{context_block}\n\n[user_message]\n{user_text}"

    if runtime != "cc":
        # Non-streaming runtime: do single-shot and emit start + done.
        yield {"event": "start", "data": {"runtime": runtime}}
        runtime_started = datetime.now(timezone.utc)
        try:
            routed_payload = dict(payload)
            routed_payload["runtime"] = runtime
            routed_payload["turn_id"] = turn_id
            routed_payload["request_id"] = request_id
            routed_payload["_receive_trace_recorded"] = True
            if family:
                routed_payload["family"] = family
            result = _favilla_chat_send(routed_payload)
        except Exception as e:
            _append_dashboard_trace_phase(
                turn_id=turn_id,
                request_id=request_id,
                channel=channel,
                surface=surface,
                phase="dashboard.runtime",
                started_at=runtime_started,
                status="error",
                error=str(e)[:1000],
                refs={"runtime": runtime},
            )
            yield {"event": "error", "data": {"message": str(e)[:500]}}
            return
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.runtime",
            started_at=runtime_started,
            refs={"runtime": runtime, "already_persisted": True},
        )
        result["trace"] = _trace_summary(turn_id, request_id, runtime=runtime)
        yield {"event": "commit", "data": {
            "turn_id": turn_id,
            "request_id": request_id,
            "surface": _surface_for_app_channel(str(routed_payload.get("channel") or "chat"), str(routed_payload.get("surface") or "")),
            "transcript_id": result.get("transcript_id") or "",
            "trace": result.get("trace") or {},
        }}
        yield {"event": "done", "data": result}
        return

    # Persist user transcript entry up-front so it shows immediately.
    _append_transcript(channel, {
        "role": "user",
        "text": user_text,
        "raw_text": user_text,
        "runtime": runtime,
        "attachments": _history_attachments(safe_attachments),
        "meta": {"turn_id": turn_id, "request_id": request_id, "surface": surface},
        "presence": _current_presence(actor="user", channel=channel),
    })
    try:
        _record_app_user_flow(user_text, channel, surface=surface, turn_id=turn_id, request_id=request_id)
    except Exception:
        logger.exception("stream user flow record failed")

    final_result: dict | None = None
    runtime_started = datetime.now(timezone.utc)
    for ev in _iter_cc_favilla_chat_events(text=runtime_text, channel=channel, surface=surface, attachments=safe_attachments, turn_id=turn_id, request_id=request_id):
        if ev.get("event") == "error":
            message = str((ev.get("data") or {}).get("message") or "stream error")
            _append_dashboard_trace_phase(
                turn_id=turn_id,
                request_id=request_id,
                channel=channel,
                surface=surface,
                phase="dashboard.runtime",
                started_at=runtime_started,
                status="error",
                error=message[:1000],
                refs={"runtime": runtime},
            )
            try:
                _record_app_error_flow(message, channel, surface=surface, runtime=runtime, turn_id=turn_id, request_id=request_id)
            except Exception:
                logger.exception("stream error flow record failed")
            yield ev
            return
        if ev.get("event") == "done":
            final_result = ev.get("data") or {}
            continue
        yield ev

    if final_result is None:
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.runtime",
            started_at=runtime_started,
            status="error",
            error="stream ended before done",
            refs={"runtime": runtime},
        )
        try:
            _record_app_error_flow("stream ended before done", channel, surface=surface, runtime=runtime, turn_id=turn_id, request_id=request_id)
        except Exception:
            logger.exception("stream missing-done flow record failed")
        return

    _apply_route_from_result(final_result)
    _merge_result_object_attachments(final_result)

    # Transcript is the durable source of truth. Persist before yielding done,
    # because the client may disconnect exactly as the final frame is sent.
    final_result["runtime"] = final_result.get("runtime") or runtime
    final_result["surface"] = surface
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.runtime",
        started_at=runtime_started,
        refs={"runtime": runtime},
    )
    final_result["trace"] = _trace_summary(turn_id, request_id, runtime=runtime)
    persist_started = datetime.now(timezone.utc)
    record = _persist_favilla_ai_transcript(channel, final_result, runtime)
    final_result["transcript_id"] = record.get("id")
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.persist",
        started_at=persist_started,
        refs={"transcript_id": final_result.get("transcript_id") or ""},
    )
    try:
        rollover = _check_and_run_session_rollover(channel)
        if rollover:
            final_result["session_rollover"] = rollover
    except Exception:
        logger.exception("session rollover check failed (stream)")
    yield {"event": "commit", "data": {
        "turn_id": turn_id,
        "request_id": request_id,
        "surface": surface,
        "transcript_id": final_result.get("transcript_id") or "",
        "trace": final_result.get("trace") or {},
    }}
    yield {"event": "done", "data": final_result}


def _favilla_upload(payload: dict) -> dict:
    """Accept base64-encoded files and store canonical bytes under store/objects."""
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    import base64
    from fiam.store.objects import ObjectStore

    files_in = payload.get("files") or []
    if not isinstance(files_in, list) or not files_in:
        raise ValueError("missing files")
    object_store = ObjectStore(_CONFIG.object_dir)
    saved = []
    for f in files_in:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip()
        b64 = str(f.get("data") or "")
        mime = str(f.get("mime") or "")
        if not name or not b64:
            continue
        # Strip data: prefix if present
        if "," in b64 and b64.startswith("data:"):
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
        except Exception as exc:
            raise ValueError(f"bad base64 for {name}: {exc}") from exc
        # Sanitize name: keep ext, strip path components
        safe_name = Path(name).name.replace("/", "_").replace("\\", "_")
        object_hash = object_store.put_bytes(raw, suffix="")
        target = object_store.path_for_hash(object_hash, suffix="")
        record = {
            "object_hash": object_hash,
            "path": str(target),
            "name": safe_name,
            "mime": mime,
            "size": len(raw),
        }
        saved.append(record)
        manifest = _CONFIG.home_path / "uploads" / "manifest.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("a", encoding="utf-8") as mf:
            mf.write(json.dumps({
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "sha256": object_hash,
                **record,
            }, ensure_ascii=False) + "\n")
    return {"ok": True, "files": saved}


# ------------------------------------------------------------------
# Favilla Chat memory ops (manual mode): /favilla/chat/recall, /favilla/chat/process
# ------------------------------------------------------------------
#
# Until BGE-M3 is well-tuned for personal-life semantics, the system runs
# in manual memory_mode: the user (via Favilla app or dashboard) decides
# *when* to recall and *when* to seal an event. Embeddings/edges are still
# computed and stored so we accumulate training data; they just don't
# auto-fire retrieval or auto-segment beats.
#
# These endpoints are intentionally lightweight stubs right now:
# - /favilla/chat/recall: builds a bounded RecallContext from existing Pool
#   nodes and keeps it for the next chat turn only.
# - /favilla/chat/process: simulates DS processing time (sleep 2s) so the UI can
#   show its hourglass animation, then appends a fake event to the pool
#   (random-init 1024-d fingerprint). Real seal logic comes after the
#   CC-path beat ingestion fix.

import threading

_SEAL_LOCK = threading.Lock()
_SEAL_BUSY = False


def _favilla_chat_recall(payload: dict) -> dict:
    """Run real spread-activation recall over the pool using the latest
    beats as the seed query vector, then stage it for the next chat turn.
    """
    if not _CONFIG or not _POOL:
        raise RuntimeError("config/pool not loaded")
    import numpy as _np
    from fiam.runtime.recall import build_recall_context
    from fiam.store.beat import read_beats
    from fiam.store.features import FeatureStore
    global _PENDING_RECALL_CONTEXT

    # Seed = mean of vectors for the most recent N event-store beats.
    seed_n = max(1, int(payload.get("seed_beats", 8)))
    seed_vec = None
    recent_beats = read_beats(_CONFIG.flow_path)[-seed_n:]
    if recent_beats:
        store = FeatureStore(_CONFIG.feature_dir, dim=_CONFIG.embedding_dim)
        vecs = []
        for beat in recent_beats:
            try:
                v = store.get_beat_vector(beat)
                if v is not None:
                    vecs.append(v)
            except Exception:
                continue
        if vecs:
            arr = _np.mean(_np.stack(vecs), axis=0).astype(_np.float32)
            n = float(_np.linalg.norm(arr))
            if n > 1e-9:
                seed_vec = arr / n

    if seed_vec is None:
        with _RECALL_LOCK:
            _PENDING_RECALL_CONTEXT = None
        return {"ok": True, "count": 0, "note": "no beats"}

    include_recent = payload.get(
        "include_recent",
        getattr(_CONFIG, "app_recall_include_recent", True),
    )
    if isinstance(include_recent, str):
        include_recent = include_recent.strip().lower() not in {"0", "false", "no", "off"}
    # Compute the shield-after cutoff: events created on or after this time
    # are excluded from recall. The cutoff is the *later* of today-midnight
    # (default shield) and the current session boundary (so events from the
    # in-flight session — which the AI already sees in its live context —
    # do not get re-surfaced via recall regardless of processed/unprocessed
    # status). When ``include_recent`` is True we drop the today-midnight
    # floor and use only the session boundary, letting freshly processed
    # events from earlier sessions today still surface.
    today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    boundary_dt: datetime | None = None
    try:
        boundary_iso = _load_session_state().get("boundary_ts") or ""
        if boundary_iso:
            boundary_dt = datetime.fromisoformat(boundary_iso.replace("Z", "+00:00"))
            if boundary_dt.tzinfo is None:
                boundary_dt = boundary_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        boundary_dt = None
    if include_recent:
        shield_after = boundary_dt
    else:
        shield_after = max(today_midnight, boundary_dt) if boundary_dt else today_midnight
    context = build_recall_context(
        _CONFIG,
        _POOL,
        seed_vec,
        top_k=_CONFIG.recall_top_k,
        shield_after=shield_after,
    )
    with _RECALL_LOCK:
        _PENDING_RECALL_CONTEXT = context if context.count else None
    return {"ok": True, "count": context.count}


def _cut_file_path():
    return _CONFIG.home_path / "app_cuts.jsonl"


def _favilla_chat_cut(payload: dict) -> dict:
    """Append a cut marker at the current end of the event stream. The next
    /favilla/chat/process call will use these markers to slice unprocessed
    beats into multiple segments (events).

    Cut alone does NOT trigger DS processing. It just records a divider.
    """
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    offset = _flow_offset_now()
    cut_path = _cut_file_path()
    cut_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"flow_offset": int(offset), "ts": datetime.now(timezone.utc).isoformat()}
    with cut_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    channel = str(payload.get("channel") or "chat")
    _append_transcript(channel, {
        "role": "ai",
        "divider": {"kind": "scissor", "label": "cut"},
    })
    return {"ok": True, "flow_offset": offset}


def _read_pending_cuts(start: int, end: int) -> list[int]:
    """Return absolute flow offsets in (start, end) where the user cut."""
    cut_path = _cut_file_path()
    if not cut_path.exists():
        return []
    out: list[int] = []
    for line in cut_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            o = int(rec.get("flow_offset", -1))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if start < o < end:
            out.append(o)
    return sorted(set(out))


def _truncate_cut_file() -> None:
    cut_path = _cut_file_path()
    if cut_path.exists():
        cut_path.write_text("", encoding="utf-8")


def _favilla_chat_process(payload: dict) -> dict:
    """Process all unprocessed flow beats. Cut markers (recorded via
    /favilla/chat/cut) split beats into multiple segments; each segment becomes
    one event via the dashboard 3-phase annotation pipeline.

    Cut markers are consumed (file truncated) on success. App-side seal is
    immutable once confirmed — edit via web console.
    """
    global _SEAL_BUSY
    if not _CONFIG or not _POOL:
        raise RuntimeError("config/pool not loaded")
    with _SEAL_LOCK:
        if _SEAL_BUSY:
            return {"ok": False, "error": "seal already in progress"}
        _SEAL_BUSY = True
    try:
        try:
            from fiam_lib.dashboard_annotation import annotation_state as _annot_state
            start = int(_annot_state().get("processed_until", 0))
            # Phase 1: load unprocessed beats
            proposal = _annotate_request({"limit": 10000})
            beats = proposal.get("beats", [])
            if not beats:
                return {"ok": False, "error": "no beats to seal"}
            end = int(proposal.get("flow_end", start + len(beats)))
            # Build cuts vector from pending cut markers in (start, end)
            cut_offsets = _read_pending_cuts(start, end)
            cuts = [0] * max(0, len(beats) - 1)
            for o in cut_offsets:
                idx = o - start - 1  # cut between beat[idx] and beat[idx+1]
                if 0 <= idx < len(cuts):
                    cuts[idx] = 1
            # Phase 2: DS proposes name + edges
            _annotate_edges({"cuts": cuts, "drift_cuts": cuts})
            # Phase 3: commit
            result = _annotate_confirm({"cuts": cuts, "drift_cuts": cuts})
            memory_processed = _run_memory_worker_once(limit=int(payload.get("memory_limit", 1000) or 1000))
            _truncate_cut_file()
            ev_ids = result.get("events_created", [])
            return {
                "ok": True,
                "event_id": ev_ids[0] if ev_ids else None,
                "events_created": ev_ids,
                "edges_created": result.get("edges_created", 0),
                "memory_processed": memory_processed,
                "beats": len(beats),
                "segments": len(ev_ids),
            }
        except Exception as exc:
            logger.exception("seal failed")
            return {"ok": False, "error": str(exc)}
    finally:
        with _SEAL_LOCK:
            _SEAL_BUSY = False


def _favilla_chat_process_status(_payload: dict | None = None) -> dict:
    return {"ok": True, "busy": _SEAL_BUSY}


# ---------------------------------------------------------------------------
# Session rollover (auto-cut + auto-process + transcript trim at threshold)
# ---------------------------------------------------------------------------
#
# Trigger: Favilla user/AI turn count since the last session boundary reaches
# `events_per_session` (default 10). On rollover:
#   1. Append a flow cut marker.
#   2. Run process so pending beats become events.
#   3. Trim shared runtime transcripts to the bounded tail.
#   4. Unlink active_session.json so CC opens a fresh session next turn.
#   5. Reset session_state.json (turns_since_boundary=0, advance boundary
#      flow_offset + ts).


def _session_state_path():
    return _CONFIG.home_path / "session_state.json"


def _load_session_state() -> dict:
    path = _session_state_path()
    if not path.exists():
        return {"turns_since_boundary": 0, "boundary_flow_offset": 0, "boundary_ts": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "turns_since_boundary": int(data.get("turns_since_boundary", 0) or 0),
                "boundary_flow_offset": int(data.get("boundary_flow_offset", 0) or 0),
                "boundary_ts": str(data.get("boundary_ts", "") or ""),
            }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"turns_since_boundary": 0, "boundary_flow_offset": 0, "boundary_ts": ""}


def _save_session_state(state: dict) -> None:
    path = _session_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _trim_runtime_transcript(channel: str) -> None:
    from fiam.runtime.prompt import trim_transcript_messages

    trim_transcript_messages(_CONFIG, channel)


def _flow_offset_now() -> int:
    try:
        from fiam.store.beat import read_beats
        return len(read_beats(_CONFIG.flow_path))
    except Exception:
        return 0


def _session_rollover(channel: str) -> dict:
    """Auto cut + process + trim transcript + clear active_session.

    Returns a small dict describing what happened, swallowing exceptions so
    rollover failures cannot break the chat handler that calls it.
    """
    info: dict = {"ok": True}
    try:
        cut_res = _favilla_chat_cut({"channel": channel})
        info["cut_offset"] = cut_res.get("flow_offset")
    except Exception as exc:
        info["cut_error"] = str(exc)[:200]
    try:
        proc_res = _favilla_chat_process({})
        info["process"] = {
            "events_created": proc_res.get("events_created", []),
            "ok": proc_res.get("ok"),
        }
    except Exception as exc:
        info["process_error"] = str(exc)[:200]
    try:
        _trim_runtime_transcript(channel)
        info["transcript_trimmed"] = True
    except Exception as exc:
        info["transcript_trim_error"] = str(exc)[:200]
    try:
        _clear_app_active_session()
    except Exception as exc:
        info["unlink_error"] = str(exc)[:200]
    new_state = {
        "turns_since_boundary": 0,
        "boundary_flow_offset": _flow_offset_now(),
        "boundary_ts": datetime.now(timezone.utc).isoformat(),
    }
    _save_session_state(new_state)
    info["new_state"] = new_state
    logger.info("session rollover channel=%s info=%s", channel, info)
    return info


def _check_and_run_session_rollover(channel: str) -> dict | None:
    """Bump the turn counter; if it crosses events_per_session, run rollover.

    Called once per fully-persisted chat turn (after both user and ai
    transcript records are written).
    """
    if not _CONFIG:
        return None
    cap = max(1, int(getattr(_CONFIG, "events_per_session", 10)))
    state = _load_session_state()
    state["turns_since_boundary"] = int(state.get("turns_since_boundary", 0) or 0) + 1
    if state["turns_since_boundary"] < cap:
        _save_session_state(state)
        return None
    # Persist the bumped count first so concurrent failures don't double-fire.
    _save_session_state(state)
    return _session_rollover(channel)


# ---------------------------------------------------------------------------
# Debug context snapshot (for /debug/context UI)
#
# build_api_messages writes home/.debug_last_assembly.json on every call.
# After the runtime returns we copy it to a runtime-specific file with
# metrics + reply length attached. The reply text itself is intentionally
# omitted — this UI is "what the model received", not "what came back".
# ---------------------------------------------------------------------------

def _record_debug_context(runtime: str, *, metrics: dict | None = None,
                          session_id: str = "", channel: str = "") -> None:
    if not _CONFIG:
        return
    rt = (runtime or "").strip().lower()
    if rt not in {"api", "cc"}:
        return
    src = _CONFIG.home_path / ".debug_last_assembly.json"
    payload: dict
    if src.exists():
        try:
            payload = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {}
    # Always stamp with the turn-completion time, so /context KPIs reflect
    # this actual call rather than an older assembly write.
    payload["timestamp"] = time.time()
    payload["runtime"] = rt
    if session_id:
        payload["session_id"] = session_id
    if channel:
        payload["channel"] = channel
    if isinstance(metrics, dict):
        payload["metrics"] = metrics
    dst = _CONFIG.home_path / f".debug_last_context_{rt}.json"
    try:
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = _CONFIG.home_path / ".debug_last_context.json"
        latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# CoT visibility: parsing lives in fiam_lib.app_markers (parse_app_cot).
# Protocol:
# - <cot>...</cot> wraps a shareable thought block.
# - <lock/> anywhere locks the ENTIRE thought chain for this turn
#   (covers both marker thoughts AND any native reasoning the runtime carries).
# - Default = unlocked. AI must opt-in to lock.

_APP_RUNTIME_CONTEXT_BASE = """[Direct runtime awareness]
The scene tag describes where this turn appears in the narrative. User-side scenes use canonical channel plus surface: user@chat/favilla is Favilla chat, user@stroll/favilla is the Favilla Stroll surface, and user@browser/atrium is browser context. AI-side scenes mirror the same channel/surface pair; ai@think is internal reasoning; ai@action is a tool call. The runtime tag describes the capability surface for this turn: api is the OpenAI-compatible API surface, cc is Claude Code with file/shell/tool capability, and auto means the server selected one. Do not infer a fixed personal name from the runtime tag. The web dashboard is view-only and never originates a user turn — there is no console scene. Reply naturally for the active scene while staying precise about the runtime."""


def _app_runtime_context() -> str:
    now_utc = _CONFIG.now_utc() if _CONFIG else datetime.now(timezone.utc)
    local = now_utc.astimezone(_CONFIG.project_tz()).isoformat() if _CONFIG else now_utc.astimezone().isoformat()
    uploads_dir = (_CONFIG.home_path / "uploads") if _CONFIG else Path("uploads")
    objects_dir = (_CONFIG.object_dir) if _CONFIG else Path("store/objects")
    object_put = (_CONFIG.code_path / "scripts" / "object_put.py") if _CONFIG else Path("scripts/object_put.py")
    config_path = (_CONFIG.toml_path) if _CONFIG else Path("fiam.toml")
    return "\n\n".join([
        _APP_RUNTIME_CONTEXT_BASE,
        f"[uploads]\nFavilla uploads are canonical object refs in {objects_dir}; the index is {uploads_dir / 'manifest.jsonl'}. Current attachment prompt lines include obj:<sha256> plus a materialized path for inspection. Do not mention old uploaded files just because they exist. Only inspect or discuss uploads when the current user message asks about files/images/uploads or includes current attachments.",
        f"[object_transfer]\nTo send a generated file back to Favilla, store it in ObjectStore and mention the returned obj token in the visible reply. In Claude Code, create the file under the home directory, then run: python {object_put} --config {config_path} --path <relative-file> --direction outbound",
        f"[server_time]\nutc={now_utc.isoformat()}\nlocal={local}",
        "[tool_mode]\nUse the structured file/shell tools (Read/Write/Edit/Glob/Grep/Bash/git_diff) only when you must wait on a real result. For fire-and-forget side effects use XML markers: <send to=\"channel:address\">message</send>, <todo at=\"...\">desc</todo>, <wake at=\"...\"/>, <sleep at=\"...\"/>, and <state value=\"mute|notify|block|busy|sleep|together\" reason=\"...\"/>. Keep tool details out of the user-facing reply unless the user asks for them.",
        "[app_markers]\nFor visible thinking summaries, wrap shareable state notes in <cot>...</cot>. To lock the entire turn's thought chain (both <cot> blocks and any native reasoning), include <lock/> anywhere in the reply. The server strips these markers into structured segments; clients may or may not render them visibly. Do not promise a specific button, bubble, or visual affordance unless the current client explicitly supports it. To pull back a reply and immediately try again, include <hold/> or <hold>reason</hold>. To end this turn without a visible reply, include <held>reason</held>. Held output is stored privately and is not queued as a user todo.",
    ])


def _cc_warm_idle_seconds() -> int:
    try:
        return max(1, int(os.environ.get("FIAM_CC_WARM_IDLE_SECONDS", _CC_WARM_IDLE_SECONDS)))
    except (TypeError, ValueError):
        return _CC_WARM_IDLE_SECONDS


def _cc_warm_enabled() -> bool:
    return os.environ.get("FIAM_CC_WARM_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}


def _cc_channel_transport_enabled() -> bool:
    try:
        from fiam_lib.cc_channel import channel_enabled

        return channel_enabled()
    except Exception:
        return False


def _cc_chat_command(*, user_prompt: str | None, system_context: str = "", input_stream: bool = False) -> list[str]:
    command = ["claude", "-p"]
    if input_stream:
        command.extend(["--input-format", "stream-json"])
    else:
        command.append(user_prompt or "")
    command.extend([
        "--output-format", "stream-json",
        "--verbose",
        "--setting-sources", "user,project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--permission-mode", "bypassPermissions",
    ])
    if system_context:
        command.extend(["--append-system-prompt", system_context])
    if _CONFIG and _CONFIG.cc_model:
        command.extend(["--model", _CONFIG.cc_model])
    if _CONFIG and _CONFIG.cc_disallowed_tools:
        command.extend([
            "--disallowedTools",
            *[tool.strip() for tool in _CONFIG.cc_disallowed_tools.split(",") if tool.strip()],
        ])
    if _CONFIG and getattr(_CONFIG, "cc_effort", ""):
        command.extend(["--effort", _CONFIG.cc_effort])
    return command


def _cc_stable_prompt_fingerprint() -> str:
    if not _CONFIG:
        return ""
    paths: list[Path] = [_CONFIG.constitution_md_path, _CONFIG.manual_md_path]
    if _CONFIG.self_dir.is_dir():
        paths.extend(sorted(_CONFIG.self_dir.glob("*.md")))
    claude_dir = _CONFIG.home_path / ".claude"
    paths.extend([claude_dir / "settings.json", claude_dir / "settings.local.json"])
    output_styles_dir = claude_dir / "output-styles"
    if output_styles_dir.is_dir():
        paths.extend(sorted(output_styles_dir.glob("*.md")))
    parts: list[str] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    parts.append(f"cc_model={_CONFIG.cc_model}")
    parts.append(f"cc_disallowed_tools={_CONFIG.cc_disallowed_tools}")
    parts.append(f"cc_effort={getattr(_CONFIG, 'cc_effort', '')}")
    return "|".join(parts)


def _split_cc_warm_system_context(system_context: str) -> tuple[str, str]:
    stable_parts: list[str] = []
    turn_parts: list[str] = []
    for part in (system_context or "").split("\n\n"):
        text = part.strip()
        if not text:
            continue
        if text.startswith("[context]") or text.startswith("[timeline]"):
            turn_parts.append(text)
        else:
            stable_parts.append(text)
    return "\n\n".join(stable_parts), "\n\n".join(turn_parts)


def _insert_cc_turn_context(user_prompt: str, current_user_text: str, turn_context: str) -> str:
    context = (turn_context or "").strip()
    prompt = (user_prompt or "").strip()
    if not context:
        return prompt
    current = (current_user_text or "").strip()
    if current and prompt.endswith(current):
        prefix = prompt[:-len(current)].rstrip()
        return "\n\n".join(part for part in (prefix, context, current) if part)
    return "\n\n".join(part for part in (prompt, context) if part)


def _build_cc_favilla_prompt_parts(prompt_text: str, *, channel: str, recall_context=None, warm: bool = False) -> tuple[str, str]:
    from fiam.runtime.prompt import build_plain_prompt_parts

    system_context, user_prompt = build_plain_prompt_parts(
        _CONFIG,
        prompt_text,
        channel=channel,
        include_recall=True,
        recall_context=recall_context,
        extra_context="" if warm else _app_runtime_context(),
    )
    if not warm:
        return system_context, user_prompt
    system_context, dynamic_context = _split_cc_warm_system_context(system_context)
    return system_context, _insert_cc_turn_context(user_prompt, prompt_text, dynamic_context)


def _cc_project_transcript_path(session_id: str) -> Path | None:
    if not _CONFIG or not session_id:
        return None
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    try:
        cwd = str(_CONFIG.home_path.resolve())
    except OSError:
        cwd = str(_CONFIG.home_path)
    project_key = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return config_dir / "projects" / project_key / f"{session_id}.jsonl"


def _scrub_cc_hook_value(value):
    if isinstance(value, str):
        if "<user-prompt-submit-hook" not in value:
            return value, False
        scrubbed = value
        if "<user-prompt-submit-hook" in scrubbed:
            scrubbed = _CC_HOOK_TAG_RE.sub("\n", scrubbed)
        scrubbed = scrubbed.strip()
        return scrubbed, scrubbed != value
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            clean, item_changed = _scrub_cc_hook_value(item)
            out.append(clean)
            changed = changed or item_changed
        return out, changed
    if isinstance(value, dict):
        changed = False
        out = {}
        for key, item in value.items():
            clean, item_changed = _scrub_cc_hook_value(item)
            out[key] = clean
            changed = changed or item_changed
        return out, changed
    return value, False


def _scrub_cc_hook_attachment(data: dict) -> tuple[dict | None, bool]:
    if data.get("type") != "attachment":
        return data, False
    attachment = data.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "hook_additional_context":
        return data, False
    return None, True


def _scrub_cc_transcript_obj(data: dict) -> tuple[dict | None, bool]:
    clean, changed = _scrub_cc_hook_value(data)
    if not isinstance(clean, dict):
        return data, changed
    clean, hook_changed = _scrub_cc_hook_attachment(clean)
    return clean, changed or hook_changed


def _cc_scrub_hook_transcript(session: dict | None = None) -> bool:
    session = session or _load_app_active_session()
    session_id = str((session or {}).get("session_id") or "").strip()
    path = _cc_project_transcript_path(session_id)
    if path and path.exists():
        try:
            from fiam_lib.cc_channel import scrub_transcript

            return scrub_transcript(path)
        except Exception:
            logger.exception("shared CC transcript scrub failed; using local scrubber")
    if not path or not path.exists():
        return False
    changed = False
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with path.open("r", encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
            for raw_line in src:
                line = raw_line.rstrip("\n")
                if not line.strip() or not any(token in line for token in _CC_TRANSCRIPT_SCRUB_TOKENS):
                    dst.write(raw_line)
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    tmp.unlink(missing_ok=True)
                    return False
                clean, item_changed = _scrub_cc_transcript_obj(data)
                if clean is None:
                    changed = True
                    continue
                dst.write(json.dumps(clean, ensure_ascii=False) + "\n" if item_changed else raw_line)
                changed = changed or item_changed
        if not changed:
            tmp.unlink(missing_ok=True)
            return False
        backup = path.with_name(path.name + ".fiam-context.bak")
        shutil.copy2(path, backup)
        tmp.replace(path)
        logger.info("scrubbed CC hook context from %s", path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class _CCWarmRunner:
    def __init__(self, *, command: list[str], cwd_path: Path, fingerprint: str) -> None:
        import subprocess

        self.command = command
        self.cwd_path = cwd_path
        self.fingerprint = fingerprint
        self.system_dirty = False
        self.last_used = time.monotonic()
        self.last_session_id = ""
        self.items: "queue.Queue[dict]" = queue.Queue()
        self.stderr_lines: list[str] = []
        self.idle_timer: threading.Timer | None = None
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd_path),
        )
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()

    def _read_stdout(self) -> None:
        try:
            assert self.proc.stdout is not None
            for raw_line in self.proc.stdout:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    self.items.put(json.loads(raw_line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.items.put({"_fiam_event": "eof"})

    def _read_stderr(self) -> None:
        try:
            assert self.proc.stderr is not None
            for raw_line in self.proc.stderr:
                line = raw_line.rstrip()
                if line:
                    self.stderr_lines.append(line)
                    if len(self.stderr_lines) > 80:
                        del self.stderr_lines[:40]
        except Exception:
            pass

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_tail(self) -> str:
        return "\n".join(self.stderr_lines[-20:]).strip()

    def cancel_idle_timer(self) -> None:
        timer = self.idle_timer
        self.idle_timer = None
        if timer:
            timer.cancel()

    def schedule_idle_timer(self) -> None:
        self.cancel_idle_timer()
        timer = threading.Timer(_cc_warm_idle_seconds(), _cc_warm_idle_timeout, args=(self,))
        timer.daemon = True
        self.idle_timer = timer
        timer.start()

    def drain_stale_items(self) -> None:
        while True:
            try:
                item = self.items.get_nowait()
            except queue.Empty:
                return
            if item.get("_fiam_event") == "eof":
                raise RuntimeError(self.stderr_tail() or "claude warm process exited")

    def iter_items(self, user_prompt: str, *, timeout_seconds: float = 240.0):
        if not self.is_alive():
            raise RuntimeError(self.stderr_tail() or "claude warm process exited")
        self.cancel_idle_timer()
        self.drain_stale_items()
        payload = {"type": "user", "message": {"role": "user", "content": user_prompt}}
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except Exception as exc:
            raise RuntimeError("failed to write to claude warm process") from exc

        deadline = time.monotonic() + timeout_seconds
        saw_result = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("claude warm chat timeout")
                try:
                    item = self.items.get(timeout=min(1.0, remaining))
                except queue.Empty:
                    if not self.is_alive():
                        raise RuntimeError(self.stderr_tail() or "claude warm process exited")
                    continue
                if item.get("_fiam_event") == "eof":
                    raise RuntimeError(self.stderr_tail() or "claude warm process exited")
                yield item
                if item.get("type") == "result":
                    saw_result = True
                    session_id = str(item.get("session_id") or "").strip()
                    if session_id:
                        self.last_session_id = session_id
                    return
        finally:
            self.last_used = time.monotonic()
            if saw_result and self.is_alive():
                self.schedule_idle_timer()

    def close(self) -> None:
        self.cancel_idle_timer()
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    self.proc.kill()
        except Exception:
            pass


class _CCWarmTurnProcess:
    def __init__(self, runner: _CCWarmRunner, user_prompt: str) -> None:
        self.runner = runner
        self.user_prompt = user_prompt
        self.returncode = 0
        self._stderr = ""
        self.stderr = self
        self.stdout = self._iter_stdout()

    def _iter_stdout(self):
        try:
            for item in self.runner.iter_items(self.user_prompt, timeout_seconds=225.0):
                yield json.dumps(item, ensure_ascii=False) + "\n"
        except Exception as exc:
            self.returncode = 1
            self._stderr = f"{_CC_WARM_FAILURE_PREFIX} {exc}"

    def wait(self, timeout=None):
        return self.returncode

    def read(self):
        return self._stderr or self.runner.stderr_tail()

    def kill(self):
        self.returncode = -9
        self.runner.close()


class _CCCompletedTurnProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        from io import StringIO

        self.stdout = StringIO(stdout or "")
        self.stderr = StringIO(stderr or "")
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _run_cc_channel_turn_result_locked(user_prompt: str, system_context: str, *, resume_session_id: str = "", max_turns: int = 10):
    from fiam_lib.cc_channel import as_completed_process, run_channel_turn

    turn = run_channel_turn(
        _CONFIG,
        user_prompt,
        system_context=system_context,
        resume_session_id=resume_session_id,
        max_turns=max_turns,
    )
    return as_completed_process(turn)


def _run_cc_warm_turn_result_locked(user_prompt: str, system_context: str):
    from types import SimpleNamespace

    runner = _cc_warm_runner_locked(system_context)
    lines: list[str] = []
    try:
        for item in runner.iter_items(user_prompt, timeout_seconds=225.0):
            lines.append(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as exc:
        raise RuntimeError(f"{_CC_WARM_FAILURE_PREFIX} {exc}") from exc
    return SimpleNamespace(stdout="".join(lines), stderr="", returncode=0)


def _cc_warm_shutdown_locked(reason: str = "", *, scrub: bool = True) -> None:
    global _CC_WARM_RUNNER
    runner = _CC_WARM_RUNNER
    _CC_WARM_RUNNER = None
    if not runner:
        return
    runner.close()
    if scrub:
        session_id = runner.last_session_id or str((_load_app_active_session() or {}).get("session_id") or "")
        if session_id:
            _cc_scrub_hook_transcript({"session_id": session_id})
    if reason:
        logger.info("CC warm runner stopped: %s", reason)


def _cc_warm_idle_timeout(runner: _CCWarmRunner) -> None:
    with _CC_RUNTIME_LOCK:
        if _CC_WARM_RUNNER is not runner:
            return
        if time.monotonic() - runner.last_used >= _cc_warm_idle_seconds():
            _cc_warm_shutdown_locked("idle timeout")
        else:
            runner.schedule_idle_timer()


def _cc_warm_runner_locked(system_context: str) -> _CCWarmRunner:
    global _CC_WARM_RUNNER
    fingerprint = _cc_stable_prompt_fingerprint()
    runner = _CC_WARM_RUNNER
    if runner and runner.is_alive():
        if runner.fingerprint != fingerprint:
            runner.system_dirty = True
        return runner
    if runner:
        _cc_warm_shutdown_locked("dead process")
    session = _load_app_active_session()
    _cc_scrub_hook_transcript(session)
    command = _cc_chat_command(user_prompt=None, system_context=system_context, input_stream=True)
    resume_id = str((session or {}).get("session_id") or "").strip()
    if resume_id:
        command.extend(["--resume", resume_id])
    _CC_WARM_RUNNER = _CCWarmRunner(command=command, cwd_path=_CONFIG.home_path, fingerprint=fingerprint)
    return _CC_WARM_RUNNER


def _clear_app_active_session_locked() -> None:
    _cc_warm_shutdown_locked("active session cleared")
    try:
        _CONFIG.active_session_path.unlink(missing_ok=True)
    except OSError:
        pass


def _clear_app_active_session() -> None:
    with _CC_RUNTIME_LOCK:
        _clear_app_active_session_locked()


def _parse_cot(reply: str) -> tuple[str, list[dict], bool, list[dict]]:
    """Strip <cot>/<lock> markers from reply.

    Returns (cleaned_reply, thoughts, locked, segments).
    - thoughts: list of {"kind":"think","text":str,"source":"marker"} from <cot> blocks
    - locked: True if AI wrote <lock/>; else False (default unlock)

    Fallback: if the model wrapped its ENTIRE reply inside <cot> markers
    so the cleaned body is empty, demote the last thought back to the user-facing
    reply (avoids "AI's answer disappeared into thinking chain" bug).
    """
    parsed = parse_app_cot(reply, _CONFIG)
    return parsed.reply, parsed.thoughts, parsed.locked, parsed.segments


def _official_thought_payloads(thinking_events: list[dict] | None) -> tuple[list[dict], list[dict]]:
    raw_steps: list[dict] = []
    for ev in thinking_events or []:
        text = str(ev.get("text") or "").strip()
        if text:
            raw_steps.append({"kind": "think", "text": text, "source": "official"})
    if not raw_steps:
        return [], []
    summaries = summarize_cot_steps(raw_steps, locked=False, config=_CONFIG)
    by_index: dict[int, dict] = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        by_index[idx] = item
    thoughts: list[dict] = []
    segments: list[dict] = []
    for index, raw in enumerate(raw_steps):
        summary = by_index.get(index, {})
        item = {
            **raw,
            "summary": str(summary.get("summary") or "Native thinking"),
            "source": "official",
            "locked": False,
            "icon": str(summary.get("icon") or "NativeThinking"),
        }
        thoughts.append(item)
        segments.append({"type": "thought", **item})
    return thoughts, segments


def _redact_cc_snippet(value: object, *, limit: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(token|api[_-]?key|secret|password)(\s*[:=]\s*)[^\s'\"]+", r"\1\2<redacted>", text)
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer <redacted>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _is_cc_resume_recoverable_error(detail: object) -> bool:
    text = str(detail or "")
    return (
        "No conversation found with session ID" in text
        or "Failed to authenticate" in text
        or "API Error: 401" in text
    )


def _summarize_cc_tool_input(name: str, data: dict) -> str:
    if name == "Bash":
        desc = data.get("description") or ""
        command = data.get("command") or ""
        return _redact_cc_snippet(desc or command, limit=360)
    for key in ("file_path", "path", "pattern", "glob", "url"):
        if data.get(key):
            return _redact_cc_snippet(data.get(key), limit=360)
    if data:
        return _redact_cc_snippet(json.dumps(data, ensure_ascii=False, sort_keys=True), limit=360)
    return ""


def _parse_cc_stream(stdout: str) -> tuple[dict, list[dict], list[dict]]:
    result_data: dict = {}
    tool_names: dict[str, str] = {}
    action_events: list[dict] = []
    thinking_events: list[dict] = []
    for raw_line in (stdout or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        item_type = item.get("type")
        if item_type == "result":
            result_data = item
            continue
        if item_type == "assistant":
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "thinking":
                    text = str(block.get("thinking") or "").strip()
                    if text:
                        thinking_events.append({
                            "text": text,
                            "signature": str(block.get("signature") or ""),
                        })
                    continue
                if btype != "tool_use":
                    continue
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "tool")
                tool_names[tool_id] = name
                input_data = block.get("input") if isinstance(block.get("input"), dict) else {}
                summary = _summarize_cc_tool_input(name, input_data)
                action_events.append({
                    "kind": "tool_use",
                    "tool_use_id": tool_id,
                    "tool_name": name,
                    "summary": summary,
                })
            continue
        if item_type == "user":
            result = item.get("tool_use_result") if isinstance(item.get("tool_use_result"), dict) else {}
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            content = message.get("content") or []
            tool_id = ""
            content_text = ""
            if content and isinstance(content[0], dict):
                tool_id = str(content[0].get("tool_use_id") or "")
                content_text = str(content[0].get("content") or "")
            name = tool_names.get(tool_id, "tool")
            stdout_text = result.get("stdout") or content_text
            stderr_text = result.get("stderr") or ""
            output = stdout_text if stdout_text else stderr_text
            action_events.append({
                "kind": "tool_result",
                "tool_use_id": tool_id,
                "tool_name": name,
                "is_error": bool(result.get("is_error")) or bool(result.get("isError")) or bool(stderr_text and not stdout_text),
                "summary": _redact_cc_snippet(output, limit=500),
                "output_text": str(output or ""),
            })
    return result_data, action_events, thinking_events


def _combine_cc_action_events(action_events: list[dict]) -> list[dict]:
    combined: list[dict] = []
    by_id: dict[str, dict] = {}
    for event in action_events:
        tool_id = str(event.get("tool_use_id") or "")
        name = str(event.get("tool_name") or "tool")
        action = by_id.get(tool_id)
        if action is None:
            action = {
                "kind": "tool_action",
                "tool_use_id": tool_id,
                "tool_name": name,
                "input_summary": "",
                "result_summary": "",
                "is_error": False,
                "status": "pending",
            }
            by_id[tool_id] = action
            combined.append(action)
        if name and action.get("tool_name") in {"", "tool"}:
            action["tool_name"] = name
        if event.get("kind") == "tool_use":
            action["input_summary"] = str(event.get("summary") or "")
        elif event.get("kind") == "tool_result":
            action["result_summary"] = str(event.get("summary") or "")
            action["result_full"] = str(event.get("output_text") or "")
            action["is_error"] = bool(event.get("is_error"))
            action["status"] = "error" if action["is_error"] else "ok"
    return combined


def _run_cc_favilla_chat(*, text: str, channel: str, surface: str = "", attachments: list | None = None, turn_id: str = "", request_id: str = "") -> dict:
    with _CC_RUNTIME_LOCK:
        return _run_cc_favilla_chat_locked(text=text, channel=channel, surface=surface, attachments=attachments, turn_id=turn_id, request_id=request_id)


def _run_cc_favilla_chat_locked(*, text: str, channel: str, surface: str = "", attachments: list | None = None, turn_id: str = "", request_id: str = "") -> dict:
    import subprocess

    prompt_started = datetime.now(timezone.utc)
    pending_recall_context = _pop_pending_recall_context()
    pending_recall = _render_recall_for_app(pending_recall_context)
    session = _load_app_active_session()
    prompt_text = text
    if attachments:
        lines = ["[attachments]"]
        for a in attachments:
            mime = a.get("mime") or ""
            lines.append(f"- obj:{a.get('object_hash', '')}  (name={a['name']!r}, mime={mime}, size={a.get('size')})")
        lines.append("")
        prompt_text = "\n".join(lines) + text
    warm_system_context, warm_user_prompt = _build_cc_favilla_prompt_parts(
        prompt_text,
        channel=channel,
        recall_context=pending_recall_context,
        warm=True,
    )
    use_warm_holder = {"enabled": _cc_warm_enabled()}
    cold_command_cache: dict[str, list[str]] = {}
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.prompt",
        started_at=prompt_started,
        refs={"runtime": "cc", "attachment_count": len(attachments or []), "resume": bool(session)},
    )
    cwd_path = _CONFIG.home_path

    def cold_command(prompt_override: str | None = None) -> list[str]:
        command = cold_command_cache.get("command")
        if command is None:
            if _CC_WARM_RUNNER:
                _cc_warm_shutdown_locked("cold non-stream fallback")
            _cc_scrub_hook_transcript(session)
            cold_system_context, cold_user_prompt = _build_cc_favilla_prompt_parts(
                prompt_text,
                channel=channel,
                recall_context=pending_recall_context,
                warm=False,
            )
            command = _cc_chat_command(user_prompt=cold_user_prompt, system_context=cold_system_context, input_stream=False)
            cold_command_cache["command"] = command
        cmd = list(command)
        if prompt_override is not None:
            cmd[2] = prompt_override
        return cmd

    def run_claude(resume_session_id: str | None, prompt_override: str | None = None):
        if _cc_channel_transport_enabled():
            try:
                return _run_cc_channel_turn_result_locked(
                    prompt_override or warm_user_prompt,
                    warm_system_context,
                    resume_session_id=resume_session_id or "",
                    max_turns=10,
                )
            except RuntimeError as exc:
                raise RuntimeError(f"claude channel failed: {str(exc).strip()[:500]}") from exc
        if use_warm_holder["enabled"]:
            try:
                return _run_cc_warm_turn_result_locked(prompt_override or warm_user_prompt, warm_system_context)
            except RuntimeError as exc:
                _cc_warm_shutdown_locked(str(exc), scrub=True)
                use_warm_holder["enabled"] = False
        cmd = cold_command(prompt_override)
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        try:
            return subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                timeout=240,
                cwd=str(cwd_path),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("claude chat timeout") from exc
        except FileNotFoundError as exc:
            raise RuntimeError("claude not found on server PATH") from exc

    def parse_claude_result(result):
        data, action_events, thinking_events = _parse_cc_stream(result.stdout or "")
        if not data:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            raise RuntimeError(f"bad claude stream-json: {detail}")
        is_partial_success = data.get("subtype") == "error_max_turns"
        is_error = bool(data.get("is_error")) or result.returncode != 0
        return data, _combine_cc_action_events(action_events), thinking_events, is_partial_success, is_error

    resume_id = session["session_id"] if session else None
    runtime_started = datetime.now(timezone.utc)
    result = run_claude(resume_id)
    data, action_events, thinking_events, is_partial_success, is_error = parse_claude_result(result)
    if is_error and not is_partial_success:
        detail = (data.get("error") or data.get("result") or result.stderr or result.stdout or "claude failed")
        if resume_id and _is_cc_resume_recoverable_error(detail):
            _clear_app_active_session_locked()
            result = run_claude(None)
            data, action_events, thinking_events, is_partial_success, is_error = parse_claude_result(result)
            detail = (data.get("error") or data.get("result") or result.stderr or result.stdout or "claude failed")
        if is_error and not is_partial_success:
            raise RuntimeError(str(detail).strip()[:500])

    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.runtime",
        started_at=runtime_started,
        refs={
            "runtime": "cc",
            "returncode": int(result.returncode or 0),
            "subtype": str(data.get("subtype") or ""),
            "model": str(data.get("model") or _CONFIG.cc_model or ""),
            "duration_ms": int(data.get("duration_ms") or 0),
            "action_count": len(action_events or []),
        },
    )

    session_id = str(data.get("session_id") or "").strip()
    if session_id:
        _save_app_active_session(session_id)

    reply = str(data.get("result") or "").strip()
    raw_reply = reply
    marker_started = datetime.now(timezone.utc)
    interpretation = _interpret_app_control_markers(reply)
    if interpretation.hold_status == "reroll":
        _record_cc_app_turn(prompt_text, raw_reply, channel, surface=surface, action_events=action_events, thinking_events=thinking_events, session_id=session_id, record_user=False, turn_id=turn_id, request_id=request_id)
        reroll_started = datetime.now(timezone.utc)
        result = run_claude(session_id or resume_id, prompt_override=_hold_reroll_prompt(prompt_text, interpretation.hold_reason))
        data, action_events, thinking_events, is_partial_success, is_error = parse_claude_result(result)
        if is_error and not is_partial_success:
            detail = (data.get("error") or data.get("result") or result.stderr or result.stdout or "claude reroll failed")
            raise RuntimeError(str(detail).strip()[:500])
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.runtime",
            started_at=reroll_started,
            refs={
                "runtime": "cc",
                "reroll": True,
                "returncode": int(result.returncode or 0),
                "subtype": str(data.get("subtype") or ""),
                "model": str(data.get("model") or _CONFIG.cc_model or ""),
                "duration_ms": int(data.get("duration_ms") or 0),
                "action_count": len(action_events or []),
            },
        )
        session_id = str(data.get("session_id") or "").strip()
        if session_id:
            _save_app_active_session(session_id)
        reply = str(data.get("result") or "").strip()
        raw_reply = reply
        interpretation = _interpret_app_control_markers(reply)
    reply = interpretation.visible_reply
    queued_todos = [
        {"at": todo.at, "kind": todo.kind, "reason": todo.reason}
        for todo in interpretation.todo_changes
    ]
    hold = _app_hold_payload(interpretation)
    hold_reason = interpretation.hold_reason
    route = interpretation.route_hint
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.marker",
        started_at=marker_started,
        refs={"todo_count": len(queued_todos), "hold": bool(hold), "route": bool(route)},
    )
    cleaned_reply, thoughts, thoughts_locked, segments = _parse_cot(reply)
    native_thoughts, native_segments = _official_thought_payloads(thinking_events)
    if native_thoughts:
        thoughts = native_thoughts + thoughts
        segments = native_segments + list(segments)
    if hold and not segments:
        thoughts_locked = True
        summary = hold_reason or "holding this reply"
        thoughts = [{"kind": "think", "text": summary, "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
        segments = [{"type": "thought", "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
    # Step 6: enrich segments with tool_use / tool_result events from cc stream-json.
    # Real-time order: tools fire while cc is working, then it emits the final
    # reply (which contains <cot>...</cot> markers + visible text). So tool
    # events come BEFORE the parsed cot/text segments, in arrival order.
    tool_segments: list[dict] = []
    for action in action_events or []:
        tool_id = str(action.get("tool_use_id") or "")
        tool_name = str(action.get("tool_name") or "tool")
        if action.get("input_summary"):
            tool_segments.append({
                "type": "tool_use",
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "input_summary": str(action.get("input_summary") or ""),
            })
        if action.get("result_summary") or action.get("is_error"):
            tool_segments.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "result_summary": str(action.get("result_summary") or ""),
                "is_error": bool(action.get("is_error")),
            })
    enriched_segments = tool_segments + list(segments)
    metrics = _normalize_metrics(
        runtime="cc",
        model=str(data.get("model") or _CONFIG.cc_model or ""),
        usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
        latency_ms=int(data.get("duration_ms") or 0) or None,
        cost_usd=data.get("total_cost_usd"),
    )
    actions_list: list[dict] = []
    for todo in queued_todos or []:
        actions_list.append({"kind": "queued_todo", **(todo if isinstance(todo, dict) else {"text": str(todo)})})
    if route:
        actions_list.append({"kind": "route", **(route if isinstance(route, dict) else {"value": str(route)})})
    _record_cc_app_turn(prompt_text, raw_reply, channel, surface=surface, action_events=action_events, thinking_events=thinking_events, session_id=session_id, turn_id=turn_id, request_id=request_id)
    try:
        _record_debug_context("cc", metrics=metrics, session_id=session_id, channel=channel)
    except Exception:
        logger.exception("debug context record (cc) failed")
    return {
        "ok": True,
        "runtime": "cc",
        "reply": cleaned_reply,
        "raw_reply": raw_reply,
        "recall": pending_recall,
        "session_id": session_id,
        "subtype": data.get("subtype"),
        "cost_usd": data.get("total_cost_usd", 0),
        "thoughts": thoughts,
        "thoughts_locked": thoughts_locked,
        "segments": enriched_segments,
        "hold": hold,
        "queued_todos": queued_todos,
        "route": route,
        "actions": action_events,
        # Step 6: structured fields for transcript
        "tool_calls_summary": action_events or [],
        "actions_list": actions_list,
        "metrics": metrics,
    }


def _iter_cc_favilla_chat_events(*, text: str, channel: str, surface: str = "", attachments: list | None = None, turn_id: str = "", request_id: str = ""):
    with _CC_RUNTIME_LOCK:
        yield from _iter_cc_favilla_chat_events_locked(text=text, channel=channel, surface=surface, attachments=attachments, turn_id=turn_id, request_id=request_id)


def _iter_cc_favilla_chat_events_locked(*, text: str, channel: str, surface: str = "", attachments: list | None = None, turn_id: str = "", request_id: str = ""):
    """Stream CC chat as SSE events.

    Yields dicts of shape {"event": str, "data": dict}. Events:
      - start: {runtime}
      - tool_use: {tool_use_id, tool_name, input_summary}
      - tool_result: {tool_use_id, tool_name, result_summary, is_error}
      - thought: {index, text, source, locked, summary, icon}     (placeholder summary)
    - thought_summary: {index, summary, icon}                    (async ds patch)
    - text_delta: {index, text}
      - done: {full result dict, same shape as _run_cc_favilla_chat return}
      - error: {message}
    """
    import queue
    import subprocess
    import threading

    from fiam_lib.app_markers import split_cot_segments

    pending_recall_context = _pop_pending_recall_context()
    pending_recall = _render_recall_for_app(pending_recall_context)
    session = _load_app_active_session()
    prompt_text = text
    if attachments:
        lines = ["[attachments]"]
        for a in attachments:
            mime = a.get("mime") or ""
            lines.append(f"- obj:{a.get('object_hash', '')}  (name={a['name']!r}, mime={mime}, size={a.get('size')})")
        lines.append("")
        prompt_text = "\n".join(lines) + text
    warm_system_context, warm_user_prompt = _build_cc_favilla_prompt_parts(
        prompt_text,
        channel=channel,
        recall_context=pending_recall_context,
        warm=True,
    )
    use_warm_holder = {"enabled": _cc_warm_enabled()}
    cold_command_cache: dict[str, list[str]] = {}
    cwd_path = _CONFIG.home_path

    def cold_base_command() -> list[str]:
        command = cold_command_cache.get("command")
        if command is None:
            _cc_warm_shutdown_locked("cold stream fallback")
            _cc_scrub_hook_transcript(session)
            cold_system_context, cold_user_prompt = _build_cc_favilla_prompt_parts(
                prompt_text,
                channel=channel,
                recall_context=pending_recall_context,
                warm=False,
            )
            command = _cc_chat_command(user_prompt=cold_user_prompt, system_context=cold_system_context, input_stream=False)
            cold_command_cache["command"] = command
        return list(command)

    yield {"event": "start", "data": {"runtime": "cc"}}

    ev_queue: "queue.Queue[dict]" = queue.Queue()

    state = {
        "thought_idx": 0,
        "text_idx": 0,
        "tool_names": {},
        "raw_reply_parts": [],
        "result_data": {},
        "action_events": [],
        "thinking_events": [],
        "stream_text_buffer": "",
    }

    def emit_text_delta(text_part: str) -> None:
        clean = str(text_part or "")
        if not clean:
            return
        ev_queue.put({"event": "text_delta", "data": {"index": state["text_idx"], "text": clean}})
        state["text_idx"] += 1

    def emit_text_and_thoughts(chunk: str):
        """Emit only marker-safe visible text.

        Once a possible XML marker starts, the remainder is buffered until the
        final MarkerInterpreter pass. This sacrifices some live text after a
        marker, but prevents control XML such as <hold>, <todo>, or <send>
        from leaking into the UI as assistant text.
        """
        state["stream_text_buffer"] = str(state.get("stream_text_buffer") or "") + chunk
        while True:
            buffered = str(state.get("stream_text_buffer") or "")
            if not buffered:
                return
            marker_start = buffered.find("<")
            if marker_start < 0:
                state["stream_text_buffer"] = ""
                for kind, body in split_cot_segments(buffered):
                    if kind == "text":
                        emit_text_delta(body)
                return
            if marker_start > 0:
                prefix = buffered[:marker_start]
                state["stream_text_buffer"] = buffered[marker_start:]
                for kind, body in split_cot_segments(prefix):
                    if kind == "text":
                        emit_text_delta(body)
                continue
            return

    def reader_for(proc: "subprocess.Popen[str]"):
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                item_type = item.get("type")
                if item_type == "result":
                    state["result_data"] = item
                    continue
                if item_type == "assistant":
                    message = item.get("message") if isinstance(item.get("message"), dict) else {}
                    for block in message.get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "tool_use":
                            tool_id = str(block.get("id") or "")
                            name = str(block.get("name") or "tool")
                            state["tool_names"][tool_id] = name
                            input_data = block.get("input") if isinstance(block.get("input"), dict) else {}
                            summary = _summarize_cc_tool_input(name, input_data)
                            state["action_events"].append({
                                "kind": "tool_use",
                                "tool_use_id": tool_id,
                                "tool_name": name,
                                "summary": summary,
                            })
                            ev_queue.put({"event": "tool_use", "data": {
                                "tool_use_id": tool_id,
                                "tool_name": name,
                                "input_summary": summary,
                            }})
                        elif btype == "text":
                            text_chunk = str(block.get("text") or "")
                            if text_chunk:
                                state["raw_reply_parts"].append(text_chunk)
                                emit_text_and_thoughts(text_chunk)
                        elif btype == "thinking":
                            think_text = str(block.get("thinking") or "").strip()
                            if think_text:
                                ev = {
                                    "text": think_text,
                                    "signature": str(block.get("signature") or ""),
                                }
                                state["thinking_events"].append(ev)
                                idx = state["thought_idx"]
                                state["thought_idx"] += 1
                                ev_queue.put({"event": "thought", "data": {
                                    "index": idx,
                                    "text": think_text,
                                    "summary": think_text[:160],
                                    "source": "official",
                                    "locked": False,
                                    "icon": "NativeThinking",
                                }})
                    continue
                if item_type == "user":
                    result = item.get("tool_use_result") if isinstance(item.get("tool_use_result"), dict) else {}
                    message = item.get("message") if isinstance(item.get("message"), dict) else {}
                    content = message.get("content") or []
                    tool_id = ""
                    content_text = ""
                    if content and isinstance(content[0], dict):
                        tool_id = str(content[0].get("tool_use_id") or "")
                        content_text = str(content[0].get("content") or "")
                    name = state["tool_names"].get(tool_id, "tool")
                    stdout_text = result.get("stdout") or content_text
                    stderr_text = result.get("stderr") or ""
                    output = stdout_text if stdout_text else stderr_text
                    is_error = bool(result.get("is_error")) or bool(result.get("isError")) or bool(stderr_text and not stdout_text)
                    summary = _redact_cc_snippet(output, limit=500)
                    state["action_events"].append({
                        "kind": "tool_result",
                        "tool_use_id": tool_id,
                        "tool_name": name,
                        "is_error": is_error,
                        "summary": summary,
                        "output_text": str(output or ""),
                    })
                    ev_queue.put({"event": "tool_result", "data": {
                        "tool_use_id": tool_id,
                        "tool_name": name,
                        "result_summary": summary,
                        "is_error": is_error,
                    }})
        finally:
            ev_queue.put({"event": "_eof", "data": {}})

    def run_claude(resume_session_id: str | None) -> "subprocess.Popen[str]":
        if _cc_channel_transport_enabled():
            result = _run_cc_channel_turn_result_locked(
                warm_user_prompt,
                warm_system_context,
                resume_session_id=resume_session_id or "",
                max_turns=10,
            )
            return _CCCompletedTurnProcess(result.stdout, result.stderr, result.returncode)
        if use_warm_holder["enabled"]:
            runner = _cc_warm_runner_locked(warm_system_context)
            return _CCWarmTurnProcess(runner, warm_user_prompt)
        cmd = cold_base_command()
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd_path),
        )

    def consume_until_eof(proc: "subprocess.Popen[str]"):
        """Drive reader thread + drain queue, yielding events. Returns rc."""
        rt = threading.Thread(target=reader_for, args=(proc,), daemon=True)
        rt.start()
        deadline = time.time() + 240.0
        while True:
            try:
                ev = ev_queue.get(timeout=1.0)
            except queue.Empty:
                if time.time() > deadline:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    yield {"event": "error", "data": {"message": "claude chat timeout"}}
                    return
                continue
            if ev.get("event") == "_eof":
                break
            yield ev
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        while True:
            try:
                ev = ev_queue.get_nowait()
            except queue.Empty:
                break
            if ev.get("event") != "_eof":
                yield ev

    def reset_stream_state() -> None:
        state["result_data"] = {}
        state["action_events"] = []
        state["thinking_events"] = []
        state["tool_names"] = {}
        state["raw_reply_parts"] = []
        state["thought_idx"] = 0
        state["text_idx"] = 0
        state["stream_text_buffer"] = ""

    resume_id = session["session_id"] if session else None
    proc = run_claude(resume_id)
    rc_holder = {"rc": 0, "stderr": ""}
    for ev in consume_until_eof(proc):
        if ev.get("event") == "error":
            yield ev
            return
        yield ev
    rc_holder["rc"] = proc.returncode or 0
    try:
        rc_holder["stderr"] = (proc.stderr.read() if proc.stderr else "") or ""
    except Exception:
        pass

    data = state["result_data"]
    is_partial_success = data.get("subtype") == "error_max_turns"
    is_error = bool(data.get("is_error")) or rc_holder["rc"] != 0

    # Resume failure → retry without --resume.
    if (is_error and not is_partial_success) or not data:
        detail = (data.get("error") or data.get("result") or rc_holder["stderr"] or "")
        if use_warm_holder["enabled"] and str(detail).startswith(_CC_WARM_FAILURE_PREFIX):
            _cc_warm_shutdown_locked(str(detail), scrub=True)
            use_warm_holder["enabled"] = False
            reset_stream_state()
            proc2 = run_claude(resume_id)
            for ev in consume_until_eof(proc2):
                if ev.get("event") == "error":
                    yield ev
                    return
                yield ev
            rc_holder["rc"] = proc2.returncode or 0
            try:
                rc_holder["stderr"] = (proc2.stderr.read() if proc2.stderr else "") or ""
            except Exception:
                rc_holder["stderr"] = ""
            data = state["result_data"]
            is_partial_success = data.get("subtype") == "error_max_turns"
            is_error = bool(data.get("is_error")) or rc_holder["rc"] != 0
            detail = (data.get("error") or data.get("result") or rc_holder["stderr"] or "")
        if resume_id and _is_cc_resume_recoverable_error(detail):
            _clear_app_active_session_locked()
            # Reset state and try again with no resume.
            reset_stream_state()
            proc2 = run_claude(None)
            for ev in consume_until_eof(proc2):
                if ev.get("event") == "error":
                    yield ev
                    return
                yield ev
            rc_holder["rc"] = proc2.returncode or 0
            data = state["result_data"]
            is_partial_success = data.get("subtype") == "error_max_turns"
            is_error = bool(data.get("is_error")) or rc_holder["rc"] != 0
        if (is_error and not is_partial_success) or not data:
            msg = str(data.get("error") or data.get("result") or rc_holder["stderr"] or "claude failed").strip()[:500]
            yield {"event": "error", "data": {"message": msg or "claude failed"}}
            return

    session_id = str(data.get("session_id") or "").strip()
    if session_id:
        _save_app_active_session(session_id)

    reply = str(data.get("result") or "").strip()
    raw_reply = reply
    interpretation = _interpret_app_control_markers(reply)
    reply = interpretation.visible_reply
    queued_todos = [
        {"at": todo.at, "kind": todo.kind, "reason": todo.reason}
        for todo in interpretation.todo_changes
    ]
    hold = _app_hold_payload(interpretation)
    hold_reason = interpretation.hold_reason
    route = interpretation.route_hint
    cleaned_reply, thoughts, thoughts_locked, segments = _parse_cot(reply)
    thinking_events = state.get("thinking_events") or []
    native_thoughts, native_segments = _official_thought_payloads(thinking_events)
    if native_thoughts:
        thoughts = native_thoughts + thoughts
        segments = native_segments + list(segments)
    if hold and not segments:
        thoughts_locked = True
        summary = hold_reason or "holding this reply"
        thoughts = [{"kind": "think", "text": summary, "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
        segments = [{"type": "thought", "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
    action_events = _combine_cc_action_events(state["action_events"])
    tool_segments: list[dict] = []
    for action in action_events or []:
        tool_id = str(action.get("tool_use_id") or "")
        tool_name = str(action.get("tool_name") or "tool")
        if action.get("input_summary"):
            tool_segments.append({
                "type": "tool_use",
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "input_summary": str(action.get("input_summary") or ""),
            })
        if action.get("result_summary") or action.get("is_error"):
            tool_segments.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "tool_name": tool_name,
                "result_summary": str(action.get("result_summary") or ""),
                "is_error": bool(action.get("is_error")),
            })
    enriched_segments = tool_segments + list(segments)
    metrics = _normalize_metrics(
        runtime="cc",
        model=str(data.get("model") or _CONFIG.cc_model or ""),
        usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
        latency_ms=int(data.get("duration_ms") or 0) or None,
        cost_usd=data.get("total_cost_usd"),
    )
    actions_list: list[dict] = []
    for todo in queued_todos or []:
        actions_list.append({"kind": "queued_todo", **(todo if isinstance(todo, dict) else {"text": str(todo)})})
    if route:
        actions_list.append({"kind": "route", **(route if isinstance(route, dict) else {"value": str(route)})})
    _record_cc_app_turn(prompt_text, raw_reply, channel, surface=surface, action_events=action_events, thinking_events=thinking_events, session_id=session_id, record_user=False, turn_id=turn_id, request_id=request_id)
    try:
        _record_debug_context("cc", metrics=metrics, session_id=session_id, channel=channel)
    except Exception:
        logger.exception("debug context record (cc stream) failed")
    final = {
        "ok": True,
        "runtime": "cc",
        "reply": cleaned_reply,
        "raw_reply": raw_reply,
        "recall": pending_recall,
        "session_id": session_id,
        "subtype": data.get("subtype"),
        "cost_usd": data.get("total_cost_usd", 0),
        "thoughts": thoughts,
        "thoughts_locked": thoughts_locked,
        "segments": enriched_segments,
        "hold": hold,
        "queued_todos": queued_todos,
        "route": route,
        "actions": action_events,
        "tool_calls_summary": action_events or [],
        "actions_list": actions_list,
        "metrics": metrics,
    }
    yield {"event": "done", "data": final}


def _app_flow_conductor():
    if not _CONFIG or not _POOL:
        return None
    from fiam.conductor import Conductor

    class DeferredEmbedder:
        def embed(self, _text: str):
            raise RuntimeError("embedding deferred to MemoryWorker")

    return Conductor(
        pool=_POOL,
        embedder=DeferredEmbedder() if getattr(_CONFIG, "memory_mode", "manual") == "manual" else _get_embedder(),
        config=_CONFIG,
        flow_path=_CONFIG.flow_path,
        drift_threshold=_CONFIG.drift_threshold,
        gorge_max_beat=_CONFIG.gorge_max_beat,
        gorge_min_depth=_CONFIG.gorge_min_depth,
        gorge_stream_confirm=_CONFIG.gorge_stream_confirm,
        memory_mode=_CONFIG.memory_mode,
    )


def _run_memory_worker_once(*, limit: int = 1000) -> int:
    if not _CONFIG:
        return 0
    from fiam.store.events import EventStore
    from fiam.store.features import FeatureStore
    from fiam.turn import MemoryWorker

    event_store = EventStore(_CONFIG.event_db_path, object_dir=_CONFIG.object_dir)
    feature_store = FeatureStore(_CONFIG.feature_dir, dim=_CONFIG.embedding_dim)
    worker = MemoryWorker(
        event_store,
        embedder=_get_embedder(),
        feature_store=feature_store,
        pool=_POOL,
        config=_CONFIG,
        model_id=getattr(_CONFIG, "embedding_model", ""),
    )
    return worker.process_once(limit=limit)


def _record_app_user_flow(user_text: str, channel: str, *, surface: str = "", turn_id: str = "", request_id: str = "") -> None:
    conductor = _app_flow_conductor()
    if conductor is None:
        return
    from fiam.runtime.turns import user_beat
    from fiam.turn import TurnCommit

    now = datetime.now(timezone.utc)
    beat = user_beat(
        user_text,
        t=now,
        channel=channel,
        user_name=getattr(_CONFIG, "user_name", "") or "zephyr",
        surface=_surface_for_app_channel(channel, surface),
    )
    if turn_id or request_id:
        meta = dict(beat.meta or {})
        if turn_id:
            meta["turn_id"] = turn_id
        if request_id:
            meta["request_id"] = request_id
        beat = replace(beat, meta=meta)
    conductor.commit_turn(TurnCommit(
        turn_id=turn_id or f"turn_{uuid.uuid4().hex}",
        request_id=request_id,
        events=(beat,),
        trace={"received": now.isoformat()},
    ), channel=channel)


def _record_app_error_flow(message: str, channel: str, *, surface: str = "", runtime: str = "", turn_id: str = "", request_id: str = "") -> None:
    conductor = _app_flow_conductor()
    if conductor is None:
        return
    from fiam.store.beat import Beat
    from fiam.turn import TurnCommit

    meta = {"error": True}
    if turn_id:
        meta["turn_id"] = turn_id
    if request_id:
        meta["request_id"] = request_id
    beat = Beat(
        t=datetime.now(timezone.utc),
        actor="ai",
        channel=channel,
        kind="message",
        content=f"error: {message}"[:2000],
        runtime=(runtime or None),
        meta=meta,
        surface=_surface_for_app_channel(channel, surface),
    )
    conductor.commit_turn(TurnCommit(
        turn_id=turn_id or f"turn_{uuid.uuid4().hex}",
        request_id=request_id,
        events=(beat,),
        trace={"error": str(message)[:500]},
    ), channel=channel)


def _record_cc_app_turn(user_text: str, assistant_reply: str, channel: str, *, surface: str = "", action_events: list[dict] | None = None, thinking_events: list[dict] | None = None, session_id: str = "", record_user: bool = True, turn_id: str = "", request_id: str = "") -> None:
    conductor = _app_flow_conductor()
    if conductor is None:
        return
    from fiam.runtime.turns import assistant_text_beats
    from fiam.store.beat import Beat
    from fiam.turn import MarkerInterpreter, TurnCommit

    commit_turn_id = turn_id or f"turn_{uuid.uuid4().hex}"
    surface = _surface_for_app_channel(channel, surface)
    if record_user:
        _record_app_user_flow(user_text, channel, surface=surface, turn_id=commit_turn_id, request_id=request_id)
    trace_meta = {}
    if turn_id:
        trace_meta["turn_id"] = turn_id
    if request_id:
        trace_meta["request_id"] = request_id
    # Native thinking beats — Anthropic extended-thinking blocks captured from
    # cc stream-json. Emitted before tool actions so flow order matches model
    # output order (think first, then act, then answer).
    beats = []
    for ev in thinking_events or []:
        text_t = str(ev.get("text") or "").strip()
        if not text_t:
            continue
        beats.append(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel,
            kind="think",
            content=text_t,
            runtime="cc",
            meta={"source": "official", "name": "official", "session_id": session_id, **trace_meta},
            surface=surface,
        ))
    for action in action_events or []:
        kind = str(action.get("kind") or "tool")
        name = str(action.get("tool_name") or "tool")
        result_summary = str(action.get("result_summary") or "").strip()
        result_full = str(action.get("result_full") or "")
        _bounded_result, result_object_hash, result_size = _bounded_cc_tool_result(result_full or result_summary)
        is_error = bool(action.get("is_error"))
        if kind == "tool_action":
            input_summary = str(action.get("input_summary") or "").strip()
            text = f"action: {name}" + (f" — {input_summary}" if input_summary else "")
        else:
            summary = str(action.get("summary") or "").strip()
            if kind == "tool_use":
                text = f"action: {name}" + (f" — {summary}" if summary else "")
            elif kind == "tool_result":
                # standalone tool_result event (rare — combined path puts it on tool_action)
                prefix = "error" if is_error else "result"
                text = f"{prefix}: {name}" + (f" — {summary}" if summary else "")
            else:
                continue
        beats.append(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel,
            kind="action",
            content=text,
            runtime="cc",
            meta={"tool": name, "name": name, "session_id": session_id, **trace_meta},
            surface=surface,
        ))
        # paired result beat: emit immediately after the action beat so flow
        # readers (and DS summarizer) see both call and outcome in order.
        if kind == "tool_action" and (result_summary or is_error):
            prefix = "error" if is_error else "result"
            result_text = f"{prefix}: {name}" + (f" — {result_summary}" if result_summary else " — (no output)")
            beats.append(Beat(
                t=datetime.now(timezone.utc),
                actor="ai",
                channel=channel,
                kind="tool_result",
                content=result_text,
                runtime="cc",
                meta={
                    "tool": name,
                    "name": name,
                    "is_error": is_error,
                    "session_id": session_id,
                    **({
                        "object_hash": result_object_hash,
                        "object_mime": "text/plain",
                        "object_name": f"{name}-result.txt",
                        "object_size": result_size,
                    } if result_object_hash else {}),
                    **trace_meta,
                },
                surface=surface,
            ))
    interpretation = MarkerInterpreter(object_resolver=_object_token_resolver()).interpret(assistant_reply)
    for beat in assistant_text_beats(
        assistant_reply,
        t=datetime.now(timezone.utc),
        channel=channel,
        runtime="cc",
        surface=surface,
    ):
        if session_id or trace_meta:
            meta = dict(beat.meta or {})
            if session_id:
                meta.setdefault("session_id", session_id)
            meta.update(trace_meta)
            beat = replace(beat, meta=meta)
        beats.append(beat)
    transcript_messages = [*_cc_tool_transcript_messages(action_events or []), {"role": "assistant", "content": interpretation.visible_reply}]
    if record_user:
        transcript_messages.insert(0, {"role": "user", "content": user_text.strip()})
    if not interpretation.visible_reply:
        transcript_messages = [msg for msg in transcript_messages if msg["role"] != "assistant"]
    conductor.commit_turn(TurnCommit(
        turn_id=commit_turn_id,
        request_id=request_id,
        session_id=session_id,
        surface=surface,
        events=tuple(beats),
        transcript_messages=tuple(transcript_messages),
        dispatch_requests=interpretation.dispatch_requests,
        todo_changes=interpretation.todo_changes,
        state_change=interpretation.state_change,
        hold_request=interpretation.hold_request,
        trace={"model_done": datetime.now(timezone.utc).isoformat()},
    ), channel=channel)


def _cc_tool_transcript_messages(action_events: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for index, action in enumerate(action_events or []):
        if str(action.get("kind") or "") != "tool_action":
            continue
        tool_name = _clean_tool_name(str(action.get("tool_name") or "tool"))
        tool_id = str(action.get("tool_use_id") or f"cc_tool_{index}")
        input_summary = str(action.get("input_summary") or "")[:2000]
        result_text = str(action.get("result_full") or action.get("result_summary") or "")
        bounded_result, _object_hash, _size = _bounded_cc_tool_result(result_text)
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps({"summary": input_summary}, ensure_ascii=False),
                },
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": tool_id,
            "name": tool_name,
            "content": bounded_result,
        })
    return messages


def _bounded_cc_tool_result(text: str, *, limit: int = 4000) -> tuple[str, str, int]:
    raw = str(text or "")
    size = len(raw.encode("utf-8"))
    if size <= limit or not _CONFIG:
        return raw, "", size
    from fiam.store.objects import ObjectStore

    object_hash = ObjectStore(_CONFIG.object_dir).put_text(raw, suffix=".txt")
    preview = raw[:limit].rstrip()
    bounded = (
        f"{preview}\n\n"
        f"[object_ref hash={object_hash} size={size} mime=text/plain reason=cc_tool_result_truncated]"
    )
    return bounded, object_hash, size


def _clean_tool_name(name: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(name or "tool"))
    return clean.strip("_") or "tool"


def _record_api_turn_light(user_text: str, assistant_reply: str, channel: str, *, surface: str = "", tool_calls: list[dict] | None = None, family: str = "", turn_id: str = "", request_id: str = "", transcript_messages: list[dict] | None = None, record_user: bool = True) -> None:
    if not _CONFIG:
        return
    from fiam.runtime.turns import assistant_text_beats, user_beat
    from fiam.store.beat import Beat
    from fiam.turn import MarkerInterpreter, TurnCommit

    conductor = _app_flow_conductor()
    if conductor is None:
        return
    runtime_tag = (family or "").strip().lower() or None
    surface = _surface_for_app_channel(channel, surface)
    trace_meta = {}
    if turn_id:
        trace_meta["turn_id"] = turn_id
    if request_id:
        trace_meta["request_id"] = request_id
    now = datetime.now(timezone.utc)
    beats = []
    if record_user:
        user = user_beat(
            user_text,
            t=now,
            channel=channel,
            user_name=getattr(_CONFIG, "user_name", "") or "zephyr",
            surface=surface,
        )
        if trace_meta:
            user = replace(user, meta={**(user.meta or {}), **trace_meta})
        beats.append(user)
    for call in tool_calls or []:
        tool_name = str(call.get("tool_name") or "tool")
        input_summary = str(call.get("input_summary") or "").replace("\n", " ")[:300]
        beats.append(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel,
            kind="action",
            content=f"action: {tool_name}" + (f" — {input_summary}" if input_summary else ""),
            runtime=runtime_tag,
            meta={"tool": tool_name, **trace_meta},
            surface=surface,
        ))
        result_summary = str(call.get("result_summary") or "").replace("\n", " ")[:500]
        if result_summary or call.get("result_object_hash"):
            meta = {
                "tool": tool_name,
                "name": tool_name,
                **trace_meta,
            }
            if call.get("result_object_hash"):
                meta["object_hash"] = str(call.get("result_object_hash") or "")
                meta["object_mime"] = "text/plain"
                meta["object_name"] = f"{tool_name}-result.txt"
                meta["object_size"] = int(call.get("result_size") or 0)
            beats.append(Beat(
                t=datetime.now(timezone.utc),
                actor="ai",
                channel=channel,
                kind="tool_result",
                content=f"result: {tool_name}" + (f" — {result_summary}" if result_summary else ""),
                runtime=runtime_tag,
                meta=meta,
                surface=surface,
            ))
    interpretation = MarkerInterpreter(object_resolver=_object_token_resolver()).interpret(assistant_reply)
    assistant_beats = assistant_text_beats(
        assistant_reply,
        t=datetime.now(timezone.utc),
        channel=channel,
        runtime=runtime_tag,
        surface=surface,
    )
    if trace_meta:
        assistant_beats = [replace(beat, meta={**(beat.meta or {}), **trace_meta}) for beat in assistant_beats]
    beats.extend(assistant_beats)
    conductor.commit_turn(TurnCommit(
        turn_id=turn_id or f"turn_{uuid.uuid4().hex}",
        surface=surface,
        request_id=request_id,
        events=tuple(beats),
        transcript_messages=tuple(transcript_messages or (
            msg for msg in (
                {"role": "user", "content": user_text.strip()} if record_user else None,
                {"role": "assistant", "content": interpretation.visible_reply},
            )
            if msg is not None and (msg["role"] != "assistant" or interpretation.visible_reply)
        )),
        dispatch_requests=interpretation.dispatch_requests,
        todo_changes=interpretation.todo_changes,
        state_change=interpretation.state_change,
        hold_request=interpretation.hold_request,
        trace={"model_done": datetime.now(timezone.utc).isoformat()},
    ), channel=channel)


def _run_api_favilla_chat(*, text: str, channel: str, surface: str = "", attachments: list | None = None, record_turn: bool = True, family: str = "", turn_id: str = "", request_id: str = "") -> dict:
    if not _CONFIG or not _POOL:
        raise RuntimeError("config not loaded")
    config = _config_for_catalog_family(_CONFIG, family)
    surface = _surface_for_app_channel(channel, surface)

    from fiam.runtime.api import ApiRuntime

    runtime = ApiRuntime.from_config(
        config,
    )
    api_text = text
    if attachments:
        lines = ["[attachments]"]
        for a in attachments:
            mime = a.get("mime") or ""
            lines.append(f"- obj:{a.get('object_hash', '')}  (name={a['name']!r}, mime={mime}, size={a.get('size')})")
        lines.append("")
        api_text = "\n".join(lines) + text
    # build_api_messages loads the shared API-message transcript for this channel.
    # Keep dashboard-specific context here, but do not duplicate chat history.
    prompt_started = datetime.now(timezone.utc)
    pending_recall_context = _pop_pending_recall_context()
    pending_recall = _render_recall_for_app(pending_recall_context)
    extras = _app_runtime_context()
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.prompt",
        started_at=prompt_started,
        refs={"runtime": "api", "family": family, "attachment_count": len(attachments or []), "extra_context": bool(extras)},
    )
    runtime_started = datetime.now(timezone.utc)
    api_started_at = time.time()
    try:
        result = runtime.ask(
            api_text,
            channel=channel,
            recall_context=pending_recall_context,
            extra_context=extras,
            image_attachments=attachments or [],
        )
    except Exception as exc:
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.runtime",
            started_at=runtime_started,
            status="error",
            error=str(exc)[:1000],
            refs={"runtime": "api", "family": family},
        )
        raise
    api_latency_ms = int((time.time() - api_started_at) * 1000)
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.runtime",
        started_at=runtime_started,
        refs={"runtime": "api", "family": family, "model": getattr(result, "model", ""), "latency_ms": api_latency_ms},
    )
    raw_reply = str(result.reply or "")
    marker_started = datetime.now(timezone.utc)
    interpretation = _interpret_app_control_markers(result.reply)
    used_reroll = False
    if interpretation.hold_status == "reroll":
        used_reroll = True
        if record_turn:
            _record_api_turn_light(api_text, raw_reply, channel=channel, surface=surface, family=family, turn_id=turn_id, request_id=request_id, transcript_messages=[], record_user=False)
        reroll_started = datetime.now(timezone.utc)
        reroll_api_started_at = time.time()
        try:
            result = runtime.ask(
                _hold_reroll_prompt(api_text, interpretation.hold_reason),
                channel=channel,
                recall_context=pending_recall_context,
                extra_context=extras,
                image_attachments=attachments or [],
            )
        except Exception as exc:
            _append_dashboard_trace_phase(
                turn_id=turn_id,
                request_id=request_id,
                channel=channel,
                surface=surface,
                phase="dashboard.runtime",
                started_at=reroll_started,
                status="error",
                error=str(exc)[:1000],
                refs={"runtime": "api", "family": family, "reroll": True},
            )
            raise
        api_latency_ms += int((time.time() - reroll_api_started_at) * 1000)
        _append_dashboard_trace_phase(
            turn_id=turn_id,
            request_id=request_id,
            channel=channel,
            surface=surface,
            phase="dashboard.runtime",
            started_at=reroll_started,
            refs={"runtime": "api", "family": family, "model": getattr(result, "model", ""), "latency_ms": api_latency_ms, "reroll": True},
        )
        raw_reply = str(result.reply or "")
        interpretation = _interpret_app_control_markers(result.reply)
    reply = interpretation.visible_reply
    queued_todos = [
        {"at": todo.at, "kind": todo.kind, "reason": todo.reason}
        for todo in interpretation.todo_changes
    ]
    hold = _app_hold_payload(interpretation)
    hold_reason = interpretation.hold_reason
    route = interpretation.route_hint
    _append_dashboard_trace_phase(
        turn_id=turn_id,
        request_id=request_id,
        channel=channel,
        surface=surface,
        phase="dashboard.marker",
        started_at=marker_started,
        refs={"todo_count": len(queued_todos), "hold": bool(hold), "route": bool(route)},
    )
    cleaned_reply, thoughts, thoughts_locked, segments = _parse_cot(reply)
    if hold and not segments:
        thoughts_locked = True
        summary = hold_reason or "holding this reply"
        thoughts = [{"kind": "think", "text": summary, "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
        segments = [{"type": "thought", "summary": summary, "source": "fiam", "locked": True, "icon": "Clock3"}]
    # OpenRouter conveniently returns usage.cost; other providers may not.
    api_cost = None
    if isinstance(result.usage, dict):
        api_cost = result.usage.get("cost") or (result.usage.get("cost_details") or {}).get("upstream_inference_cost")
    metrics = _normalize_metrics(
        runtime="api",
        model=result.model,
        usage=result.usage if isinstance(result.usage, dict) else None,
        latency_ms=api_latency_ms,
        cost_usd=api_cost,
    )
    if isinstance(getattr(result, "timings", None), dict) and result.timings:
        metrics["timings"] = result.timings
    actions_list: list[dict] = []
    # Surface api tool invocations (Step 6 schema)
    api_tool_calls = list(getattr(result, "tool_calls", None) or [])
    api_tool_calls_summary: list[dict] = []
    for call in api_tool_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "")
        api_tool_calls_summary.append({
            "tool_name": name,
            "tool_id": call.get("id") or "",
            "input_summary": str(call.get("arguments") or "")[:300],
            "result_summary": str(call.get("result_preview") or "")[:500],
            "result_object_hash": str(call.get("result_object_hash") or ""),
            "result_size": call.get("result_size") or 0,
            "loop": call.get("loop"),
        })
        actions_list.append({
            "kind": "api_tool",
            "tool_name": name,
            "tool_id": call.get("id") or "",
            "arguments": str(call.get("arguments") or "")[:300],
            "result_preview": call.get("result_preview") or "",
            "loop": call.get("loop"),
        })
    if record_turn:
        transcript_messages = None if used_reroll else list(getattr(result, "transcript_messages", None) or [])
        _record_api_turn_light(api_text, raw_reply, channel=channel, surface=surface, tool_calls=api_tool_calls_summary, family=family, turn_id=turn_id, request_id=request_id, transcript_messages=transcript_messages)
    for todo in queued_todos or []:
        actions_list.append({"kind": "queued_todo", **(todo if isinstance(todo, dict) else {"text": str(todo)})})
    if route:
        actions_list.append({"kind": "route", **(route if isinstance(route, dict) else {"value": str(route)})})
    try:
        _record_debug_context("api", metrics=metrics, channel=channel)
    except Exception:
        logger.exception("debug context record (api) failed")
    return {
        "ok": True,
        "runtime": "api",
        "reply": cleaned_reply,
        "raw_reply": raw_reply,
        "recall": pending_recall,
        "session_id": "",
        "subtype": None,
        "cost_usd": 0,
        "model": result.model,
        "usage": result.usage,
        "recall_fragments": result.recall_fragments,
        "dispatched": result.dispatched,
        "thoughts": thoughts,
        "thoughts_locked": thoughts_locked,
        "segments": segments,
        "hold": hold,
        "queued_todos": queued_todos,
        "route": route,
        # Step 6: structured fields for transcript
        "tool_calls_summary": api_tool_calls_summary,
        "actions_list": actions_list,
        "metrics": metrics,
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
        "started_at": _CONFIG.now_local().isoformat(),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _render_recall_for_app(context, max_chars: int = 1400) -> str:
    if context is None:
        return ""
    try:
        return context.render(max_chars=max_chars)
    except Exception:
        return ""


def _pop_pending_recall_context():
    global _PENDING_RECALL_CONTEXT
    with _RECALL_LOCK:
        context = _PENDING_RECALL_CONTEXT
        _PENDING_RECALL_CONTEXT = None
    return context


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


def _normalize_app_beat_dict(item: dict) -> dict:
    channel = str(item.get("channel") or "").strip().lower()
    surface = str(item.get("surface") or "").strip().lower()
    if channel in {"favilla", "app"}:
        item = dict(item)
        item["channel"] = "chat"
        item["surface"] = "favilla" if not surface or surface in {"favilla", "app"} or surface.startswith("favilla.") else surface
    elif surface == "app" or surface.startswith("favilla."):
        item = dict(item)
        item["surface"] = "favilla"
    elif surface.startswith("atrium."):
        item = dict(item)
        item["surface"] = "atrium"
    return item


def _api_flow(offset: int = 0, limit: int = 50) -> dict:
    """Read beats from the SQLite event store with pagination."""
    if not _CONFIG:
        return {"beats": [], "offset": 0, "total": 0}
    from fiam.store.beat import read_beats

    all_beats = read_beats(_CONFIG.flow_path)
    total = len(all_beats)
    # Return from the end (most recent) if offset is 0
    if offset <= 0:
        start = max(0, total - limit)
    else:
        start = offset
    end = min(start + limit, total)

    beats = [_normalize_app_beat_dict(beat.to_dict()) for beat in all_beats[start:end]]
    return {"beats": beats, "offset": start, "total": total}


def _constant_time_equal(got: str, expected: str) -> bool:
    """Constant-time string compare that is safe for non-ASCII inputs."""
    import hmac
    if not got or not expected:
        return False
    try:
        got_bytes = got.encode("utf-8", "surrogatepass")
        expected_bytes = expected.encode("utf-8", "surrogatepass")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(got_bytes, expected_bytes)


def _ingest_token_ok(handler) -> bool:
    """Constant-time comparison of X-Fiam-Token header against env secret."""
    import os
    expected = os.environ.get("FIAM_INGEST_TOKEN", "")
    if not expected:
        return False
    got = handler.headers.get("X-Fiam-Token", "")
    if not got:
        return False
    return _constant_time_equal(got, expected)


# ---------------------------------------------------------------------------
# Studio vault (markdown + git, design v0.1)
# ---------------------------------------------------------------------------
# Endpoints (POST /studio/share, POST /studio/quicknote, GET /studio/list,
# GET /studio/file) write markdown into a vault directory and (best-effort)
# git-commit each change. Vault path defaults to <home>/studio but can be
# overridden via FIAM_STUDIO_VAULT_DIR (used for tests / local dev).
# All endpoints require X-Fiam-Token (FIAM_INGEST_TOKEN).

import os as _studio_os
import subprocess as _studio_subprocess


_STUDIO_DEFAULT_DIRS = ("shelf", "desk")
_STUDIO_PRIVATE_INBOX_LABEL = "ai-inbox"
_STUDIO_AGENT_VALUES = {"zephyr", "cc", "copilot", "codex", "ai", "system"}
_STUDIO_SOURCE_VALUES = {"atrium", "favilla", "quicknote", "manual", "email", "obsidian"}
_STUDIO_MAX_TEXT = 1024 * 1024  # 1 MiB per write
_STUDIO_LIST_LIMIT = 200


def _studio_vault_dir() -> Path:
    override = _studio_os.environ.get("FIAM_STUDIO_VAULT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    return (_CONFIG.home_path / "studio").resolve()


def _studio_ai_inbox_dir() -> Path:
    override = _studio_os.environ.get("FIAM_STUDIO_AI_INBOX_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _CONFIG:
        return (_CONFIG.home_path / "studio_ai_inbox").resolve()
    vault = _studio_vault_dir()
    return (vault.parent / f"{vault.name}-ai-inbox").resolve()


def _studio_ensure_git_repo(root: Path, subdirs: tuple[str, ...] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for sub in subdirs:
        (root / sub).mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        try:
            _studio_subprocess.run(
                ["git", "init", "--quiet"],
                cwd=str(root), check=False, capture_output=True, timeout=5,
            )
            _studio_subprocess.run(
                ["git", "config", "user.email", "studio@fiam.local"],
                cwd=str(root), check=False, capture_output=True, timeout=5,
            )
            _studio_subprocess.run(
                ["git", "config", "user.name", "fiam-studio"],
                cwd=str(root), check=False, capture_output=True, timeout=5,
            )
        except (OSError, _studio_subprocess.SubprocessError):
            pass
    return root


def _studio_ensure_vault() -> Path:
    vault = _studio_vault_dir()
    return _studio_ensure_git_repo(vault, _STUDIO_DEFAULT_DIRS)


def _studio_ensure_ai_inbox() -> Path:
    return _studio_ensure_git_repo(_studio_ai_inbox_dir())


def _studio_normalize_rel(rel: str) -> Path:
    """Resolve a vault-relative path safely. Raises ValueError on traversal."""
    if not rel or not isinstance(rel, str):
        raise ValueError("missing path")
    cleaned = rel.replace("\\", "/").lstrip("/")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid path")
    if any(part in {"", "..", "."} for part in cleaned.split("/")):
        raise ValueError("invalid path")
    if cleaned.startswith(".git/") or cleaned == ".git" or "/.git/" in f"/{cleaned}":
        raise ValueError("path inside .git")
    vault = _studio_ensure_vault()
    abs_path = (vault / cleaned).resolve()
    try:
        abs_path.relative_to(vault)
    except ValueError as exc:
        raise ValueError("path escapes vault") from exc
    return abs_path


def _studio_today_relpath(sub: str) -> str:
    sub = sub if sub in _STUDIO_DEFAULT_DIRS else "desk"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{sub}/{today}.md"


def _studio_today_ai_inbox_relpath() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d.md")


def _studio_default_target(source: str, has_url: bool) -> tuple[str, str]:
    if source == "quicknote":
        return "vault", _studio_today_relpath("desk")
    return "ai-inbox", _studio_today_ai_inbox_relpath()


def _studio_format_block(payload: dict) -> str:
    when = datetime.now(timezone.utc).astimezone().strftime("%H:%M")
    source = str(payload.get("source") or "manual").strip().lower() or "manual"
    if source not in _STUDIO_SOURCE_VALUES:
        source = "manual"
    agent = str(payload.get("agent") or "zephyr").strip().lower() or "zephyr"
    if agent not in _STUDIO_AGENT_VALUES:
        agent = "system"
    selection = str(payload.get("selection") or "").strip()
    note = str(payload.get("note") or "").strip()
    url = str(payload.get("url") or "").strip()
    lines = [f"## {when} · {source} · {agent}"]
    if selection:
        for sel_line in selection.splitlines() or [""]:
            lines.append(f"> {sel_line}" if sel_line else ">")
        lines.append("")
    if note:
        lines.append(note)
        lines.append("")
    meta = []
    if url:
        meta.append(f"source: {url}")
    tags = payload.get("tags")
    if isinstance(tags, list):
        clean_tags = [f"#{str(t).lstrip('#').strip()}" for t in tags if str(t).strip()]
        if clean_tags:
            meta.append("tags: " + " ".join(clean_tags))
    if meta:
        lines.extend(meta)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _studio_format_archive(payload: dict) -> str:
    source = str(payload.get("source") or "manual").strip().lower() or "manual"
    url = str(payload.get("url") or "").strip()
    agent = str(payload.get("agent") or "system").strip().lower() or "system"
    ts = datetime.now(timezone.utc).isoformat()
    tags = payload.get("tags")
    tag_list = []
    if isinstance(tags, list):
        tag_list = [str(t).lstrip("#").strip() for t in tags if str(t).strip()]
    front = ["---"]
    front.append(f"source: {source}")
    if url:
        front.append(f"url: {url}")
    front.append(f"ts: {ts}")
    front.append(f"agent: {agent}")
    if tag_list:
        front.append("tags: [" + ", ".join(tag_list) + "]")
    front.append("---")
    body = str(payload.get("selection") or payload.get("note") or "").strip()
    return "\n".join(front) + "\n\n" + body + "\n"


def _studio_git_commit(vault: Path, rel: str, message: str) -> str | None:
    """Best-effort git add+commit. Returns commit sha or None on failure."""
    try:
        add = _studio_subprocess.run(
            ["git", "add", "--", rel],
            cwd=str(vault), capture_output=True, timeout=10,
        )
        if add.returncode != 0:
            return None
        # If nothing staged (e.g. file unchanged), skip commit.
        diff = _studio_subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(vault), capture_output=True, timeout=10,
        )
        if diff.returncode == 0:
            return None
        commit = _studio_subprocess.run(
            ["git", "commit", "-m", message[:500]],
            cwd=str(vault), capture_output=True, timeout=15,
        )
        if commit.returncode != 0:
            return None
        sha = _studio_subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(vault), capture_output=True, text=True, timeout=5,
        )
        return (sha.stdout or "").strip() or None
    except (OSError, _studio_subprocess.SubprocessError):
        return None


def _studio_share(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("bad payload")
    source = str(payload.get("source") or "manual").strip().lower() or "manual"
    if source not in _STUDIO_SOURCE_VALUES:
        raise ValueError("unknown source")
    selection = str(payload.get("selection") or "")
    note = str(payload.get("note") or "")
    if not selection.strip() and not note.strip():
        raise ValueError("missing selection or note")
    if len(selection) + len(note) > _STUDIO_MAX_TEXT:
        raise ValueError("payload too large")
    target = str(payload.get("target_file") or "").strip()
    target_space = "vault"
    if not target:
        target_space, target = _studio_default_target(source, bool(str(payload.get("url") or "").strip()))
    cleaned_target = target.replace("\\", "/").lstrip("/")
    if cleaned_target == "inbox" or cleaned_target.startswith("inbox/"):
        raise ValueError("inbox is AI-private; omit target_file to send there")
    if not cleaned_target.lower().endswith(".md"):
        raise ValueError("target_file must end with .md")
    if target_space == "ai-inbox":
        inbox = _studio_ensure_ai_inbox()
        if any(part in {"", "..", "."} for part in cleaned_target.split("/")):
            raise ValueError("invalid path")
        abs_path = (inbox / cleaned_target).resolve()
        try:
            abs_path.relative_to(inbox)
        except ValueError as exc:
            raise ValueError("path escapes AI inbox") from exc
        rel = abs_path.relative_to(inbox).as_posix()
    else:
        abs_path = _studio_normalize_rel(cleaned_target)
        rel = abs_path.relative_to(_studio_vault_dir()).as_posix()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    is_private = target_space == "ai-inbox"
    is_archive = not is_private and rel.startswith("shelf/")
    if is_archive:
        if abs_path.exists():
            raise ValueError("shelf target already exists")
        body = _studio_format_archive(payload)
        abs_path.write_text(body, encoding="utf-8")
    else:
        block = _studio_format_block(payload)
        with abs_path.open("a", encoding="utf-8") as fh:
            if abs_path.stat().st_size > 0:
                fh.write("\n")
            fh.write(block)
    agent = str(payload.get("agent") or "system").strip().lower() or "system"
    repo = _studio_ai_inbox_dir() if is_private else _studio_vault_dir()
    display_rel = f"{_STUDIO_PRIVATE_INBOX_LABEL}/{rel}" if is_private else rel
    msg = f"studio: {source}/{agent} -> {display_rel}"
    sha = _studio_git_commit(repo, rel, msg)
    return {
        "ok": True,
        "rel_path": display_rel,
        "abs_path": str(abs_path),
        "commit_sha": sha,
        "archive": is_archive,
        "private": is_private,
    }


def _studio_quicknote(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("bad payload")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    return _studio_share({
        "source": "quicknote",
        "selection": text,
        "agent": payload.get("agent") or "zephyr",
        "tags": payload.get("tags"),
    })


def _studio_list(dir_filter: str = "", limit: int = 50) -> dict:
    vault = _studio_ensure_vault()
    try:
        limit = max(1, min(int(limit or 50), _STUDIO_LIST_LIMIT))
    except (TypeError, ValueError):
        limit = 50
    if dir_filter:
        try:
            scope = _studio_normalize_rel(dir_filter)
        except ValueError:
            scope = vault
    else:
        scope = vault
    files = []
    for fp in scope.rglob("*.md"):
        if ".git" in fp.parts:
            continue
        try:
            stat = fp.stat()
        except OSError:
            continue
        rel = fp.resolve().relative_to(vault).as_posix()
        files.append({
            "path": rel,
            "mtime": int(stat.st_mtime),
            "size": stat.st_size,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    files = files[:limit]
    log = []
    try:
        log_proc = _studio_subprocess.run(
            ["git", "log", f"--max-count={limit}", "--name-only", "--pretty=format:%H%x1f%ct%x1f%s"],
            cwd=str(vault), capture_output=True, text=True, timeout=10,
        )
        if log_proc.returncode == 0:
            current = None
            for raw_line in (log_proc.stdout or "").splitlines():
                if "\x1f" in raw_line:
                    if current:
                        log.append(current)
                    sha, ts, msg = (raw_line.split("\x1f", 2) + ["", ""])[:3]
                    current = {"sha": sha, "ts": int(ts) if ts.isdigit() else 0, "msg": msg, "files": []}
                elif raw_line.strip() and current is not None:
                    current["files"].append(raw_line.strip())
            if current:
                log.append(current)
    except (OSError, _studio_subprocess.SubprocessError):
        pass
    return {"ok": True, "files": files, "log": log[:limit], "vault": str(vault)}


def _studio_file(rel: str) -> tuple[str, dict]:
    abs_path = _studio_normalize_rel(rel)
    if not abs_path.exists() or not abs_path.is_file():
        raise FileNotFoundError(rel)
    text = abs_path.read_text(encoding="utf-8", errors="replace")
    meta = {
        "path": abs_path.relative_to(_studio_vault_dir()).as_posix(),
        "size": len(text.encode("utf-8")),
    }
    return text, meta


def _viewer_token_ok(handler) -> bool:
    """Auth for dashboard viewing. Accepts FIAM_VIEW_TOKEN via:
    0. Header  X-Forwarded-User   (trusted local Caddy basic-auth proxy)
    1. Cookie  fiam_view=<token>   (set by /login redirect)
    2. Query   ?token=<token>       (one-shot, used by /login)
    3. Header  X-Fiam-View-Token    (programmatic clients)
    Returns True if any source matches.
    """
    import os
    forwarded_user = handler.headers.get("X-Forwarded-User", "").lower()
    if forwarded_user in {"Zephyr", "ai", "live"}:
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
            if _constant_time_equal(got, expected):
                return True
    # header
    got = handler.headers.get("X-Fiam-View-Token", "")
    if _constant_time_equal(got, expected):
        return True
    # query (only used by /login below)
    raw = handler.path
    if "?" in raw:
        import urllib.parse as _u
        qs = dict(_u.parse_qsl(raw.split("?", 1)[1]))
        got = qs.get("token", "")
        if _constant_time_equal(got, expected):
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
            elif path == "/api/todo":
                self._serve_json(_api_todo())
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
                role = user if user in ("Zephyr", "ai", "live") else "anon"
                self._serve_json({"role": role})
            elif path == "/api/health":
                self._serve_json(_api_health())
            elif path == "/api/config":
                self._serve_json(_api_config())
            elif path == "/api/catalog/list":
                self._serve_json(_api_catalog_list())
            elif path == "/api/objects":
                if not _CONFIG:
                    self._serve_json({"error": "config not loaded"}, status=503)
                    return
                self._serve_json(_api_objects(query))
            elif path == "/api/timeline":
                if not _CONFIG:
                    self._serve_json({"error": "config not loaded"}, status=503)
                    return
                self._serve_json(_api_timeline(query))
            elif path == "/api/plugins":
                self._serve_json(_api_plugins())
            elif path == "/api/pool/edge-types":
                from fiam.store.pool import Pool
                self._serve_json({"types": list(Pool.EDGE_TYPE_NAMES.values())})
            elif path == "/api/annotate/proposal":
                self._serve_json(_annotate_proposal())
            elif path == "/api/debug/context":
                rt = (query.get("runtime", "latest") or "latest").lower()
                if rt not in {"latest", "api", "cc"}:
                    self._serve_json({"error": "runtime must be latest, api, or cc"}, status=400)
                    return
                if not _CONFIG:
                    self._serve_json({"error": "config not loaded"}, status=503)
                    return
                if rt == "latest":
                    candidates = [
                        _CONFIG.home_path / ".debug_last_assembly.json",
                        _CONFIG.home_path / ".debug_last_context.json",
                        _CONFIG.home_path / ".debug_last_context_api.json",
                        _CONFIG.home_path / ".debug_last_context_cc.json",
                    ]
                    existing = [p for p in candidates if p.exists()]
                    if not existing:
                        self._serve_json({"runtime": "latest", "empty": True})
                        return
                    ctx_path = max(existing, key=lambda p: p.stat().st_mtime)
                else:
                    ctx_path = _CONFIG.home_path / f".debug_last_context_{rt}.json"
                if not ctx_path.exists():
                    self._serve_json({"runtime": rt, "empty": True})
                    return
                try:
                    payload = json.loads(ctx_path.read_text(encoding="utf-8"))
                    if ctx_path.name == ".debug_last_assembly.json":
                        payload.setdefault("runtime", "in-flight")
                        payload.setdefault("source", "assembly")
                except Exception as e:
                    self._serve_json({"error": f"read failed: {e}"}, status=500)
                    return
                self._serve_json(payload)
            elif path == "/api/debug/trace":
                if not _CONFIG:
                    self._serve_json({"error": "config not loaded"}, status=503)
                    return
                self._serve_json(_api_debug_trace(query))
            elif path == "/api/debug/flow":
                try:
                    limit = max(1, min(2000, int(query.get("limit", "200") or "200")))
                except ValueError:
                    limit = 200
                if not _CONFIG:
                    self._serve_json({"error": "config not loaded"}, status=503)
                    return
                try:
                    from fiam.store.beat import read_beats
                    beats = read_beats(_CONFIG.flow_path)
                except Exception as e:
                    self._serve_json({"error": f"read failed: {e}"}, status=500)
                    return
                rows = [beat.to_dict() for beat in beats[-limit:]]
                self._serve_json({"rows": rows, "total": len(beats), "returned": len(rows)})
            else:
                self.send_error(404)
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)


    def do_GET(self):
        # Strip query string
        raw = self.path
        path = raw.split("?")[0]

        if path == "/browser/wakeup":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json({"items": _browser_wakeup_pop_all()})
            return

        if path == "/favilla/splash":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_favilla_splash())
            return

        if path == "/favilla/status":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_favilla_status())
            return

        if path == "/favilla/dashboard":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_favilla_dashboard())
            return

        if path.startswith("/favilla/object/"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            from urllib.parse import quote, unquote

            token = unquote(path[len("/favilla/object/"):])
            try:
                body, name, mime = _favilla_object_download(token)
            except FileNotFoundError:
                self._serve_json({"error": "object not found"}, status=404)
                return
            except Exception as e:
                logger.exception("Favilla object download error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/ring/today":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_favilla_ring_today())
            return

        if path == "/favilla/studio":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                self._serve_json(_favilla_studio_load())
            except Exception as e:
                logger.exception("Favilla Studio load error")
                self._serve_json({"error": str(e)}, status=500)
            return

        if path in ("/studio/list", "/studio/file"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(raw).query)
            if path == "/studio/list":
                try:
                    result = _studio_list(
                        dir_filter=(qs.get("dir", [""])[0] or ""),
                        limit=int(qs.get("limit", ["50"])[0] or 50),
                    )
                except ValueError as e:
                    self._serve_json({"error": str(e)}, status=400)
                    return
                except Exception as e:
                    logger.exception("Studio list error")
                    self._serve_json({"error": str(e)}, status=500)
                    return
                self._serve_json(result)
                return
            # /studio/file
            rel = qs.get("path", [""])[0] or ""
            try:
                text, meta = _studio_file(rel)
            except FileNotFoundError:
                self._serve_json({"error": "not found"}, status=404)
                return
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Studio file error")
                self._serve_json({"error": str(e)}, status=500)
                return
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Studio-Path", meta["path"])
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/favilla/chat/transcript", "/favilla/stroll/transcript"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(raw).query)
            channel = "stroll" if path == "/favilla/stroll/transcript" else (query.get("channel") or ["chat"])[0]
            try:
                limit = int((query.get("limit") or ["200"])[0])
            except ValueError:
                limit = 200
            self._serve_json(_favilla_transcript_load(channel=channel, limit=limit))
            return

        if path == "/favilla/stroll/nearby":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(raw).query)
            try:
                payload = {
                    "lng": float((query.get("lng") or [""])[0]),
                    "lat": float((query.get("lat") or [""])[0]),
                    "radiusM": float((query.get("radiusM") or ["50"])[0]),
                    "changedSince": int((query.get("changedSince") or ["0"])[0]),
                }
                self._serve_json(_favilla_stroll_nearby(payload))
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
            except Exception as e:
                logger.exception("Favilla Stroll nearby error")
                self._serve_json({"error": str(e)}, status=500)
            return

        if path == "/favilla/chat/process/status":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            self._serve_json(_favilla_chat_process_status())
            return

        if path == "/favilla/stroll/state":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                self._serve_json(_favilla_stroll_state_status())
            except Exception as e:
                logger.exception("Favilla Stroll state error")
                self._serve_json({"error": str(e)}, status=500)
            return

        if path == "/favilla/stroll/events":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            from urllib.parse import parse_qs, urlparse
            from fiam_lib.stroll_events import get_bus

            query = parse_qs(urlparse(raw).query)
            last_id_header = self.headers.get("Last-Event-ID")
            try:
                last_id = int(last_id_header) if last_id_header else int((query.get("last_id") or ["0"])[0])
            except ValueError:
                last_id = 0
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                bus = get_bus()
                # Initial replay of any missed events.
                for ev in bus.replay(last_id):
                    self.wfile.write(_format_sse(ev))
                    self.wfile.flush()
                    last_id = ev["id"]
                # Long-poll loop: 25s windows then heartbeat comment.
                while True:
                    got_any = False
                    for ev in bus.subscribe(after_id=last_id, timeout=25.0):
                        self.wfile.write(_format_sse(ev))
                        self.wfile.flush()
                        last_id = ev["id"]
                        got_any = True
                    if not got_any:
                        # Heartbeat keeps proxies / mobile radios from killing the conn.
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                logger.exception("Favilla Stroll SSE error")
                return
            return

        if path == "/favilla/computer/events":
            # Live push of AI computer-control activity (browser + desktop).
            # EventSource can't set custom headers, so accept token via
            # query string (?token=...) in addition to X-Fiam-Token.
            import os as _os
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(raw).query)
            expected = _os.environ.get("FIAM_INGEST_TOKEN", "")
            qtoken = (query.get("token") or [""])[0]
            authed = False
            if expected:
                if _constant_time_equal(qtoken, expected):
                    authed = True
                else:
                    htoken = self.headers.get("X-Fiam-Token", "")
                    if _constant_time_equal(htoken, expected):
                        authed = True
            if not authed:
                self._serve_json({"error": "unauthorized"}, status=401)
                return

            from fiam_lib.computer_events import get_bus as _get_computer_bus

            last_id_header = self.headers.get("Last-Event-ID")
            try:
                last_id = int(last_id_header) if last_id_header else int((query.get("last_id") or ["0"])[0])
            except ValueError:
                last_id = 0
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                bus = _get_computer_bus()
                for ev in bus.replay(last_id):
                    self.wfile.write(_format_sse(ev))
                    self.wfile.flush()
                    last_id = ev["id"]
                while True:
                    got_any = False
                    for ev in bus.subscribe(after_id=last_id, timeout=25.0):
                        self.wfile.write(_format_sse(ev))
                        self.wfile.flush()
                        last_id = ev["id"]
                        got_any = True
                    if not got_any:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception:
                logger.exception("Favilla Computer SSE error")
                return
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
        config_paths = {"/api/config/memory-mode", "/api/config/plugin", "/api/config/catalog", "/api/catalog/refresh"}
        is_pool_write = path in pool_write_paths or any(path.startswith(p) for p in pool_write_prefixes)
        is_annotate = path in annotate_paths
        is_config_write = path in config_paths

        if path in {"/browser/snapshot", "/browser/ask", "/browser/tick", "/browser/action-result", "/browser/wakeup"}:
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            max_browser_post = 8 * 1024 * 1024
            if length <= 0 or length > max_browser_post:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                if path == "/browser/snapshot":
                    result = _browser_snapshot(payload)
                elif path == "/browser/action-result":
                    result = _append_browser_action_flow(payload)
                elif path == "/browser/tick":
                    result = _browser_control_tick(payload)
                elif path == "/browser/wakeup":
                    result = _browser_wakeup_push(payload)
                else:
                    result = _browser_ask(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Browser bridge error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path == "/ring/sync":
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
                result = _favilla_ring_sync(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("ring sync error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

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

        if path in ("/favilla/chat/send", "/favilla/stroll/send"):
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
                if path == "/favilla/stroll/send":
                    payload["channel"] = "stroll"
                # Stream branch: client opted into SSE via Accept header or ?stream=1.
                from urllib.parse import parse_qs, urlparse
                accept_hdr = (self.headers.get("Accept") or "").lower()
                want_stream = "text/event-stream" in accept_hdr or (parse_qs(urlparse(self.path).query).get("stream") or [""])[0] in ("1", "true")
                if want_stream:
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache, no-transform")
                        self.send_header("Content-Encoding", "identity")
                        # Force close so the client's stream reader sees EOF when the
                        # generator finishes (no Content-Length, no chunked encoding).
                        self.send_header("Connection", "close")
                        self.close_connection = True
                        self.send_header("X-Accel-Buffering", "no")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        # Flush a leading comment so proxies see the response start immediately.
                        try:
                            self.wfile.write(b": stream-start\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        sse_id = 0
                        client_gone = False
                        for ev in _favilla_chat_send_stream(payload):
                            sse_id += 1
                            if client_gone:
                                continue
                            frame = _format_sse({"id": sse_id, "event": ev.get("event") or "message", "data": ev.get("data") or {}})
                            try:
                                self.wfile.write(frame)
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                # Keep consuming the generator so the runtime
                                # can finish and persist the transcript. SSE is
                                # a delivery channel, not the source of truth.
                                client_gone = True
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except Exception:
                        logger.exception("Favilla chat SSE error")
                    return
                result = _favilla_chat_send(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla chat error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path == "/favilla/upload":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 32 * 1024 * 1024:  # 32MB cap
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _favilla_upload(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla upload error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path == "/favilla/studio/edit":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 2 * 1024 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _favilla_studio_edit(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla Studio edit error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path == "/favilla/studio":
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 2 * 1024 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _favilla_studio_save(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla Studio save error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path in ("/studio/share", "/studio/quicknote"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 4 * 1024 * 1024:
                self._serve_json({"error": "bad length"}, status=400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                if path == "/studio/share":
                    result = _studio_share(payload)
                else:
                    result = _studio_quicknote(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Studio %s error", path)
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path in ("/favilla/chat/transcript", "/favilla/stroll/transcript"):
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
                if path == "/favilla/stroll/transcript":
                    payload["channel"] = "stroll"
                result = _favilla_transcript_append(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla transcript error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path in ("/favilla/stroll/records", "/favilla/stroll/action-result"):
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
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._serve_json({"error": f"bad json: {e}"}, status=400)
                return
            try:
                result = _favilla_stroll_record(payload) if path == "/favilla/stroll/records" else _favilla_stroll_action_result(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla Stroll write error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path in ("/favilla/stroll/start", "/favilla/stroll/heartbeat", "/favilla/stroll/stop"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            payload: dict = {}
            if length > 0:
                if length > 64 * 1024:
                    self._serve_json({"error": "bad length"}, status=400)
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) or {}
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._serve_json({"error": f"bad json: {e}"}, status=400)
                    return
            try:
                if path == "/favilla/stroll/start":
                    result = _favilla_stroll_state_start(payload)
                elif path == "/favilla/stroll/heartbeat":
                    result = _favilla_stroll_state_heartbeat(payload)
                else:
                    result = _favilla_stroll_state_stop(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla Stroll state op error")
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(result)
            return

        if path in ("/favilla/chat/recall", "/favilla/chat/cut", "/favilla/chat/process"):
            if not _ingest_token_ok(self):
                self._serve_json({"error": "unauthorized"}, status=401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            payload: dict = {}
            if length > 0:
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) or {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
            try:
                if path == "/favilla/chat/recall":
                    result = _favilla_chat_recall(payload)
                elif path == "/favilla/chat/cut":
                    result = _favilla_chat_cut(payload)
                else:
                    result = _favilla_chat_process(payload)
            except ValueError as e:
                self._serve_json({"error": str(e)}, status=400)
                return
            except Exception as e:
                logger.exception("Favilla chat memory op error")
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
                elif path == "/api/config/catalog":
                    result = _update_catalog(payload)
                elif path == "/api/catalog/refresh":
                    result = _api_catalog_refresh(payload)
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
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def log_message(self, format, *args):
        pass  # suppress access logs


def main():
    parser = argparse.ArgumentParser(description="fiam debug dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address (default 127.0.0.1; use 0.0.0.0 only behind a trusted proxy)")
    args = parser.parse_args()

    _LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(_LOGS / "dashboard_server.log", encoding="utf-8"),
        ],
        force=True,
    )

    _load_config()
    _get_bus()
    import os
    if not os.environ.get("FIAM_VIEW_TOKEN"):
        print("WARN: FIAM_VIEW_TOKEN not set — direct GET requests will return 401 unless proxied by Caddy auth.",
              file=sys.stderr)
    server = ThreadingHTTPServer((args.bind, args.port), DashboardHandler)
    print(f"Dashboard: http://{args.bind}:{args.port}/")
    _start_stroll_tick_thread()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
