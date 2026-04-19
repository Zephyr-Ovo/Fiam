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
from dataclasses import dataclass
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
)
from fiam_lib.postman import sweep_outbox, fetch_inbox, fetch_tg_inbox
from fiam_lib.scheduler import extract_wake_tags, append_to_schedule, load_due
from fiam_lib.cost import log_cost, check_budget
from fiam_lib.ui import _console, _flow, _ANIM_IDLE, _ANIM_ACTIVE, _animated_sleep


# ------------------------------------------------------------------
# Comm state: notify / mute / block
# ------------------------------------------------------------------

def _load_comm_state(config) -> str:
    """Load communication state from self/comm_state.json. Default: 'notify'."""
    state_file = Path(config.home_path) / "self" / "comm_state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            return data.get("state", "notify")
        except (json.JSONDecodeError, OSError):
            pass
    return "notify"


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
    if config.cc_model:
        cmd.extend(["--model", config.cc_model])
    if config.cc_disallowed_tools:
        cmd.extend(["--disallowedTools"] + [t.strip() for t in config.cc_disallowed_tools.split(",") if t.strip()])
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
            # Log cost to ledger
            cost = data.get("total_cost_usd", 0)
            if cost > 0:
                log_cost(config, cost,
                         session_id=data.get("session_id", ""),
                         tag=tag,
                         turns=data.get("num_turns", 0))
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
                # Extract WAKE tags for scheduler
                wake_tags = extract_wake_tags(response_text)
                if wake_tags:
                    n = append_to_schedule(wake_tags, config)
                    _plog.info("scheduler  +%d wake(s) queued", n)
                    _console.print(f"  [dim]└ scheduler +{n} wake(s)[/dim]")

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

    # ── Load .env (secrets like API keys, bot tokens) ──
    env_file = code_path / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = val

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

    # Initial imports for new architecture
    from fiam.retriever.embedder import Embedder
    from fiam.store.pool import Pool
    from fiam.conductor import Conductor
    import numpy as np

    # ── Conductor: new architecture routing layer ──
    _pool = Pool(config.pool_dir, dim=config.embedding_dim)
    _conductor_embedder = Embedder(config)
    event_count = _pool.event_count
    _conductor = Conductor(
        pool=_pool,
        embedder=_conductor_embedder,
        config=config,
        flow_path=config.flow_path,
        recall_path=config.background_path,
        drift_threshold=0.65,
    )

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

    # Live recall: conductor handles drift detection and recall internally
    recall_min_chars = 40                       # skip trivial messages
    # Regex to strip daemon wake signal tags from user text
    _wake_tag_re = re.compile(r"\[wake:[^\]]*\]\s*")

    def _refresh_recall_for_task(context: str) -> None:
        """Refresh recall.md based on task context before a wake.

        Uses conductor's embedder + spreading activation retrieval.
        """
        if len(context) < recall_min_chars:
            return
        try:
            vec = _conductor_embedder.embed(context)
            _conductor._refresh_recall(vec)
            _plog.info("task recall (spread)  context=%s", context[:60])
        except Exception as e:
            _plog.error("task recall error: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Beat ingestion via Conductor
    #
    # Conductor handles: embed → StreamGorge segmentation → Pool storage
    # → drift detection → recall refresh.  Replaces the old manual
    # TextTiling depth code + store_segment pipeline.
    # ------------------------------------------------------------------

    def _ingest_new_beats() -> None:
        """Parse unread CC JSONL via Conductor; it handles segment cuts internally."""
        if not jsonl_dir.is_dir():
            return
        jf_list = list(jsonl_dir.glob("*.jsonl"))
        if not jf_list:
            return

        cursor = _load_cursor(code_path)

        for jf in sorted(jf_list, key=lambda p: p.stat().st_mtime):
            jkey = jf.name
            entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})
            try:
                jf_mtime = jf.stat().st_mtime
            except FileNotFoundError:
                continue
            if jf_mtime < entry["mtime"]:
                entry["byte_offset"] = 0

            results, new_offset = _conductor.ingest_cc_output(jf, entry["byte_offset"])
            n_beats = len(results)
            n_events = sum(1 for r in results if r is not None)
            if n_beats:
                _log_action("ingest", f"{n_beats} beats", events=n_events)
                if n_events:
                    for eid in results:
                        if eid:
                            _console.print(f"  [bold #a8f0e8]+1[/] memory [{eid}]")
                            _plog.info("conductor event  id=%s", eid)

            cursor[jkey] = {"byte_offset": new_offset, "mtime": jf_mtime}

        _save_cursor(code_path, cursor)

    def _process_pending() -> None:
        """Flush conductor beat buffer and any unread JSONL on idle/shutdown."""
        nonlocal active

        # Parse any remaining unread beats
        try:
            _ingest_new_beats()
        except Exception as e:
            _plog.error("final ingest error: %s", e, exc_info=True)

        # Flush conductor buffer → pool events
        try:
            event_ids = _conductor.flush_all()
            if event_ids:
                for eid in event_ids:
                    _console.print(f"  [bold #a8f0e8]+1[/] memory (idle) [{eid}]")
                    _plog.info("conductor flush  id=%s", eid)
                _log_action("flush", f"{len(event_ids)} events")
            else:
                _console.print(f"  [dim]·  up to date[/dim]")
        except Exception as e:
            _console.print(f"  [red]flush error:[/] {e}")
            _plog.error("conductor flush error: %s", e, exc_info=True)

        active = False

    # ------------------------------------------------------------------
    # Daemon state export for debug dashboard
    # ------------------------------------------------------------------
    state_log: list[dict] = []  # ring buffer of recent actions (max 50)

    def _log_action(action: str, detail: str = "", **extra) -> None:
        """Append an action entry to the state log ring buffer."""
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "action": action,
            "detail": detail,
            **extra,
        }
        state_log.append(entry)
        if len(state_log) > 50:
            state_log.pop(0)

    def _write_daemon_state() -> None:
        """Write current daemon state to logs/daemon_state.json for the debug dashboard."""
        try:
            state_path = code_path / "logs" / "daemon_state.json"
            comm = _load_comm_state(config)
            session = _load_active_session(config)

            state = {
                "pid": os.getpid(),
                "uptime": time.strftime("%H:%M:%S"),
                "active": active,
                "comm_state": comm,
                "session": session.get("session_id", "")[:8] if session else None,
                "conductor_beat_buf": len(_conductor._beat_buf),
                "pool_events": len(_pool.load_events()),
                "last_activity": time.strftime("%H:%M:%S", time.localtime(last_activity)) if last_activity else None,
                "recent_actions": state_log[-20:],
            }
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # non-critical

    last_inbox_check: float = 0.0
    inbox_interval = 60  # check TG + email every 60s
    last_replay_check: float = 0.0
    replay_interval = 30 * 60  # memory replay every 30 minutes when idle

    while running:
        _animated_sleep(
            poll_interval,
            _ANIM_ACTIVE if active else _ANIM_IDLE,
            stop_check=lambda: not running,
        )

        if not running:
            break

        # Write daemon state for debug dashboard (every poll cycle)
        _write_daemon_state()

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
                    _log_action("inbox", f"tg={n_tg} email={n_email}")

                    # Check comm state
                    comm_state = _load_comm_state(config)
                    _plog.debug("comm_state=%s", comm_state)

                    if comm_state == "block":
                        _plog.info("comm_state=block — messages archived, no wake")
                        _console.print(f"  [dim]comm: block — messages archived[/dim]")
                    elif comm_state == "mute":
                        _plog.info("comm_state=mute — messages queued, wake deferred")
                        _console.print(f"  [dim]comm: mute — queued for later[/dim]")
                    else:
                        # notify (default) — wake Fiet
                        # Wake Fiet if inbox has messages and not interactive
                        interactive = _is_interactive(config)
                        _plog.debug("interactive=%s", interactive)
                        if not interactive:
                            # Budget check before wake
                            budget_ok, budget_reason = check_budget(config)
                            if not budget_ok:
                                _plog.warning("budget exceeded — skipping inbox wake: %s", budget_reason)
                                _console.print(f"  [yellow]budget: {budget_reason}[/]")
                            else:
                                inbox_jsonl = config.inbox_jsonl_path
                                jsonl_exists = inbox_jsonl.exists() and inbox_jsonl.stat().st_size > 0
                                _plog.debug("inbox_jsonl exists=%s path=%s", jsonl_exists, inbox_jsonl)
                                if jsonl_exists:
                                    tag = "tg" if n_tg else "email"
                                    summary = f"{n_tg + n_email} new message(s)"
                                    # Read inbox content for recall context
                                    try:
                                        inbox_text = inbox_jsonl.read_text(encoding="utf-8").strip()
                                        # Extract message bodies from JSONL
                                        inbox_parts = []
                                        for line in inbox_text.splitlines():
                                            try:
                                                obj = json.loads(line)
                                                body = obj.get("body", obj.get("text", ""))
                                                if body:
                                                    inbox_parts.append(body[:200])
                                            except json.JSONDecodeError:
                                                pass
                                        if inbox_parts:
                                            _refresh_recall_for_task("\n".join(inbox_parts))
                                    except Exception as e:
                                        _plog.debug("inbox recall skip: %s", e)
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

        # ── Memory replay (idle consolidation) ──
        # When no active conversation for > 30min, periodically replay
        # top-priority memories to strengthen fading-but-important events.
        if (not active
                and (time.time() - last_replay_check > replay_interval)
                and last_activity > 0
                and (time.time() - last_activity) > 30 * 60):
            last_replay_check = time.time()
            try:
                from datetime import datetime, timezone
                events = _pool.load_events()
                if events:
                    # Simple replay: bump access_count on least-accessed events
                    sorted_by_access = sorted(events, key=lambda e: e.access_count)
                    top = sorted_by_access[:5]
                    bumped = 0
                    for ev in top:
                        ev.access_count += 1
                        bumped += 1
                    _pool.save_events()
                    _plog.info("replay: consolidated %d memories (pool-based)", bumped)
            except Exception as e:
                _plog.error("replay error: %s", e)

        # ── Scheduler: check for due wakes ──
        try:
            from fiam_lib.scheduler import archive_stale, mark_fired
            # Archive anything past grace window or max attempts.
            missed, failed = archive_stale(config)
            if missed or failed:
                _plog.info("scheduler archived  missed=%d failed=%d", missed, failed)

            due = load_due(config)
            for entry in due:
                reason = entry.get("reason", "scheduled wake")
                wake_type = entry.get("type", "check")
                _plog.info("scheduler fire  type=%s reason=%s", wake_type, reason)

                # Budget check before scheduled wake.
                # Defer (don't drop) so the wake retries once quota refreshes.
                budget_ok, budget_reason = check_budget(config)
                if not budget_ok:
                    _plog.warning("budget exceeded — deferring scheduled wake: %s", budget_reason)
                    _console.print(f"  [yellow]⏰ {reason} — deferred ({budget_reason})[/]")
                    mark_fired(entry, config, success=False)
                    continue

                _console.print(f"  [bold #e8c8ff]⏰[/] scheduled: {reason}")
                _refresh_recall_for_task(reason)
                ok = _wake_session(config, f"[scheduled:{wake_type}] {reason}", tag="sched")
                mark_fired(entry, config, success=ok)
                if ok:
                    _plog.info("scheduler wake OK")
                else:
                    attempts = int(entry.get("attempts", 0)) + 1
                    _plog.warning("scheduler wake FAILED  retry=%d", attempts)
        except Exception as e:
            _plog.error("scheduler error: %s", e, exc_info=True)

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

            # Ingest new beats via Conductor (handles embed + gorge + recall)
            try:
                _ingest_new_beats()
            except Exception as e:
                _log_action("error", f"ingest: {e}")
                _plog.error("ingest error: %s", e, exc_info=True)
                if config.debug_mode:
                    print(f"  [ingest] Error: {e}", file=sys.stderr)

            _write_daemon_state()
            continue

        # Check idle timeout
        if active and (time.time() - last_activity) > idle_timeout:
            ts = time.strftime("%H:%M")
            _console.print(f"  [dim]└[{ts}][/dim] [bold #f7a8d0]⟳[/]  processing...")
            _plog.info("idle timeout → processing")
            _process_pending()

        _write_daemon_state()

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
