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
from datetime import datetime, timezone
from pathlib import Path

from fiam_lib.core import (
    _project_root,
    _toml_path,
    _build_config,
    _pid_path,
    _is_daemon_running,
    _load_env_file,
)

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
from fiam_lib.postman import sweep_outbox
from fiam_lib.todo import extract_scheduled_items, extract_state_tag, append_to_todo, load_due
from fiam_lib.app_markers import parse_app_cot
from fiam_lib.cost import log_cost, check_budget
from fiam_lib.ui import _console, _flow, _ANIM_IDLE, _ANIM_ACTIVE, _animated_sleep


# ------------------------------------------------------------------
# AI state: notify / mute / block / sleep / busy / together
# ------------------------------------------------------------------

_AI_STATES = {"notify", "mute", "block", "sleep", "busy", "together", "online"}


def _default_ai_state() -> dict:
    return {"state": "notify"}


def _write_ai_state(config, data: dict) -> None:
    path = config.ai_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_ai_state(
    config,
    state: str,
    *,
    reason: str = "",
    until: str = "",
    expires_at: str = "",
) -> None:
    state = state if state in _AI_STATES else "notify"
    data = {
        "state": state,
        "since": config.now_local().isoformat(),
    }
    if reason:
        data["reason"] = reason
    if until:
        data["until"] = until
    if expires_at:
        data["expires_at"] = expires_at
    _write_ai_state(config, data)


def _clear_ai_state(config) -> None:
    config.ai_state_path.unlink(missing_ok=True)


def _parse_state_time(config, raw: str):
    try:
        dt = datetime.fromisoformat(str(raw))
        return config.ensure_timezone(dt)
    except (TypeError, ValueError):
        return None


def _load_ai_state(config) -> dict:
    """Load the unified AI state."""
    path = config.ai_state_path
    data: dict | None = None
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
    if not data:
        return _default_ai_state()

    state = str(data.get("state", "notify"))
    if state not in _AI_STATES:
        _clear_ai_state(config)
        return _default_ai_state()

    expires_at = data.get("expires_at")
    if expires_at:
        dt = _parse_state_time(config, expires_at)
        if dt is None or datetime.now(timezone.utc) >= dt:
            _clear_ai_state(config)
            return _default_ai_state()

    if state == "sleep":
        until = str(data.get("until", ""))
        if until == "open":
            return data
        dt = _parse_state_time(config, until)
        if dt is None or datetime.now(timezone.utc) >= dt:
            _clear_ai_state(config)
            return _default_ai_state()

    return data


def _load_comm_state(config) -> str:
    """Compatibility wrapper for older call sites."""
    return str(_load_ai_state(config).get("state", "notify"))


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


def _save_active_session(config, session_id: str, events_count: int = 0) -> None:
    """Write active_session.json with current session_id, timestamp and event counter."""
    path = config.active_session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "started_at": config.now_local().isoformat(),
        "events_count": int(events_count),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _increment_session_events(config) -> int:
    """Bump events_count on the active session and return the new value.

    No-op (returns 0) if there is no active session.
    """
    session = _load_active_session(config)
    if not session:
        return 0
    new_count = int(session.get("events_count", 0) or 0) + 1
    session["events_count"] = new_count
    config.active_session_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    return new_count


def _retire_session(config, reason: str = "error") -> None:
    """Archive current session → self/retired/ and clear active_session.json."""
    session = _load_active_session(config)
    if not session:
        return
    retired_dir = config.self_dir / "retired"
    retired_dir.mkdir(parents=True, exist_ok=True)
    ts = config.now_local().strftime("%Y-%m-%d_%H%M%S")
    archive = retired_dir / f"{ts}_{reason}.json"
    archive.write_text(json.dumps(session, indent=2), encoding="utf-8")
    config.active_session_path.unlink(missing_ok=True)


def _save_sleep_state(config, sleeping_until: str, reason: str) -> None:
    """Persist AI sleep state in the unified ai_state.json."""
    _save_ai_state(config, "sleep", until=sleeping_until, reason=reason)


def _clear_sleep_state(config) -> None:
    if _load_ai_state(config).get("state") == "sleep":
        _clear_ai_state(config)


