"""Todo queue for delayed AI work."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fiam.config import FiamConfig
from fiam.markers import (
    parse_sleep_markers,
    parse_state_markers,
    parse_todo_markers,
    parse_wake_markers,
)


def _ensure_config_timezone(dt: datetime, config: FiamConfig | None) -> datetime:
    if config is not None:
        return config.ensure_timezone(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def extract_state_tag(text: str, config: FiamConfig | None = None) -> dict | None:
    """Extract the last ``<state .../>`` marker from AI text."""
    markers = parse_state_markers(text)
    if not markers:
        return None
    marker = markers[-1]
    result = {"state": marker.state, "reason": marker.reason}
    if marker.until:
        try:
            parsed = datetime.fromisoformat(marker.until.replace("Z", "+00:00"))
            parsed = _ensure_config_timezone(parsed, config)
            result["until"] = parsed.isoformat()
        except ValueError:
            return None
    return result


def extract_scheduled_items(text: str, config: FiamConfig | None = None) -> list[dict]:
    """Extract scheduled wake/todo XML markers from AI output text.

    Returns a list of ``{at, kind, reason, created}`` dicts where:
      * ``kind`` is ``"wake"`` for ``<wake>TIME</wake>`` (no description), or
        ``"todo"`` for ``<todo at="TIME">description</todo>``.
      * ``reason`` is the todo body for ``"todo"`` and ``""`` for ``"wake"``.
    """
    default_tz = config.project_tz() if config is not None else None
    results: list[dict] = []
    for marker in parse_wake_markers(text, default_tz=default_tz):
        try:
            at = datetime.fromisoformat(marker.at)
            at = _ensure_config_timezone(at, config)
            results.append({
                "at": at.isoformat(),
                "kind": "wake",
                "reason": "",
                "created": datetime.now(timezone.utc).isoformat(),
            })
        except ValueError:
            continue
    for marker in parse_todo_markers(text, default_tz=default_tz):
        try:
            at = datetime.fromisoformat(marker.at)
            at = _ensure_config_timezone(at, config)
            results.append({
                "at": at.isoformat(),
                "kind": "todo",
                "reason": marker.text,
                "created": datetime.now(timezone.utc).isoformat(),
            })
        except ValueError:
            continue
    for marker in parse_sleep_markers(text, default_tz=default_tz):
        try:
            at = datetime.fromisoformat(marker.at)
            at = _ensure_config_timezone(at, config)
            results.append({
                "at": at.isoformat(),
                "kind": "sleep",
                "reason": "",
                "created": datetime.now(timezone.utc).isoformat(),
            })
        except ValueError:
            continue
    return results


def append_to_todo(tags: list[dict], config: FiamConfig) -> int:
    """Append extracted delayed-work entries to todo.jsonl. Returns count added."""
    if not tags:
        return 0
    now = datetime.now(timezone.utc)
    todo_path = config.todo_path
    todo_path.parent.mkdir(parents=True, exist_ok=True)

    existing: set[tuple[str, str]] = set()
    existing_times: list[datetime] = []
    if todo_path.exists():
        for line in todo_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                existing.add((entry.get("at", ""), entry.get("reason", "")))
                at = datetime.fromisoformat(entry["at"])
                at = config.ensure_timezone(at)
                if at > now:
                    existing_times.append(at)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    quota_max = 7
    quota_window = timedelta(hours=5)

    count = 0
    skipped_quota = 0
    with open(todo_path, "a", encoding="utf-8") as file:
        for tag in tags:
            try:
                at = datetime.fromisoformat(tag["at"])
                at = config.ensure_timezone(at)
                if at <= now:
                    continue
            except (KeyError, ValueError):
                continue
            key = (tag.get("at", ""), tag.get("reason", ""))
            if key in existing:
                continue
            overlapping = sum(
                1 for existing_time in existing_times
                if abs((existing_time - at).total_seconds()) <= quota_window.total_seconds() / 2
            )
            if overlapping >= quota_max:
                skipped_quota += 1
                continue
            file.write(json.dumps(tag, ensure_ascii=False) + "\n")
            existing.add(key)
            existing_times.append(at)
            count += 1
    if skipped_quota:
        print(f"[todo] skipped {skipped_quota} item(s): 5h window quota ({quota_max}) reached")
    return count


def load_pending(config: FiamConfig) -> list[dict]:
    """Load all future todo entries, sorted by time."""
    path = config.todo_path
    if not path.exists():
        return []

    now = datetime.now(timezone.utc)
    pending = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            at = datetime.fromisoformat(entry["at"])
            at = config.ensure_timezone(at)
            if at > now:
                entry["_todo_utc"] = at
                pending.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    pending.sort(key=lambda entry: entry["_todo_utc"])
    return pending


def load_due(config: FiamConfig) -> list[dict]:
    """Load todo entries whose time has passed."""
    path = config.todo_path
    if not path.exists():
        return []

    now = datetime.now(timezone.utc)
    grace = timedelta(hours=MISSED_GRACE_HOURS)
    due = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            at = datetime.fromisoformat(entry["at"])
            at = config.ensure_timezone(at)
            if at > now:
                continue
            if now - at > grace:
                continue
            if int(entry.get("attempts", 0)) >= MAX_ATTEMPTS:
                continue
            entry["_todo_utc"] = at
            due.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return due


MISSED_GRACE_HOURS = 2
MAX_ATTEMPTS = 3
RETRY_BACKOFF = [5 * 60, 20 * 60, 80 * 60]


def _archive_path(config: FiamConfig, kind: str) -> Path:
    return config.self_dir / f"todo_{kind}.jsonl"


def _append_archive(entry: dict, config: FiamConfig, kind: str, note: str = "") -> None:
    entry = {key: value for key, value in entry.items() if not key.startswith("_")}
    entry["archived_at"] = datetime.now(timezone.utc).isoformat()
    entry["archive_reason"] = note
    path = _archive_path(config, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archive_stale(config: FiamConfig) -> tuple[int, int]:
    """Archive entries past grace window or max attempts."""
    path = config.todo_path
    if not path.exists():
        return 0, 0
    now = datetime.now(timezone.utc)
    grace = timedelta(hours=MISSED_GRACE_HOURS)
    keep: list[dict] = []
    missed = 0
    failed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            at = datetime.fromisoformat(entry["at"])
            at = config.ensure_timezone(at)
            attempts = int(entry.get("attempts", 0))
            if attempts >= MAX_ATTEMPTS:
                _append_archive(entry, config, "failed", note=f"exceeded {MAX_ATTEMPTS} attempts")
                failed += 1
                continue
            if at <= now and (now - at) > grace:
                _append_archive(entry, config, "missed", note=f"past grace window ({MISSED_GRACE_HOURS}h)")
                missed += 1
                continue
            keep.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    if missed or failed:
        _atomic_write_jsonl(path, keep)
    return missed, failed


def _atomic_write_jsonl(path: Path, entries: list[dict]) -> None:
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        for entry in entries:
            clean = {key: value for key, value in entry.items() if not key.startswith("_")}
            file.write(json.dumps(clean, ensure_ascii=False) + "\n")
        file.flush()
        try:
            os.fsync(file.fileno())
        except OSError:
            pass
    tmp.replace(path)


def mark_done(entry: dict, config: FiamConfig, success: bool) -> None:
    """Update todo.jsonl after an item has run."""
    path = config.todo_path
    if not path.exists():
        return
    target_key = (entry.get("at", ""), entry.get("reason", ""))
    now = datetime.now(timezone.utc)
    new_entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            current = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (current.get("at", ""), current.get("reason", ""))
        if key != target_key:
            new_entries.append(current)
            continue
        if success:
            continue
        attempts = int(current.get("attempts", 0)) + 1
        backoff = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
        current["attempts"] = attempts
        current["last_attempt_at"] = now.isoformat()
        current["at"] = (now + timedelta(seconds=backoff)).isoformat()
        new_entries.append(current)
    _atomic_write_jsonl(path, new_entries)


def queue_summary(config: FiamConfig) -> str:
    """Return a short summary for awareness injection."""
    pending = load_pending(config)
    if not pending:
        return "todo 队列: 空（无稍后任务）"
    lines = [f"todo 队列: {len(pending)} 条"]
    for entry in pending[:5]:
        at = entry.get("at", "?")
        reason = entry.get("reason", "?")
        lines.append(f"  - {at}: {reason}")
    if len(pending) > 5:
        lines.append(f"  ...及 {len(pending) - 5} 条更多")
    return "\n".join(lines)


def _rewrite_todo(config: FiamConfig) -> None:
    """Atomically compact todo.jsonl."""
    pending = load_pending(config)
    _atomic_write_jsonl(config.todo_path, pending)
