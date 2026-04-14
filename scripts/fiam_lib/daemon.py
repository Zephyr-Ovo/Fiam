"""Daemon lifecycle — start, stop, status."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _build_config, _pid_path, _is_daemon_running

# ------------------------------------------------------------------
# Pipeline diagnostic log  (logs/pipeline.log)
# ------------------------------------------------------------------
_plog = logging.getLogger("fiam.pipeline")
_plog.setLevel(logging.DEBUG)
_plog.propagate = False  # don't bubble to root logger
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


# ------------------------------------------------------------------
# Session management helpers
# ------------------------------------------------------------------

def _load_active_session(config) -> dict | None:
    """Load active_session.json → {session_id, started_at} or None."""
    path = config.active_session_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("session_id"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_active_session(config, session_id: str) -> None:
    """Write active_session.json with current session_id and timestamp."""
    path = config.active_session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _retire_session(config, reason: str = "error") -> None:
    """Archive current session → self/retired/ and clear active_session.json."""
    session = _load_active_session(config)
    if not session:
        return
    retired_dir = config.self_dir / "retired"
    retired_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H%M%S")
    archive = retired_dir / f"{ts}_{reason}.json"
    archive.write_text(json.dumps(session, indent=2), encoding="utf-8")
    config.active_session_path.unlink(missing_ok=True)


def _is_interactive(config) -> bool:
    """Check if a human is interactively using the CC session."""
    lock = config.interactive_lock_path
    if not lock.exists():
        return False
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        pid = data.get("pid")
        if pid:
            # Check if the process is still alive
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                # Process gone — stale lock
                lock.unlink(missing_ok=True)
                return False
    except (json.JSONDecodeError, OSError):
        pass
    # Lock file exists but no valid PID — treat as stale after 2 hours
    try:
        age = time.time() - lock.stat().st_mtime
        if age > 7200:
            lock.unlink(missing_ok=True)
            return False
        return True
    except OSError:
        return False


def _wake_session(config, message: str, tag: str = "tg") -> bool:
    """Send a message to Fiet via `claude -p --resume <id>`.

    If no active session exists, creates a new session and saves its ID.
    Returns True if the message was sent successfully.
    Also extracts outbound markers from the response and writes to outbox.
    """
    session = _load_active_session(config)
    resuming = session is not None

    cmd = [
        "claude", "-p", f"[wake:{tag}] {message}",
        "--output-format", "json",
        "--max-turns", "10",
    ]
    if resuming:
        cmd.extend(["--resume", session["session_id"]])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(config.home_path),
        )
        _plog.info("wake cmd=%s  exit=%d", " ".join(cmd[:4]), result.returncode)
        if result.stderr:
            _plog.info("wake stderr: %s", result.stderr.strip()[:500])

        # Parse result JSON — even on exit 1 (error_max_turns still has session_id)
        data = None
        try:
            data = json.loads(result.stdout)
            _plog.info("wake response: cost=$%.4f  session=%s  subtype=%s",
                       data.get("total_cost_usd", 0),
                       data.get("session_id", "?")[:8],
                       data.get("subtype", ""))
        except (json.JSONDecodeError, ValueError):
            pass

        if result.returncode != 0:
            # error_max_turns is a partial success — session exists, save it
            if data and data.get("subtype") == "error_max_turns":
                _plog.warning("wake hit max_turns — partial success")
                _console.print(f"  [yellow]wake partial[/] (max_turns)")
            else:
                _plog.warning("wake FAILED stdout: %s", result.stdout.strip()[:500])
                _console.print(f"  [red]wake failed[/] (exit {result.returncode})")
                # Still save session_id if we got one on a new session
                if data and not resuming:
                    new_sid = data.get("session_id", "")
                    if new_sid:
                        _save_active_session(config, new_sid)
                        _plog.info("saved session from failed wake: %s", new_sid)
                return False

        # Save new session_id
        if data and not resuming:
            new_sid = data.get("session_id", "")
            if new_sid:
                _save_active_session(config, new_sid)
                _console.print(f"  [dim]└ new session {new_sid[:8]}[/dim]")
                _plog.info("new session created: %s", new_sid)

        # Extract outbound markers from response
        if data:
            response_text = data.get("result", "")
            if response_text:
                _extract_outbound_markers(config, response_text)

        return True
    except subprocess.TimeoutExpired:
        _console.print(f"  [red]wake timeout[/]")
        return False
    except FileNotFoundError:
        _console.print(f"  [red]claude not found[/]")
        return False


# Regex for outbound message markers: [→tg:Name] or [→email:Name]
_OUTBOUND_RE = re.compile(
    r"\[→(tg|telegram|email):([^\]]+)\]\s*(.+?)(?=\[→(?:tg|telegram|email):|$)",
    re.DOTALL,
)


def _extract_outbound_markers(config, text: str) -> int:
    """Extract [→channel:recipient] markers from text and write to outbox/.

    Returns count of outbox files written.
    """
    outbox = config.outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    count = 0

    for match in _OUTBOUND_RE.finditer(text):
        channel, recipient, body = match.group(1), match.group(2), match.group(3).strip()
        if not body:
            continue
        via = "telegram" if channel in ("tg", "telegram") else "email"
        ts = time.strftime("%m%d_%H%M%S")
        fname = f"auto_{ts}_{count:02d}.md"
        content = (
            f"---\nto: {recipient.strip()}\nvia: {via}\n"
            f"priority: normal\n---\n\n{body}\n"
        )
        (outbox / fname).write_text(content, encoding="utf-8")
        count += 1

    if count:
        _console.print(f"  [dim]└ outbox +{count}[/dim]")
    return count


def cmd_start(args: argparse.Namespace) -> None:
    """Daemon: poll JSONL for activity, process on idle timeout."""
    config = _build_config(args)
    code_path = _project_root()

    # ── Setup pipeline log ──
    log_dir = code_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%m-%d %H:%M:%S"))
    _plog.addHandler(_fh)
    _plog.info("─── daemon start (PID %d) ───", os.getpid())

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
    # Regex to strip daemon wake signal tags from user text
    _wake_tag_re = re.compile(r"\[wake:[^\]]*\]\s*")

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
                        # Strip [wake:xxx] tags — daemon signals, not real content
                        cleaned = _wake_tag_re.sub("", content).strip()
                        if cleaned:
                            parts.append(cleaned)
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

        _plog.info("process  turns=%d files=%d", len(total_turns), len(jf_list))
        if total_turns:
            try:
                r = post_session(config, total_turns)
                _console.print(f"  [bold #a8f0e8]+{r['events_written']}[/] memories")
                _plog.info("post_session  events_written=%d", r['events_written'])
            except Exception as e:
                _console.print(f"  [red]error:[/] {e}")
                _plog.error("post_session error: %s", e, exc_info=True)

            try:
                store = HomeStore(config)
                from fiam.retriever import joint as joint_retriever
                events = joint_retriever.search("", store, config)
                _write_recall(config, events)
                recall_query_vec = None
                _console.print(f"  recall [#f7a8d0]←[/] [bold #f7e08a]{len(events)}[/] fragments")
                _plog.info("recall updated  fragments=%d", len(events))
            except Exception as e:
                _console.print(f"  [red]recall error:[/] {e}")
                _plog.error("recall error: %s", e, exc_info=True)
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
                _plog.debug("poll  tg=%d email=%d", n_tg, n_email)
                if n_tg or n_email:
                    ts = time.strftime("%H:%M")
                    _console.print(f"  [dim]└[{ts}][/dim] [bold #7eb8f7]✉[/]  inbox +{n_tg + n_email}")
                    _plog.info("inbox  tg=+%d email=+%d", n_tg, n_email)

                    # Wake Fiet if inbox has messages and not interactive
                    interactive = _is_interactive(config)
                    _plog.debug("interactive=%s", interactive)
                    if not interactive:
                        inbox_jsonl = config.inbox_jsonl_path
                        jsonl_exists = inbox_jsonl.exists() and inbox_jsonl.stat().st_size > 0
                        _plog.debug("inbox_jsonl exists=%s path=%s", jsonl_exists, inbox_jsonl)
                        if jsonl_exists:
                            tag = "tg" if n_tg else "email"
                            summary = f"{n_tg + n_email} new message(s)"
                            _plog.info("wake attempt  tag=%s summary=%s", tag, summary)
                            ok = _wake_session(config, summary, tag=tag)
                            if ok:
                                ts2 = time.strftime("%H:%M")
                                _console.print(f"  [dim]└[{ts2}][/dim] [bold #a8f0e8]↗[/]  wake sent")
                                _plog.info("wake OK")
                            else:
                                _plog.warning("wake FAILED, retrying...")
                                ok2 = _wake_session(config, summary, tag=tag)
                                if not ok2:
                                    _console.print(f"  [yellow]wake failed twice — messages remain queued[/]")
                                    _plog.error("wake FAILED x2 — messages queued")
                                    session = _load_active_session(config)
                                    if session:
                                        _retire_session(config, reason="wake_failed")
                    else:
                        _console.print(f"  [dim]interactive session — messages queued for hook[/dim]")
                        _plog.info("interactive — skipping wake")
            except Exception as e:
                _plog.error("inbox error: %s", e, exc_info=True)
                if config.debug_mode:
                    print(f"  [inbox] Error: {e}", file=sys.stderr)

        # ── Outbox dispatch ──
        try:
            sweep_outbox(config)
        except Exception as e:
            _plog.error("outbox error: %s", e)

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
                _plog.info("jsonl ACTIVE")
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
            _plog.info("idle timeout → processing")
            _process_pending()

    # ── Graceful shutdown: process any pending content before exit ──
    if shutdown_requested and active:
        _console.print()
        _console.print(f"  [bold #f7a8d0]⟳[/]  wrapping up...")
        _process_pending()

    # Cleanup
    pid_file.unlink(missing_ok=True)
    _plog.info("─── daemon stop ───")
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