def _is_sleeping(config) -> tuple[bool, str | None]:
    """Returns (is_sleeping, sleeping_until_iso_or_open).

    Auto-clears expired explicit sleep states.
    """
    state = _load_ai_state(config)
    if state.get("state") != "sleep":
        return False, None
    return True, str(state.get("until", "open"))


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


def _wake_session(config, message: str, tag: str = "inbox", conductor=None) -> bool:
    """Send a message to the AI runtime via `claude -p --resume <id>`.

    If no active session exists, creates a new session and saves its ID.
    Returns True if the message was sent successfully.
    Dispatches outbound markers from the response via conductor.dispatch().
    """
    session = _load_active_session(config)
    resuming = session is not None

    cmd = [
        "claude", "-p", message,
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
                # Stale session id (claude lost it) → clear & retry once without --resume
                if resuming and "No conversation found with session ID" in (result.stderr or ""):
                    _plog.info("wake retrying without stale --resume")
                    try:
                        config.active_session_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    cmd_retry = [c for c in cmd if c != "--resume" and c != session["session_id"]]
                    # rebuild cleanly: drop the two consecutive items
                    cmd_retry = list(cmd)
                    if "--resume" in cmd_retry:
                        idx = cmd_retry.index("--resume")
                        del cmd_retry[idx:idx+2]
                    result = subprocess.run(
                        cmd_retry, capture_output=True, text=True,
                        timeout=180, cwd=str(config.home_path),
                    )
                    _plog.info("wake retry exit=%d", result.returncode)
                    try:
                        data = json.loads(result.stdout)
                    except (json.JSONDecodeError, ValueError):
                        data = None
                    resuming = False
                    # Save new session id from retry regardless of outcome
                    if data:
                        new_sid = data.get("session_id", "")
                        if new_sid:
                            _save_active_session(config, new_sid)
                    if result.returncode != 0:
                        if data and data.get("subtype") == "error_max_turns":
                            _plog.warning("wake retry hit max_turns — partial success")
                            _console.print(f"  [yellow]wake partial[/] (max_turns, retry)")
                        else:
                            _plog.warning("wake retry FAILED stdout: %s", (result.stdout or "").strip()[:500])
                            if result.stderr:
                                _plog.warning("wake retry stderr: %s", result.stderr.strip()[:500])
                            return False
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

        # Extract outbound markers from response → dispatch via conductor
        if data:
            response_text = data.get("result", "")
            if response_text:
                _extract_and_dispatch(config, response_text, conductor)
                later_todos = extract_scheduled_items(response_text, config)
                if later_todos:
                    added = append_to_todo(later_todos, config)
                    _plog.info("todo  +%d item(s) queued", added)
                    _console.print(f"  [dim]└ todo +{added} item(s)[/dim]")
                state_tag = extract_state_tag(response_text, config)
                if state_tag:
                    state = state_tag["state"]
                    _save_ai_state(
                        config,
                        state,
                        until=str(state_tag.get("until") or ""),
                        reason=str(state_tag.get("reason") or ""),
                    )
                    _plog.info("AI state  state=%s reason=%s", state, state_tag.get("reason", ""))

        # Bump per-session event counter and rotate if we've hit the cap
        # (skip if sleep already retired the session above)
        if _load_active_session(config) is not None:
            new_count = _increment_session_events(config)
            cap = max(1, int(getattr(config, "events_per_session", 10)))
            if new_count >= cap:
                _retire_session(config, reason="rotated")
                _plog.info("session rotated after %d events (cap=%d)", new_count, cap)
                _console.print(f"  [dim]└ session rotated ({new_count}/{cap})[/dim]")

        return True
    except subprocess.TimeoutExpired:
        _console.print(f"  [red]wake timeout[/]")
        return False
    except FileNotFoundError:
        _console.print(f"  [red]claude not found[/]")
        return False


def _run_claude_json(config, message: str, *, tag: str) -> tuple[bool, dict | None]:
    session = _load_active_session(config)
    resuming = session is not None
    cmd = [
        "claude", "-p", message,
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
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False, None
    if result.returncode != 0 and data.get("subtype") != "error_max_turns":
        return False, data
    if data and not resuming:
        new_sid = data.get("session_id", "")
        if new_sid:
            _save_active_session(config, new_sid)
    cost = data.get("total_cost_usd", 0)
    if cost:
        log_cost(config, cost, session_id=data.get("session_id", ""), tag=tag, turns=data.get("num_turns", 0))
    return True, data


def _append_transcript(config, source: str, message: dict) -> dict:
    clean_source = re.sub(r"[^A-Za-z0-9_-]+", "_", (source or "favilla").strip().lower()).strip("_") or "favilla"
    path = config.home_path / "transcript" / f"{clean_source}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": str(message.get("id") or f"srv-{int(time.time() * 1000)}"),
        "role": str(message.get("role") or "ai"),
        "t": int(message.get("t") or int(time.time() // 60)),
    }
    for key in (
        "text", "raw_text", "runtime",
        "attachments", "thinking", "thinkingLocked", "segments", "hold",
        "divider", "recallUsed", "error",
        # Step 6: extended schema
        "tool_calls_summary", "actions", "presence", "metrics", "meta",
    ):
        if key in message and message[key] not in (None, [], ""):
            record[key] = message[key]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _extract_and_dispatch(config, text: str, conductor) -> int:
    """Extract [→channel:recipient] markers and dispatch via Conductor.

    Returns count of dispatched messages.
    """
    from fiam.markers import parse_outbound_markers
    from fiam.plugins import resolve_dispatch_target

    count = 0
    for marker in parse_outbound_markers(text):
        target = resolve_dispatch_target(config, marker.channel)
        if target is None:
            _plog.info("dispatch skipped disabled plugin channel=%s", marker.channel)
            continue
        if conductor is not None:
            try:
                conductor.dispatch(target, marker.recipient, marker.body)
                count += 1
            except Exception as e:
                _plog.error("dispatch failed: %s", e)
        else:
            _plog.warning("no conductor for dispatch, skipping")

    if count:
        _console.print(f"  [dim]└ dispatched {count}[/dim]")
    return count


def cmd_start(args: argparse.Namespace) -> None:
    """Daemon: poll JSONL for activity, process on idle timeout."""
    config = _build_config(args)
    code_path = _project_root()

    # ── Load .env (secrets like API keys, bot tokens) ──
    _load_env_file(code_path)

    # ── Setup pipeline log ──
    log_dir = code_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%m-%d %H:%M:%S"))
    _plog.addHandler(_fh)
    _plog.info("─── daemon start (PID %d) ───", os.getpid())

    def _project_time(fmt: str, timestamp: float | None = None) -> str:
        if timestamp is None:
            dt = config.now_utc()
        else:
            dt = datetime.fromtimestamp(timestamp, timezone.utc)
        return dt.astimezone(config.project_tz()).strftime(fmt)

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
    process_pending_on_shutdown = False

    def _shutdown(sig, frame):
        nonlocal running, shutdown_requested, process_pending_on_shutdown
        if shutdown_requested:
            # Second Ctrl+C = force exit
            sys.exit(1)
        shutdown_requested = True
        process_pending_on_shutdown = sig != signal.SIGTERM
        running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _shutdown)

    # Initial imports for new architecture
    from fiam.retriever.embedder import Embedder
    from fiam.store.features import FeatureStore
    from fiam.store.pool import Pool
    from fiam.conductor import Conductor
    from fiam.bus import Bus, RECEIVE_ALL
    import queue as _queue

    # ── MQTT bus: replaces all channel polling ──
    _bus = Bus(client_id="fiam-daemon")
    _inbox_q: _queue.Queue = _queue.Queue()

    def _on_receive(channel: str, payload: dict) -> None:
        """Bus thread → main loop queue. Convert MQTT payload to msg dict."""
        text = (payload.get("text") or "").strip()
        if not text:
            return
        channel_name = str(payload.get("channel") or payload.get("source") or channel)
        try:
            from fiam.plugins import is_receive_enabled
            if not is_receive_enabled(config, channel_name):
                _plog.info("receive skipped disabled plugin channel=%s", channel_name)
                return
        except Exception:
            pass
        t_raw = payload.get("t")
        t_val: datetime | None = None
        if isinstance(t_raw, str):
            try:
                t_val = datetime.fromisoformat(t_raw)
            except ValueError:
                t_val = None
        elif isinstance(t_raw, datetime):
            t_val = t_raw
        meta = {
            key: value for key, value in payload.items()
            if key not in {"text", "channel", "source", "t"} and value not in (None, "", [])
        }
        _inbox_q.put({
            "channel": channel_name,
            "from_name": payload.get("from_name", ""),
            "text": text,
            "t": t_val or datetime.now(timezone.utc),
            "meta": meta,
        })

    _bus.subscribe(RECEIVE_ALL, _on_receive)
    try:
        _bus.connect(config.mqtt_host, config.mqtt_port, config.mqtt_keepalive)
        _bus.loop_start()
        _plog.info("bus connected to %s:%d", config.mqtt_host, config.mqtt_port)
    except Exception as e:
        _plog.error("bus connect failed: %s — running without MQTT", e)
        _console.print(f"  [yellow]MQTT broker unreachable ({e}); inbound channels disabled[/]")

    # ── Pool + Embedder ──
    _pool = Pool(config.pool_dir, dim=config.embedding_dim)
    _feature_store = FeatureStore(config.feature_dir, dim=config.embedding_dim)
    _conductor_embedder = Embedder(config)
    event_count = _pool.event_count

    # ── Recall: daemon owns this (recall never enters flow) ──
    _recall_top_k = 3

    def _refresh_recall(query_vec) -> None:
        """Run spreading activation and write recall.md + .recall_dirty marker."""
        from fiam.runtime.recall import refresh_recall

        count = refresh_recall(config, _pool, query_vec, top_k=_recall_top_k)
        if count:
            _plog.info("recall refreshed (%d fragments)", count)

    # ── Conductor: stateless hub, drift → _refresh_recall callback ──
    _conductor = Conductor(
        pool=_pool,
        embedder=_conductor_embedder,
        config=config,
        flow_path=config.flow_path,
        drift_threshold=config.drift_threshold,
        gorge_max_beat=config.gorge_max_beat,
        gorge_min_depth=config.gorge_min_depth,
        gorge_stream_confirm=config.gorge_stream_confirm,
        on_drift=_refresh_recall,
        bus=_bus,
        memory_mode=config.memory_mode,
        feature_store=_feature_store,
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
    daemon_started_at = time.time()

    def _current_jsonl_mtime() -> float:
        if not jsonl_dir.is_dir():
            return 0.0
        try:
            return max((p.stat().st_mtime for p in jsonl_dir.glob("*.jsonl")), default=0.0)
        except OSError:
            return 0.0

    last_activity: float = _current_jsonl_mtime()
    active = False
    idle_timeout = config.idle_timeout_minutes * 60
    poll_interval = config.poll_interval_seconds

    def _seed_jsonl_cursor_to_present() -> None:
        if not jsonl_dir.is_dir():
            return
        cursor = _load_cursor(code_path)
        changed = False
        for jf in jsonl_dir.glob("*.jsonl"):
            try:
                stat = jf.stat()
            except FileNotFoundError:
                continue
            if stat.st_mtime <= daemon_started_at:
                cursor[jf.name] = {"byte_offset": stat.st_size, "mtime": stat.st_mtime}
                changed = True
        if changed:
            _save_cursor(code_path, cursor)

    # Live recall: conductor fires on_drift → _refresh_recall (above)

    def _write_pending_external(config, msgs: list[dict]) -> None:
        """Append pre-formatted external messages for inject.sh hook delivery."""
        parts = []
        for m in msgs:
            parts.append(f"[{m['channel']}:{m['from_name']}] {m['text']}")
        formatted = "\n\n".join(parts)
        path = config.pending_external_path
        with open(path, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")

    def _format_user_message(msgs: list[dict], prefix: str = "") -> str:
        """Format external messages for `claude -p` user field.

        Optional ``prefix`` (typically a ``[context]...[/context]`` block built
        by :func:`_build_wake_context`) is prepended verbatim.
        """
        parts = []
        for m in msgs:
            parts.append(f"[{m['channel']}:{m['from_name']}] {m['text']}")
        body = "\n\n".join(parts)
        return f"{prefix}{body}" if prefix else body

    def _build_wake_context(prior_sleep: dict | None, wake_trigger: str) -> str:
        """Return a one-shot ``[context]`` prefix when waking from sleep.

        ``prior_sleep`` is the ai_state dict captured before clearing sleep.
        Returns ``""`` if there was no sleep state to announce.
        """
        if not prior_sleep or prior_sleep.get("state") != "sleep":
            return ""
        until = str(prior_sleep.get("until", "open") or "open")
        reason = str(prior_sleep.get("reason", "") or "").strip()
        lines = [
            "[context]",
            "last_state=sleep",
            f"sleep_until_planned={until}",
        ]
        if reason:
            lines.append(f"sleep_reason={reason}")
        lines.append(f"wake_trigger={wake_trigger}")
        unread = _count_notifications_inbox()
        if unread > 0:
            lines.append(f"notifications_inbox_unread={unread}")
        lines.append("[/context]")
        return "\n".join(lines) + "\n\n"

    _NOTIF_SLUG_RE = re.compile(r"[^a-z0-9]+")

    def _slugify_for_filename(text: str, *, max_len: int = 40) -> str:
        slug = _NOTIF_SLUG_RE.sub("-", text.lower()).strip("-")
        return slug[:max_len] or "msg"

    def _write_notification_file(msg: dict) -> Path | None:
        """Drop a lazy-channel message as a markdown file in notifications/inbox/."""
        try:
            inbox = config.notifications_inbox_dir
            inbox.mkdir(parents=True, exist_ok=True)
            t = msg.get("t")
            if isinstance(t, datetime):
                ts_iso = t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                ts_human = t.astimezone(timezone.utc).isoformat()
            else:
                now = datetime.now(timezone.utc)
                ts_iso = now.strftime("%Y%m%dT%H%M%SZ")
                ts_human = now.isoformat()
            channel = str(msg.get("channel") or "unknown")
            text = str(msg.get("text") or "").strip()
            from_name = str(msg.get("from_name") or "")
            summary = _slugify_for_filename(text.splitlines()[0] if text else channel)
            fname = f"{ts_iso}_{channel}_{summary}.md"
            path = inbox / fname
            header = f"# {channel}"
            if from_name:
                header += f" / {from_name}"
            header += f"\n\nt: {ts_human}\nchannel: {channel}\n"
            if from_name:
                header += f"from: {from_name}\n"
            path.write_text(f"{header}\n---\n\n{text}\n", encoding="utf-8")
            return path
        except Exception as e:
            _plog.error("write notification file failed: %s", e)
            return None

    def _count_notifications_inbox() -> int:
        try:
            inbox = config.notifications_inbox_dir
            if not inbox.is_dir():
                return 0
            return sum(1 for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
        except Exception:
            return 0

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
            entry_mtime = float(entry.get("mtime", 0.0))
            if jf_mtime < entry_mtime:
                entry["byte_offset"] = 0
            elif jf_mtime <= entry_mtime:
                continue

            results, new_offset = _conductor.receive_cc(jf, entry["byte_offset"])
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
            "time": _project_time("%H:%M:%S"),
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
            ai_state = _load_ai_state(config)
            ai_state_name = str(ai_state.get("state", "notify"))
            session = _load_active_session(config)

            state = {
                "pid": os.getpid(),
                "uptime": time.strftime("%H:%M:%S"),
                "active": active,
                "ai_state": ai_state_name,
                "comm_state": ai_state_name,
                "ai_state_until": ai_state.get("until") or ai_state.get("expires_at") or None,
                "session": session.get("session_id", "")[:8] if session else None,
                "conductor_beat_buf": len(_conductor._beat_buf),
                "pool_events": len(_pool.load_events()),
                "last_activity": _project_time("%H:%M:%S", last_activity) if last_activity else None,
                "recent_actions": state_log[-20:],
            }
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # non-critical

    _seed_jsonl_cursor_to_present()
    _log_action("start", "daemon initialized")
    _write_daemon_state()

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

        # ── Inbound channels: drain MQTT queue (no polling here) ──
        # Bridges (bridge_email, dashboard /api/capture, ...) push
        # messages onto fiam/receive/+; the bus thread enqueues them.
        all_msgs: list[dict] = []
        while True:
            try:
                all_msgs.append(_inbox_q.get_nowait())
            except _queue.Empty:
                break
        if all_msgs:
            # Sort by msg timestamp so flow.jsonl reflects real-world order
            all_msgs.sort(key=lambda m: m.get("t") or datetime.min.replace(tzinfo=timezone.utc))
            source_counts: dict[str, int] = {}
            for msg in all_msgs:
                channel = str(msg.get("channel") or "unknown")
                source_counts[channel] = source_counts.get(channel, 0) + 1
            source_summary = " ".join(f"{ch}={count}" for ch, count in sorted(source_counts.items()))
            try:
                ts = _project_time("%H:%M")
                _console.print(
                    f"  [dim]└[{ts}][/dim] [bold #7eb8f7]✉[/]  bus +{len(all_msgs)} "
                    f"({source_summary})"
                )
                _plog.info("bus  total=+%d %s", len(all_msgs), source_summary)
                _log_action("bus", f"+{len(all_msgs)}")

                ai_state = _load_ai_state(config)
                ai_state_name = str(ai_state.get("state", "notify"))
                beat_ai_state = "online" if ai_state_name == "notify" else ai_state_name
                if beat_ai_state in _AI_STATES:
                    _conductor.set_status(ai=beat_ai_state)

                # All messages → Conductor → flow.jsonl + frozen vectors.
                # In auto mode it also runs drift/gorge; in manual mode it stops there.
                for msg in all_msgs:
                    try:
                        _conductor.receive(
                            msg["text"],
                            msg["channel"],
                            t=msg.get("t"),
                            meta=msg.get("meta") or {},
                        )
                    except Exception as e:
                        _plog.error("conductor.receive failed: %s", e)

                # ── Split by plugin delivery: lazy channels (email/ring/xiao)
                # only land in flow.jsonl + notifications/inbox/ and wait for AI
                # to peek; instant channels follow the wake path. ──
                from fiam.plugins import delivery_for_channel
                immediate_msgs: list[dict] = []
                lazy_msgs: list[dict] = []
                for msg in all_msgs:
                    ch = str(msg.get("channel") or "unknown")
                    if delivery_for_channel(config, ch, default="instant") == "instant":
                        immediate_msgs.append(msg)
                    else:
                        lazy_msgs.append(msg)

                if lazy_msgs:
                    written = 0
                    for msg in lazy_msgs:
                        if _write_notification_file(msg) is not None:
                            written += 1
                    if written:
                        ts_lz = _project_time("%H:%M")
                        _console.print(
                            f"  [dim]└[{ts_lz}][/dim] [dim #a08f7f]📬[/]  inbox +{written} (lazy)"
                        )
                        _plog.info("lazy notifications written: %d", written)

                if not immediate_msgs:
                    # All messages were lazy — done; no wake, no pending.
                    pass
                else:
                    # Recompute counts/summary for immediate flow.
                    source_counts = {}
                    for msg in immediate_msgs:
                        src = str(msg.get("source") or "unknown")
                        source_counts[src] = source_counts.get(src, 0) + 1

                    # Daemon decides CC delivery: ai_state > interactive > budget
                    ai_state = _load_ai_state(config)
                    ai_state_name = str(ai_state.get("state", "notify"))
                    sleeping = ai_state_name == "sleep"
                    sleep_until = str(ai_state.get("until", "open")) if sleeping else None
                    _plog.debug("ai_state=%s until=%s", ai_state_name, sleep_until)

                    if sleeping and sleep_until != "open":
                        # Explicit sleep: queue, don't wake
                        _plog.info("AI sleeping until %s — queuing inbox", sleep_until)
                        _console.print(f"  [dim]💤 sleeping — queued for next wake[/dim]")
                        _write_pending_external(config, immediate_msgs)
                    elif ai_state_name == "block":
                        _plog.info("ai_state=block — in flow, delivery discarded")
                        _console.print(f"  [dim]ai_state: block — recorded, no delivery[/dim]")
                    elif ai_state_name in {"mute", "busy"}:
                        _plog.info("ai_state=%s — in flow, no wake", ai_state_name)
                        _console.print(f"  [dim]ai_state: {ai_state_name} — queued for later[/dim]")
                        _write_pending_external(config, immediate_msgs)
                    else:
                        # Open sleep is auto-cleared by external arrival
                        prior_sleep_state = dict(ai_state) if sleeping else None
                        if sleeping and sleep_until == "open":
                            _plog.info("AI open-sleep — external msg auto-wakes")
                            _clear_sleep_state(config)
                        # notify (default) — deliver to CC
                        interactive = _is_interactive(config)
                        _plog.debug("interactive=%s", interactive)
                        if not interactive:
                            budget_ok, budget_reason = check_budget(config)
                            if not budget_ok:
                                _plog.warning("budget exceeded — queuing: %s", budget_reason)
                                _console.print(f"  [yellow]budget: {budget_reason}[/]")
                                _write_pending_external(config, immediate_msgs)
                            else:
                                sources_label = ",".join(sorted(source_counts)) or "unknown"
                                wake_prefix = _build_wake_context(
                                    prior_sleep_state,
                                    f"external:{sources_label}",
                                )
                                user_msg = _format_user_message(immediate_msgs, prefix=wake_prefix)
                                tag = next(iter(source_counts)) if len(source_counts) == 1 else "inbox"
                                _plog.info("wake attempt  tag=%s msgs=%d", tag, len(immediate_msgs))
                                ok = _wake_session(config, user_msg, tag=tag, conductor=_conductor)
                                if ok:
                                    ts2 = _project_time("%H:%M")
                                    _console.print(f"  [dim]└[{ts2}][/dim] [bold #a8f0e8]↗[/]  wake sent")
                                    _plog.info("wake OK")
                                else:
                                    _plog.warning("wake FAILED, retrying...")
                                    ok2 = _wake_session(config, user_msg, tag=tag, conductor=_conductor)
                                    if not ok2:
                                        _console.print(f"  [yellow]wake failed twice — messages queued[/]")
                                        _plog.error("wake FAILED x2 — messages queued in pending")
                                        _write_pending_external(config, immediate_msgs)
                                        session = _load_active_session(config)
                                        if session:
                                            _retire_session(config, reason="wake_failed")
                        else:
                            _console.print(f"  [dim]interactive — messages queued for hook[/dim]")
                            _plog.info("interactive — queuing for hook delivery")
                            _write_pending_external(config, immediate_msgs)
            except Exception as e:
                _plog.error("inbox handling error: %s", e, exc_info=True)
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

        # ── Todo: check for due delayed work ──
        try:
            from fiam_lib.todo import archive_stale, mark_done
            missed, failed = archive_stale(config)
            if missed or failed:
                _plog.info("todo archived  missed=%d failed=%d", missed, failed)

            due = load_due(config)
            for entry in due:
                # Schema: kind="wake" (no description), kind="todo" (with reason text),
                # or kind="sleep" (deferred sleep transition).
                kind = str(entry.get("kind") or "todo").lower()
                if kind not in {"wake", "todo", "sleep"}:
                    kind = "todo"
                reason = entry.get("reason", "") if kind == "todo" else ""
                display = reason if kind == "todo" else ("scheduled wake" if kind == "wake" else "scheduled sleep")
                _plog.info("todo fire  kind=%s reason=%s", kind, reason)

                if kind == "sleep":
                    _save_sleep_state(config, "open", "")
                    _plog.info("AI sleep  scheduled sleep fired (open)")
                    _console.print("  [dim]└ 💤 sleep (scheduled)[/dim]")
                    mark_done(entry, config, success=True)
                    continue

                # Budget check before delayed work.
                # Defer (don't drop) so the todo retries once quota refreshes.
                budget_ok, budget_reason = check_budget(config)
                if not budget_ok:
                    _plog.warning("budget exceeded — deferring todo: %s", budget_reason)
                    _console.print(f"  [yellow]⏰ {display} — deferred ({budget_reason})[/]")
                    mark_done(entry, config, success=False)
                    continue

                _console.print(f"  [bold #e8c8ff]⏰[/] {kind}: {display}")
                # Sleep gate: if AI is sleeping past this wake's time, skip.
                # (mark_done so it doesn't loop; AI's own sleep takes precedence)
                sleeping, sleep_until = _is_sleeping(config)
                prior_sleep_state = None
                if sleeping:
                    if sleep_until == "open":
                        _plog.info("AI open-sleep — todo clears it")
                        prior_sleep_state = dict(_load_ai_state(config))
                        _clear_sleep_state(config)
                    else:
                        _plog.info("AI sleeping until %s — skipping todo", sleep_until)
                        _console.print(f"  [dim]💤 still sleeping — todo skipped[/dim]")
                        mark_done(entry, config, success=True)
                        continue
                action = str(entry.get("action") or "")
                if action == "hold_retry":
                    trigger_label = f"hold_retry:{reason[:40]}" if reason else "hold_retry"
                    body = f"[hold_retry] {reason or 'reconsider held output'}"
                    todo_prefix = _build_wake_context(prior_sleep_state, trigger_label)
                    ok = _wake_session(
                        config,
                        f"{todo_prefix}{body}",
                        tag="hold_retry",
                        conductor=_conductor,
                    )
                else:
                    trigger_label = "scheduled" if kind == "wake" else f"todo:{reason[:40]}"
                    body = "[scheduled wake]" if kind == "wake" else f"[todo] {reason}"
                    todo_prefix = _build_wake_context(prior_sleep_state, trigger_label)
                    ok = _wake_session(
                        config,
                        f"{todo_prefix}{body}",
                        tag=kind,
                        conductor=_conductor,
                    )
                mark_done(entry, config, success=ok)
                if ok:
                    _plog.info("todo item OK")
                else:
                    attempts = int(entry.get("attempts", 0)) + 1
                    _plog.warning("todo item FAILED  retry=%d", attempts)
        except Exception as e:
            _plog.error("todo error: %s", e, exc_info=True)

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
                ts = _project_time("%H:%M")
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
            ts = _project_time("%H:%M")
            _console.print(f"  [dim]└[{ts}][/dim] [bold #f7a8d0]⟳[/]  processing...")
            _plog.info("idle timeout → processing + auto-retire")
            _process_pending()
            # Auto-retire: long inactivity = AI naturally drifted to sleep without
            # declaring it. Next message starts a fresh session.
            if _load_active_session(config):
                _retire_session(config, reason="idle")
                _plog.info("session auto-retired (idle)")

        _write_daemon_state()

    # ── Graceful shutdown: process any pending content before exit ──
    if shutdown_requested and process_pending_on_shutdown and active:
        _console.print()
        _console.print(f"  [bold #f7a8d0]⟳[/]  wrapping up...")
        _process_pending()

    # Stop bus loop (drains queued QoS 1 messages)
    try:
        _bus.loop_stop()
    except Exception:
        pass

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
        os.kill(pid, signal.SIGINT)

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
        from fiam.store.pool import Pool
        config = FiamConfig.from_toml(toml, code_path)
        print(f"  home: {config.home_path}")
        print(f"  memory mode: {config.memory_mode}")

        pool = Pool(config.pool_dir, dim=config.embedding_dim)
        print(f"  events: {pool.event_count}")

        try:
            from fiam.store.features import FeatureStore
            count = FeatureStore(config.feature_dir, dim=config.embedding_dim).count()
        except Exception:
            count = 0
        print(f"  beat vectors: {count}")

        cursor = _load_cursor(code_path)
        if cursor:
            latest_mtime = max(v.get("mtime", 0) for v in cursor.values())
            if latest_mtime > 0:
                from datetime import datetime
                dt = datetime.fromtimestamp(latest_mtime, timezone.utc).astimezone(config.project_tz()).strftime("%Y-%m-%d %H:%M")
                print(f"  last processed: {dt}")
    else:
        print("  (no fiam.toml — run 'fiam init')")
