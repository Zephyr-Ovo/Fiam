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
from fiam_lib.scheduler import extract_wake_tags, extract_sleep_tag, append_to_schedule, load_due
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
        "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if reason:
        data["reason"] = reason
    if until:
        data["until"] = until
    if expires_at:
        data["expires_at"] = expires_at
    _write_ai_state(config, data)
    # Clean up legacy split-state files once the unified state is written.
    config.sleep_state_path.unlink(missing_ok=True)
    (config.self_dir / "comm_state.json").unlink(missing_ok=True)


def _clear_ai_state(config) -> None:
    config.ai_state_path.unlink(missing_ok=True)
    config.sleep_state_path.unlink(missing_ok=True)
    (config.self_dir / "comm_state.json").unlink(missing_ok=True)


def _parse_state_time(raw: str):
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _migrate_legacy_ai_state(config) -> dict | None:
    sleep_path = config.sleep_state_path
    if sleep_path.exists():
        try:
            data = json.loads(sleep_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        until = str(data.get("sleeping_until") or data.get("until") or "")
        reason = str(data.get("reason") or "")
        if until:
            migrated = {
                "state": "sleep",
                "until": until,
                "reason": reason,
                "since": str(data.get("since") or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            }
            _write_ai_state(config, migrated)
            sleep_path.unlink(missing_ok=True)
            return migrated

    comm_path = config.self_dir / "comm_state.json"
    if comm_path.exists():
        try:
            data = json.loads(comm_path.read_text(encoding="utf-8"))
            state = str(data.get("state", "notify"))
        except (json.JSONDecodeError, OSError):
            state = "notify"
        if state in _AI_STATES:
            migrated = {
                "state": state,
                "reason": str(data.get("reason") or ""),
                "since": str(data.get("since") or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            }
            _write_ai_state(config, migrated)
            comm_path.unlink(missing_ok=True)
            return migrated
    return None


def _load_ai_state(config) -> dict:
    """Load the unified AI state, auto-migrating legacy split files."""
    path = config.ai_state_path
    data: dict | None = None
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
    if data is None:
        data = _migrate_legacy_ai_state(config)
    if not data:
        return _default_ai_state()

    state = str(data.get("state", "notify"))
    if state not in _AI_STATES:
        _clear_ai_state(config)
        return _default_ai_state()

    expires_at = data.get("expires_at")
    if expires_at:
        dt = _parse_state_time(expires_at)
        if dt is None or datetime.now(timezone.utc) >= dt:
            _clear_ai_state(config)
            return _default_ai_state()

    if state == "sleep":
        until = str(data.get("until", ""))
        if until == "open":
            return data
        dt = _parse_state_time(until)
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


def _save_sleep_state(config, sleeping_until: str, reason: str) -> None:
    """Persist AI sleep state in the unified ai_state.json."""
    _save_ai_state(config, "sleep", until=sleeping_until, reason=reason)


def _clear_sleep_state(config) -> None:
    if _load_ai_state(config).get("state") == "sleep":
        _clear_ai_state(config)
    config.sleep_state_path.unlink(missing_ok=True)


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


def _wake_session(config, message: str, tag: str = "tg", conductor=None) -> bool:
    """Send a message to Fiet via `claude -p --resume <id>`.

    If no active session exists, creates a new session and saves its ID.
    Returns True if the message was sent successfully.
    Dispatches outbound markers from the response via conductor.dispatch().
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

        # Extract outbound markers from response → dispatch via conductor
        if data:
            response_text = data.get("result", "")
            if response_text:
                _extract_and_dispatch(config, response_text, conductor)
                # Extract WAKE tags for scheduler
                wake_tags = extract_wake_tags(response_text)
                if wake_tags:
                    n = append_to_schedule(wake_tags, config)
                    _plog.info("scheduler  +%d wake(s) queued", n)
                    _console.print(f"  [dim]└ scheduler +{n} wake(s)[/dim]")
                # Extract SLEEP tag → persist + retire session
                sleep_tag = extract_sleep_tag(response_text)
                if sleep_tag:
                    _save_sleep_state(config, sleep_tag["sleeping_until"], sleep_tag["reason"])
                    _retire_session(config, reason="sleep")
                    until_label = sleep_tag["sleeping_until"]
                    if until_label != "open":
                        until_label = until_label[:16].replace("T", " ")
                    _plog.info("AI sleep  until=%s reason=%s", until_label, sleep_tag["reason"])
                    _console.print(f"  [dim]└ 💤 sleep until {until_label}[/dim]")

        return True
    except subprocess.TimeoutExpired:
        _console.print(f"  [red]wake timeout[/]")
        return False
    except FileNotFoundError:
        _console.print(f"  [red]claude not found[/]")
        return False


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

    def _on_receive(source: str, payload: dict) -> None:
        """Bus thread → main loop queue. Convert MQTT payload to msg dict."""
        text = (payload.get("text") or "").strip()
        if not text:
            return
        source_name = str(payload.get("source") or source)
        try:
            from fiam.plugins import is_receive_enabled
            if not is_receive_enabled(config, source_name):
                _plog.info("receive skipped disabled plugin source=%s", source_name)
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
            if key not in {"text", "source", "t"} and value not in (None, "", [])
        }
        _inbox_q.put({
            "source": source_name,
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
    # Regex to strip daemon wake signal tags from user text
    _wake_tag_re = re.compile(r"\[wake:[^\]]*\]\s*")

    def _write_pending_external(config, msgs: list[dict]) -> None:
        """Append pre-formatted external messages for inject.sh hook delivery."""
        parts = []
        for m in msgs:
            parts.append(f"[{m['source']}:{m['from_name']}] {m['text']}")
        formatted = "\n\n".join(parts)
        path = config.pending_external_path
        with open(path, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")

    def _format_user_message(msgs: list[dict]) -> str:
        """Format external messages for `claude -p` user field."""
        parts = []
        for m in msgs:
            parts.append(f"[{m['source']}:{m['from_name']}] {m['text']}")
        return "\n\n".join(parts)

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
                "last_activity": time.strftime("%H:%M:%S", time.localtime(last_activity)) if last_activity else None,
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
        # Bridges (bridge_tg, bridge_email, dashboard /api/capture, ...) push
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
            n_tg = sum(1 for m in all_msgs if m["source"] == "tg")
            n_email = sum(1 for m in all_msgs if m["source"] == "email")
            n_other = len(all_msgs) - n_tg - n_email
            try:
                ts = time.strftime("%H:%M")
                _console.print(
                    f"  [dim]└[{ts}][/dim] [bold #7eb8f7]✉[/]  bus +{len(all_msgs)} "
                    f"(tg={n_tg} email={n_email} other={n_other})"
                )
                _plog.info("bus  total=+%d tg=+%d email=+%d other=+%d",
                           len(all_msgs), n_tg, n_email, n_other)
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
                            msg["source"],
                            t=msg.get("t"),
                            meta=msg.get("meta") or {},
                        )
                    except Exception as e:
                        _plog.error("conductor.receive failed: %s", e)

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
                    _write_pending_external(config, all_msgs)
                elif ai_state_name == "block":
                    _plog.info("ai_state=block — in flow, delivery discarded")
                    _console.print(f"  [dim]ai_state: block — recorded, no delivery[/dim]")
                elif ai_state_name in {"mute", "busy"}:
                    _plog.info("ai_state=%s — in flow, no wake", ai_state_name)
                    _console.print(f"  [dim]ai_state: {ai_state_name} — queued for later[/dim]")
                    _write_pending_external(config, all_msgs)
                else:
                    # Open sleep is auto-cleared by external arrival
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
                            _write_pending_external(config, all_msgs)
                        else:
                            user_msg = _format_user_message(all_msgs)
                            tag = "tg" if n_tg and not n_email else ("email" if n_email and not n_tg else "inbox")
                            _plog.info("wake attempt  tag=%s msgs=%d", tag, len(all_msgs))
                            ok = _wake_session(config, user_msg, tag=tag, conductor=_conductor)
                            if ok:
                                ts2 = time.strftime("%H:%M")
                                _console.print(f"  [dim]└[{ts2}][/dim] [bold #a8f0e8]↗[/]  wake sent")
                                _plog.info("wake OK")
                            else:
                                _plog.warning("wake FAILED, retrying...")
                                ok2 = _wake_session(config, user_msg, tag=tag, conductor=_conductor)
                                if not ok2:
                                    _console.print(f"  [yellow]wake failed twice — messages queued[/]")
                                    _plog.error("wake FAILED x2 — messages queued in pending")
                                    _write_pending_external(config, all_msgs)
                                    session = _load_active_session(config)
                                    if session:
                                        _retire_session(config, reason="wake_failed")
                    else:
                        _console.print(f"  [dim]interactive — messages queued for hook[/dim]")
                        _plog.info("interactive — queuing for hook delivery")
                        _write_pending_external(config, all_msgs)
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
                # Sleep gate: if AI is sleeping past this wake's time, skip.
                # (mark_fired so it doesn't loop; AI's own sleep takes precedence)
                sleeping, sleep_until = _is_sleeping(config)
                if sleeping:
                    if sleep_until == "open":
                        _plog.info("AI open-sleep — scheduled wake clears it")
                        _clear_sleep_state(config)
                    else:
                        _plog.info("AI sleeping until %s — skipping scheduled wake", sleep_until)
                        _console.print(f"  [dim]💤 still sleeping — wake skipped[/dim]")
                        mark_fired(entry, config, success=True)
                        continue
                ok = _wake_session(config, f"[scheduled:{wake_type}] {reason}", tag="sched", conductor=_conductor)
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
                dt = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  last processed: {dt}")
    else:
        print("  (no fiam.toml — run 'fiam init')")
