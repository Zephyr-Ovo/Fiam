"""
ActivityWatch bridge — poll AW's REST API, write changes to activity.jsonl.

Runs on Local (Windows). Queries aw-watcher-window for current activity.
Only appends when the active window changes (dedup by title+app).

Output: home/world/activity.jsonl (synced to ISP via tunnel/git)

Usage:
    python scripts/aw_bridge.py                  # run once
    python scripts/aw_bridge.py --watch           # continuous polling
    python scripts/aw_bridge.py --summarize       # summarize recent activity
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# AW REST API defaults
AW_BASE = "http://localhost:5600/api"
POLL_INTERVAL = 60  # seconds

# Resolve paths
SCRIPT_DIR = Path(__file__).resolve().parent
CODE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CODE_ROOT / "src"))


def _get_hostname() -> str:
    """Auto-detect AW bucket hostname."""
    try:
        url = f"{AW_BASE}/0/buckets/"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            buckets = json.loads(resp.read())
        for name in buckets:
            if name.startswith("aw-watcher-window_"):
                return name.replace("aw-watcher-window_", "")
    except Exception:
        pass
    import socket
    return socket.gethostname()


def _fetch_current(hostname: str) -> dict | None:
    """Fetch the latest window event from AW."""
    bucket = f"aw-watcher-window_{hostname}"
    url = f"{AW_BASE}/0/buckets/{bucket}/events?limit=1"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            events = json.loads(resp.read())
        if events:
            e = events[0]
            return {
                "app": e.get("data", {}).get("app", ""),
                "title": e.get("data", {}).get("title", ""),
                "timestamp": e.get("timestamp", ""),
                "duration": e.get("duration", 0),
            }
    except urllib.error.URLError:
        return None
    return None


def _load_last(jsonl_path: Path) -> dict | None:
    """Read the last line of the JSONL file."""
    if not jsonl_path.exists():
        return None
    try:
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            return json.loads(lines[-1])
    except Exception:
        pass
    return None


def _append(jsonl_path: Path, entry: dict) -> None:
    """Append one JSON line."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _same_activity(a: dict | None, b: dict | None) -> bool:
    """True if both represent the same window (app + title)."""
    if a is None or b is None:
        return False
    return a.get("app") == b.get("app") and a.get("title") == b.get("title")


def _resolve_output(home_path: str | None) -> Path:
    """Resolve the output JSONL path."""
    if home_path:
        return Path(home_path) / "world" / "activity.jsonl"
    # Try loading from fiam.toml
    try:
        from fiam.config import FiamConfig
        cfg = FiamConfig.from_toml(CODE_ROOT / "fiam.toml", CODE_ROOT)
        return cfg.world_dir / "activity.jsonl"
    except Exception:
        return CODE_ROOT / "store" / "activity.jsonl"


def poll_once(output: Path, hostname: str) -> bool:
    """Fetch current activity, append if changed. Returns True if written."""
    current = _fetch_current(hostname)
    if current is None:
        return False

    last = _load_last(output)
    if _same_activity(last, current):
        return False

    entry = {
        "app": current["app"],
        "title": current["title"],
        "time": datetime.now(timezone.utc).isoformat(),
        "aw_ts": current["timestamp"],
    }
    _append(output, entry)
    print(f"[aw] {entry['time'][:19]}  {current['app']}  |  {current['title'][:60]}")
    return True


def summarize(output: Path, hours: int = 24) -> str:
    """Summarize recent activity from the JSONL file."""
    if not output.exists():
        return "No activity data yet."

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    app_minutes: dict[str, float] = {}
    lines = output.read_text(encoding="utf-8").strip().splitlines()
    prev_time = None

    for line in lines:
        try:
            entry = json.loads(line)
            t = datetime.fromisoformat(entry["time"])
            if t < cutoff:
                continue
            app = entry.get("app", "unknown")
            if prev_time and (t - prev_time).total_seconds() < 300:
                duration = (t - prev_time).total_seconds() / 60
                app_minutes[app] = app_minutes.get(app, 0) + duration
            prev_time = t
        except Exception:
            continue

    if not app_minutes:
        return f"No activity in the last {hours}h."

    total = sum(app_minutes.values())
    lines_out = [f"Activity summary (last {hours}h, {total:.0f} min total):"]
    for app, mins in sorted(app_minutes.items(), key=lambda t: t[1], reverse=True):
        pct = mins / total * 100
        lines_out.append(f"  {app}: {mins:.0f}min ({pct:.0f}%)")
    return "\n".join(lines_out)


def main():
    parser = argparse.ArgumentParser(description="ActivityWatch → fiam bridge")
    parser.add_argument("--watch", action="store_true", help="Continuous polling mode")
    parser.add_argument("--summarize", action="store_true", help="Summarize recent activity")
    parser.add_argument("--hours", type=int, default=24, help="Summary window (hours)")
    parser.add_argument("--home", type=str, default=None, help="Override home_path")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="Poll interval (seconds)")
    args = parser.parse_args()

    output = _resolve_output(args.home)
    hostname = _get_hostname()
    print(f"[aw] hostname={hostname}  output={output}  interval={args.interval}s")

    if args.summarize:
        print(summarize(output, args.hours))
        return

    if args.watch:
        print(f"[aw] Watching ActivityWatch (poll every {args.interval}s)...")
        while True:
            try:
                poll_once(output, hostname)
            except KeyboardInterrupt:
                print("[aw] Stopped.")
                break
            except Exception as e:
                print(f"[aw] Error: {e}")
            time.sleep(args.interval)
    else:
        changed = poll_once(output, hostname)
        if not changed:
            print("[aw] No change (same window active)")


if __name__ == "__main__":
    main()
