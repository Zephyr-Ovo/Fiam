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


def _recent_events(n: int = 10) -> list[dict]:
    """Return n most recent events (id, time, preview)."""
    if not _CONFIG:
        return []
    events_dir = _CONFIG.events_dir
    if not events_dir.is_dir():
        return []
    all_events: list[dict] = []
    for md in events_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        etime = ""
        preview = ""
        in_frontmatter = False
        body_lines: list[str] = []
        for line in text.split("\n"):
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter and line.startswith("time:"):
                etime = line.split(":", 1)[1].strip()
            elif not in_frontmatter:
                body_lines.append(line)
        preview = " ".join(body_lines[:3]).strip()[:120]
        all_events.append({
            "id": md.stem,
            "time": etime,
            "preview": preview,
        })
    # Sort by time descending (ISO-ish format sorts lexically)
    all_events.sort(key=lambda e: e["time"], reverse=True)
    return all_events[:n]


def _recall_content() -> str:
    """Read current recall.md."""
    if not _CONFIG:
        return ""
    path = _CONFIG.background_path
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _schedule_data() -> list[dict]:
    """Read schedule.json."""
    if not _CONFIG:
        return []
    path = Path(_CONFIG.home_path) / "self" / "schedule.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _cost_today() -> str:
    """Read today's cost from ledger."""
    if not _CONFIG:
        return "no config"
    ledger_path = Path(_CONFIG.home_path) / "self" / "cost_ledger.jsonl"
    if not ledger_path.exists():
        return "no ledger"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    entries = 0
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("date", "").startswith(today):
                total += obj.get("cost_usd", 0)
                entries += 1
    except Exception:
        pass
    return f"Today ({today}): ${total:.4f} across {entries} entries\nBudget: ${_CONFIG.daily_budget_usd:.2f}/day"


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
                    pid = None
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
        in_fm = False
        body: list[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if s == "---":
                in_fm = not in_fm
                continue
            if in_fm:
                if s.startswith("time:"):
                    etime = s.split(":", 1)[1].strip()
                elif s.startswith("intensity:"):
                    try:
                        intensity = float(s.split(":", 1)[1].strip())
                    except ValueError:
                        pass
            else:
                body.append(line)
        preview = " ".join(l.strip() for l in body if l.strip())[:140]
        out.append({
            "id": md.stem,
            "time": etime,
            "intensity": intensity,
            "preview": preview,
        })
    out.sort(key=lambda e: e["time"], reverse=True)
    return out[:limit]


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
    for ev in _api_events(10000):
        intensity_map[ev["id"]] = ev["intensity"]
        time_map[ev["id"]] = ev["time"]
        nodes[ev["id"]] = {
            "id": ev["id"],
            "label": ev["id"][-6:],
            "intensity": ev["intensity"],
            "time": ev["time"],
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
                        }
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return {"nodes": list(nodes.values()), "edges": edges}


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
            else:
                self.send_error(404)
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)


    def do_GET(self):
        # Strip query string
        raw = self.path
        path = raw.split("?")[0]

        # --- New /api/* endpoints for SvelteKit frontend ---
        if path.startswith("/api/"):
            return self._handle_api(path, raw)

        if path == "/" or path == "/dashboard.html":
            self._serve_file(_LOGS / "dashboard.html", "text/html")
        elif path == "/daemon_state.json":
            self._serve_file(_LOGS / "daemon_state.json", "application/json")
        elif path == "/pipeline_tail.txt":
            self._serve_text(_pipeline_tail(), "text/plain")
        elif path == "/recent_events.json":
            self._serve_json({"events": _recent_events()})
        elif path == "/recall.md":
            self._serve_text(_recall_content(), "text/plain")
        elif path == "/schedule.json":
            self._serve_json(_schedule_data())
        elif path == "/cost_today.txt":
            self._serve_text(_cost_today(), "text/plain")
        elif path == "/graph_3d.html":
            self._serve_file(_LOGS / "graph_3d.html", "text/html")
        elif path == "/graph_data.json":
            self._serve_file(_LOGS / "graph_data.json", "application/json")
        else:
            # Fall through to SvelteKit static build (SPA with fallback)
            return self._serve_spa(path)

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
    args = parser.parse_args()

    _load_config()
    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"Dashboard: http://localhost:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
