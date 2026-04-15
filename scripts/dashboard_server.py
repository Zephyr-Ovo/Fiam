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


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serve dashboard HTML and dynamic data endpoints."""

    def do_GET(self):
        # Strip query string
        path = self.path.split("?")[0]

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
            self.send_error(404)

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

    def _serve_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
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
