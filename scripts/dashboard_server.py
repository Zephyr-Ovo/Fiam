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
_STORE = None  # set after config load
_CONFIG = None
_POOL = None        # Pool instance (lazy)
_EMBEDDER = None    # Embedder instance (lazy)
_COMPUTE_LOCK = threading.Lock()  # gate concurrent mutations

# Fix sys.path: add src/, remove scripts/ (fiam.py shadows fiam package)
_src_dir = str(_ROOT / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_scripts_dir = str(_ROOT / "scripts")
if _scripts_dir in sys.path:
    sys.path.remove(_scripts_dir)


def _load_config():
    global _CONFIG, _STORE, _POOL
    from fiam.config import FiamConfig
    toml_path = _ROOT / "fiam.toml"
    if toml_path.exists():
        _CONFIG = FiamConfig.from_toml(toml_path, _ROOT)
        _STORE = Path(_CONFIG.home_path) / "store"
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
        ev_dir = _CONFIG.events_dir
        if ev_dir.is_dir():
            events = len(list(ev_dir.glob("*.md")))
        emb_dir = _CONFIG.embeddings_dir
        if emb_dir.is_dir():
            embeddings = len(list(emb_dir.glob("*.npy")))
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
    """Events with intensity parsed from frontmatter."""
    if not _CONFIG:
        return []
    events_dir = _CONFIG.events_dir
    if not events_dir.is_dir():
        return []
    out: list[dict] = []
    for md in events_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        etime = ""
        intensity = 0.0
        preview = ""
        last_accessed = ""
        access_count = 0
        in_fm = False
        body: list[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if s == "---":
                in_fm = not in_fm
                continue
            if in_fm:
                if s.startswith("time:"):
                    etime = s.split(":", 1)[1].strip().strip("'\"")
                elif s.startswith("intensity:"):
                    try:
                        intensity = float(s.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif s.startswith("last_accessed:"):
                    last_accessed = s.split(":", 1)[1].strip().strip("'\"")
                elif s.startswith("access_count:"):
                    try:
                        access_count = int(s.split(":", 1)[1].strip())
                    except ValueError:
                        pass
            else:
                body.append(line)
        preview = " ".join(l.strip() for l in body if l.strip())[:140]
        out.append({
            "id": md.stem,
            "time": etime,
            "intensity": intensity,
            "last_accessed": last_accessed,
            "access_count": access_count,
            "preview": preview,
        })
    out.sort(key=lambda e: e["time"], reverse=True)
    return out[:limit]


def _api_event(event_id: str) -> dict | None:
    """Full content of one event by id (markdown stem)."""
    if not _CONFIG:
        return None
    md = _CONFIG.events_dir / f"{event_id}.md"
    if not md.is_file():
        return None
    text = md.read_text(encoding="utf-8", errors="replace")
    frontmatter: dict[str, str] = {}
    body_lines: list[str] = []
    in_fm = False
    fm_seen = 0
    for line in text.split("\n"):
        if line.strip() == "---":
            fm_seen += 1
            in_fm = fm_seen == 1
            continue
        if in_fm:
            if ":" in line:
                k, v = line.split(":", 1)
                frontmatter[k.strip()] = v.strip().strip("'\"")
        else:
            body_lines.append(line)
    body = "\n".join(body_lines).strip()
    return {"id": event_id, "frontmatter": frontmatter, "body": body}


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


def _api_graph() -> dict:
    """Return nodes/edges from graph.jsonl for visualization."""
    if not _CONFIG:
        return {"nodes": [], "edges": []}
    graph_path = _CONFIG.graph_jsonl_path
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    # Build intensity map from events
    intensity_map: dict[str, float] = {}
    time_map: dict[str, str] = {}
    last_acc_map: dict[str, str] = {}
    acc_cnt_map: dict[str, int] = {}
    for ev in _api_events(10000):
        intensity_map[ev["id"]] = ev["intensity"]
        time_map[ev["id"]] = ev["time"]
        last_acc_map[ev["id"]] = ev.get("last_accessed", "")
        acc_cnt_map[ev["id"]] = ev.get("access_count", 0)
        nodes[ev["id"]] = {
            "id": ev["id"],
            "label": ev["id"][-6:],
            "intensity": ev["intensity"],
            "time": ev["time"],
            "last_accessed": ev.get("last_accessed", ""),
            "access_count": ev.get("access_count", 0),
        }
    if graph_path.exists():
        for line in graph_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                src = e.get("source") or e.get("src")
                tgt = e.get("target") or e.get("dst")
                if not src or not tgt:
                    continue
                edges.append({
                    "source": src,
                    "target": tgt,
                    "kind": e.get("kind", e.get("type", "associative")),
                    "weight": float(e.get("weight", e.get("score", 0.5))),
                })
                # ensure endpoints exist as nodes
                for eid in (src, tgt):
                    if eid not in nodes:
                        nodes[eid] = {
                            "id": eid,
                            "label": eid[-6:],
                            "intensity": intensity_map.get(eid, 0.3),
                            "time": time_map.get(eid, ""),
                            "last_accessed": last_acc_map.get(eid, ""),
                            "access_count": acc_cnt_map.get(eid, 0),
                        }
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return {"nodes": list(nodes.values()), "edges": edges}


def _api_capture(payload: dict) -> dict:
    """Ingest a mobile/quick-capture event.

    Expected payload keys: text (required), source (optional), url (optional),
    tags (optional list). Writes a markdown file to events_dir with the same
    frontmatter shape used elsewhere.
    """
    if not _CONFIG:
        raise RuntimeError("config not loaded")
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("missing text")
    source = (payload.get("source") or "mobile").strip()
    url = (payload.get("url") or "").strip()
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    if source and source not in tags:
        tags.insert(0, source)

    now = datetime.now(timezone.utc)
    import secrets
    ev_id = now.strftime("%m%d_%H%M") + "_" + secrets.token_hex(2)
    ev_path = _CONFIG.events_dir / f"{ev_id}.md"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    tags_yaml = "[" + ", ".join(json.dumps(t) for t in tags) + "]" if tags else "[]"
    fm = (
        "---\n"
        f"time: '{now.isoformat()}'\n"
        "intensity: 0.4\n"
        "access_count: 0\n"
        f"tags: {tags_yaml}\n"
        f"source: {json.dumps(source)}\n"
        + (f"url: {json.dumps(url)}\n" if url else "")
        + "---\n\n"
    )
    body = f"[capture]\n{text}\n"
    ev_path.write_text(fm + body, encoding="utf-8")
    return {"ok": True, "id": ev_id, "path": str(ev_path)}


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
        body = _POOL.read_body(ev.id)
        # First non-empty line as label (skip [user]/[assistant] markers)
        label = ev.id.replace("_", " ")
        for line in body.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped and stripped not in ("[user]", "[assistant]"):
                label = stripped[:60]
                break
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
                # Pool-based graph (fallback to legacy if pool empty)
                pool_data = _pool_graph()
                if pool_data["nodes"]:
                    self._serve_json(pool_data)
                else:
                    self._serve_json(_api_graph())
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
            elif path == "/api/pool/edge-types":
                from fiam.store.pool import Pool
                self._serve_json({"types": list(Pool.EDGE_TYPE_NAMES.values())})
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
        is_pool_write = path in pool_write_paths or any(path.startswith(p) for p in pool_write_prefixes)

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
                if path.startswith("/api/pool/event/"):
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
