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
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from io import BytesIO

# Resolve paths relative to fiam-code root
_ROOT = Path(__file__).resolve().parent.parent
_LOGS = _ROOT / "logs"
_STORE = None  # set after config load
_CONFIG = None

# Fix sys.path: add src/, remove scripts/ (fiam.py shadows fiam package)
_src_dir = str(_ROOT / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_scripts_dir = str(_ROOT / "scripts")
if _scripts_dir in sys.path:
    sys.path.remove(_scripts_dir)


def _load_config():
    global _CONFIG, _STORE
    from fiam.config import FiamConfig
    toml_path = _ROOT / "fiam.toml"
    if toml_path.exists():
        _CONFIG = FiamConfig.from_toml(toml_path, _ROOT)
        _STORE = Path(_CONFIG.home_path) / "store"


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
                self._serve_json(_api_graph())
            elif path == "/api/pipeline":
                self._serve_json({"lines": _pipeline_tail(200).splitlines()})
            elif path == "/api/whoami":
                # Determine role from Caddy-forwarded header (basic-auth user)
                user = self.headers.get("X-Forwarded-User", "anon").lower()
                role = user if user in ("iris", "ai", "fiet") else "anon"
                self._serve_json({"role": role})
            elif path == "/api/health":
                self._serve_json(_api_health())
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
        if path != "/api/capture":
            self.send_error(404)
            return
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
