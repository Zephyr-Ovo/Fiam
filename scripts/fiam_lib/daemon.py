"""Daemon lifecycle — start, stop, status."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _build_config, _pid_path, _is_daemon_running
from fiam_lib.jsonl import (
    _claude_projects_dir,
    _sanitize_home_path,
    _load_cursor,
    _save_cursor,
    _parse_jsonl_from,
)
from fiam_lib.postman import sweep_outbox, fetch_inbox, fetch_tg_inbox
from fiam_lib.recall import _write_recall
from fiam_lib.ui import _console, _flow, _ANIM_IDLE, _ANIM_ACTIVE, _animated_sleep


def cmd_start(args: argparse.Namespace) -> None:
    """Daemon: poll JSONL for activity, process on idle timeout."""
    config = _build_config(args)
    code_path = _project_root()

    # PID check
    existing_pid = _is_daemon_running(code_path)
    if existing_pid:
        print(f"fiam is already running (PID {existing_pid}).", file=sys.stderr)
        print("  Use 'fiam stop' first.", file=sys.stderr)
        sys.exit(1)

    # Write PID
    pid_file = _pid_path(code_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # Graceful shutdown
    running = True
    shutdown_requested = False

    def _shutdown(sig, frame):
        nonlocal running, shutdown_requested
        if shutdown_requested:
            # Second Ctrl+C = force exit
            sys.exit(1)
        shutdown_requested = True
        running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _shutdown)

    # Initial pre_session
    from fiam.pipeline import pre_session, post_session
    from fiam.retriever.embedder import Embedder
    from fiam.store.home import HomeStore
    import numpy as np

    result = pre_session(config)
    event_count = result["event_count"]

    _console.print()
    _console.print(_flow("  fiam  ✦"))
    _console.print("  [dim #b57bee]────────────────────────────────────────[/]")
    _console.print(f"  [#7eb8f7]home[/]    {config.home_path}")
    _console.print(f"  [#f7a8d0]memory[/]  {event_count} events")
    _console.print()

    # Find the JSONL directory for this home
    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(config.home_path)
    jsonl_dir = projects_dir / sanitized

    last_activity: float = 0.0
    active = False
    idle_timeout = config.idle_timeout_minutes * 60
    poll_interval = config.poll_interval_seconds

    # Live recall state
    recall_query_vec: np.ndarray | None = None  # embedding of last recall query
    recall_drift_threshold = 0.65               # cosine sim below this = topic shift
    recall_min_chars = 40                       # skip trivial messages
    embedder_lazy: Embedder | None = None

    def _get_embedder() -> Embedder:
        nonlocal embedder_lazy
        if embedder_lazy is None:
            embedder_lazy = Embedder(config)
        return embedder_lazy

    def _peek_recent_user_text(jsonl_files: list[Path], max_chars: int = 600) -> str:
        """Read the last ~max_chars of user text from JSONL files (read-only peek)."""
        parts: list[str] = []
        total = 0
        for jf in sorted(jsonl_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = jf.read_bytes()
            except OSError:
                continue
            # Walk lines backward
            for raw_line in reversed(raw.split(b"\n")):
                line_text = raw_line.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue
                try:
                    obj = json.loads(line_text)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "user":
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        parts.append(content.strip())
                        total += len(content)
                        if total >= max_chars:
                            break
            if total >= max_chars:
                break
        # Return in chronological order
        parts.reverse()
        return "\n".join(parts)

    def _update_recall_if_drifted(jsonl_files: list[Path]) -> None:
        """Check if conversation topic drifted from current recall; update if so."""
        nonlocal recall_query_vec

        user_text = _peek_recent_user_text(jsonl_files)
        if len(user_text) < recall_min_chars:
            return

        emb = _get_embedder()
        current_vec = emb.embed(user_text)

        # Check drift against last recall query
        if recall_query_vec is not None:
            sim = float(np.dot(current_vec, recall_query_vec) / (
                np.linalg.norm(current_vec) * np.linalg.norm(recall_query_vec)
            ))
            if sim > recall_drift_threshold:
                return  # topic hasn't shifted enough

        # Topic shifted — run retrieval and update recall.md
        recall_query_vec = current_vec

        store = HomeStore(config)
        from fiam.retriever import joint as joint_retriever
        events = joint_retriever.search(user_text, store, config)

        if not events:
            return

        _write_recall(config, events)
        ts = time.strftime("%H:%M")
        _console.print(f"  [dim]└[{ts}][/dim] [bold #7eb8f7]↻[/]  recall  [bold #f7e08a]{len(events)}[/]")

    def _process_pending() -> None:
        """Process all unread JSONL content and refresh recall."""
        nonlocal active, recall_query_vec

        if not jsonl_dir.is_dir():
            return
        jf_list = list(jsonl_dir.glob("*.jsonl"))
        if not jf_list:
            return

        cursor = _load_cursor(code_path)
        total_turns: list[dict[str, str]] = []

        for jf in sorted(jf_list, key=lambda p: p.stat().st_mtime):
            jkey = jf.name  # platform-independent: just filename
            entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})

            try:
                jf_mtime = jf.stat().st_mtime
            except FileNotFoundError:
                continue
            if jf_mtime < entry["mtime"]:
                entry["byte_offset"] = 0

            turns, new_offset = _parse_jsonl_from(jf, entry["byte_offset"])
            if turns:
                total_turns.extend(turns)
            cursor[jkey] = {"byte_offset": new_offset, "mtime": jf_mtime}

        if total_turns:
            try:
                r = post_session(config, total_turns)
                _console.print(f"  [bold #a8f0e8]+{r['events_written']}[/] memories")
            except Exception as e:
                _console.print(f"  [red]error:[/] {e}")

            try:
                store = HomeStore(config)
                from fiam.retriever import joint as joint_retriever
                events = joint_retriever.search("", store, config)
                _write_recall(config, events)
                recall_query_vec = None
                _console.print(f"  recall [#f7a8d0]←[/] [bold #f7e08a]{len(events)}[/] fragments")
            except Exception as e:
                _console.print(f"  [red]recall error:[/] {e}")
        else:
            _console.print(f"  [dim]·  up to date[/dim]")

        _save_cursor(code_path, cursor)
        active = False

    last_inbox_check: float = 0.0
    inbox_interval = 60  # check TG + email every 60s

    while running:
        _animated_sleep(
            poll_interval,
            _ANIM_ACTIVE if active else _ANIM_IDLE,
            stop_check=lambda: not running,
        )

        if not running:
            break

        # ── Inbound channels: TG + email polling ──
        now_ts = time.time()
        if now_ts - last_inbox_check > inbox_interval:
            last_inbox_check = now_ts
            try:
                n_tg = fetch_tg_inbox(config)
                n_email = fetch_inbox(config)
                if n_tg or n_email:
                    ts = time.strftime("%H:%M")
                    _console.print(f"  [dim]└[{ts}][/dim] [bold #7eb8f7]✉[/]  inbox +{n_tg + n_email}")
            except Exception as e:
                if config.debug_mode:
                    print(f"  [inbox] Error: {e}", file=sys.stderr)

        # ── Outbox dispatch ──
        try:
            sweep_outbox(config)
        except Exception:
            pass

        # Check JSONL directory for activity
        if not jsonl_dir.is_dir():
            continue

        jsonl_files = list(jsonl_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        # Detect activity across ALL jsonl files
        max_mtime = max(f.stat().st_mtime for f in jsonl_files)

        if max_mtime > last_activity:
            if not active:
                ts = time.strftime("%H:%M")
                _console.print(f"  [dim]└[{ts}][/dim] [bold #f7e08a]✦[/]  active")
                active = True
            last_activity = max_mtime

            # Live recall: check topic drift on each new activity burst
            try:
                _update_recall_if_drifted(jsonl_files)
            except Exception as e:
                if config.debug_mode:
                    print(f"  [recall] Error: {e}", file=sys.stderr)

            continue

        # Check idle timeout
        if active and (time.time() - last_activity) > idle_timeout:
            ts = time.strftime("%H:%M")
            _console.print(f"  [dim]└[{ts}][/dim] [bold #f7a8d0]⟳[/]  processing...")
            _process_pending()

    # ── Graceful shutdown: process any pending content before exit ──
    if shutdown_requested and active:
        _console.print()
        _console.print(f"  [bold #f7a8d0]⟳[/]  wrapping up...")
        _process_pending()

    # Cleanup
    pid_file.unlink(missing_ok=True)
    _console.print()
    _console.print(_flow("  ( ˘ω˘ )  see you"))
    _console.print()


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running daemon gracefully (processes pending content first)."""
    code_path = _project_root()
    pid = _is_daemon_running(code_path)
    if pid is None:
        print("fiam is not running.")
        return

    print(f"Stopping fiam (PID {pid})...")
    print("  (processing pending content before exit)")

    if sys.platform == "win32":
        # Send Ctrl+C event to trigger graceful shutdown handler
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Attach to the daemon's console and send CTRL_BREAK_EVENT
        # Fall back to SIGTERM-style if that fails
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except (OSError, AttributeError):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGTERM)

    # Wait for graceful exit (up to 120s for model inference)
    for _ in range(120):
        if not _is_daemon_running(code_path):
            print("  fiam stopped.")
            return
        time.sleep(1)

    # Force kill if still running
    print("  Timed out — force killing...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGKILL)
    _pid_path(code_path).unlink(missing_ok=True)
    print(f"  fiam stopped (forced).")


def cmd_status(args: argparse.Namespace) -> None:
    """Show daemon status and memory stats."""
    code_path = _project_root()
    toml = _toml_path()

    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  fiam: running (PID {pid})")
    else:
        print(f"  fiam: stopped")

    if toml.exists():
        from fiam.config import FiamConfig
        config = FiamConfig.from_toml(toml, code_path)
        print(f"  home: {config.home_path}")

        events_dir = config.events_dir
        if events_dir.is_dir():
            count = len(list(events_dir.glob("*.md")))
            print(f"  events: {count}")

        emb_dir = config.embeddings_dir
        if emb_dir.is_dir():
            count = len(list(emb_dir.glob("*.npy")))
            print(f"  embeddings: {count}")

        cursor = _load_cursor(code_path)
        if cursor:
            latest_mtime = max(v.get("mtime", 0) for v in cursor.values())
            if latest_mtime > 0:
                from datetime import datetime
                dt = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  last processed: {dt}")
    else:
        print("  (no fiam.toml — run 'fiam init')")
