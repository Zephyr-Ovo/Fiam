"""
Self-scheduling daemon — AI decides when to wake itself up.

The AI writes a WAKE tag anywhere in conversation output:

    <<WAKE:2026-04-10T21:00:00-07:00:private:晚间反思与日记>>
    <<WAKE:2026-04-11T08:00:00-07:00:notify:早上检查消息>>

Format: <<WAKE:ISO_TIMESTAMP:TYPE:REASON>>
  TYPE: "private" (no push to user) | "notify" (push output to user after)

This module:
  1. Extracts WAKE tags from post-session text (called by pipeline)
  2. Appends them to home/self/schedule.jsonl
  3. Runs a polling loop that triggers CC sessions at scheduled times
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fiam.config import FiamConfig

# Regex for <<WAKE:ISO:TYPE:REASON>>
WAKE_RE = re.compile(
    r"<<WAKE:"
    r"(?P<time>.+?)"                           # ISO timestamp (non-greedy)
    r":(?P<type>private|notify|seek|check)"    # type
    r":(?P<reason>[^>]+)"                      # reason text
    r">>",
    re.IGNORECASE,
)

# Regex for <<SLEEP:UNTIL:REASON>>
#   UNTIL: ISO datetime, or literal "open" (= sleep until external event)
SLEEP_RE = re.compile(
    r"<<SLEEP:"
    r"(?P<until>open|[^:]+?)"
    r":(?P<reason>[^>]+)"
    r">>",
    re.IGNORECASE,
)


def extract_sleep_tag(text: str) -> dict | None:
    """Extract the LAST <<SLEEP:until:reason>> tag from AI text.

    Returns {sleeping_until, reason} where sleeping_until is ISO str or "open".
    Last-wins so AI can revise during a turn.
    """
    last = None
    for m in SLEEP_RE.finditer(text):
        until_raw = m.group("until").strip()
        reason = m.group("reason").strip()
        if until_raw.lower() == "open":
            until = "open"
        else:
            try:
                dt = datetime.fromisoformat(until_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                until = dt.isoformat()
            except ValueError:
                continue
        last = {"sleeping_until": until, "reason": reason}
    return last


# ------------------------------------------------------------------
# Extraction (called after post_session)
# ------------------------------------------------------------------

def extract_wake_tags(text: str) -> list[dict]:
    """Extract all WAKE tags from AI output text."""
    results = []
    for m in WAKE_RE.finditer(text):
        try:
            wake_at = datetime.fromisoformat(m.group("time"))
            results.append({
                "wake_at": wake_at.isoformat(),
                "type": m.group("type"),
                "reason": m.group("reason").strip(),
                "created": datetime.now(timezone.utc).isoformat(),
            })
        except ValueError:
            continue
    return results


def append_to_schedule(tags: list[dict], config: FiamConfig) -> int:
    """Append extracted WAKE tags to schedule.jsonl. Returns count added.

    Skips expired entries and deduplicates against existing schedule.
    """
    if not tags:
        return 0
    now = datetime.now(timezone.utc)
    schedule_path = config.schedule_path
    schedule_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing entries for dedup (wake_at + reason) and quota check (wake_at list)
    existing: set[tuple[str, str]] = set()
    existing_times: list[datetime] = []
    if schedule_path.exists():
        for line in schedule_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                existing.add((entry.get("wake_at", ""), entry.get("reason", "")))
                t = datetime.fromisoformat(entry["wake_at"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if t > now:
                    existing_times.append(t)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    # Quota: in any rolling 5h window, ≤7 scheduled wakes (CC token window).
    QUOTA_MAX = 7
    QUOTA_WINDOW = timedelta(hours=5)

    count = 0
    skipped_quota = 0
    with open(schedule_path, "a", encoding="utf-8") as f:
        for tag in tags:
            try:
                wake_at = datetime.fromisoformat(tag["wake_at"])
                if wake_at.tzinfo is None:
                    wake_at = wake_at.replace(tzinfo=timezone.utc)
                if wake_at <= now:
                    continue  # skip already-expired entries
            except (KeyError, ValueError):
                continue
            # Dedup: skip if same wake_at + reason already scheduled
            key = (tag.get("wake_at", ""), tag.get("reason", ""))
            if key in existing:
                continue
            # Quota check: count existing wakes within ±2.5h of this one.
            overlapping = sum(
                1 for t in existing_times
                if abs((t - wake_at).total_seconds()) <= QUOTA_WINDOW.total_seconds() / 2
            )
            if overlapping >= QUOTA_MAX:
                skipped_quota += 1
                continue
            f.write(json.dumps(tag, ensure_ascii=False) + "\n")
            existing.add(key)
            existing_times.append(wake_at)
            count += 1
    if skipped_quota:
        print(f"[scheduler] skipped {skipped_quota} wake(s): 5h window quota ({QUOTA_MAX}) reached")
    return count


# ------------------------------------------------------------------
# Queue management
# ------------------------------------------------------------------

def load_pending(config: FiamConfig) -> list[dict]:
    """Load all future schedule entries, sorted by time."""
    path = config.schedule_path
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
            wake_at = datetime.fromisoformat(entry["wake_at"])
            # Normalize to UTC for comparison
            if wake_at.tzinfo is None:
                wake_at = wake_at.replace(tzinfo=timezone.utc)
            if wake_at > now:
                entry["_wake_utc"] = wake_at
                pending.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    pending.sort(key=lambda e: e["_wake_utc"])
    return pending


def load_due(config: FiamConfig) -> list[dict]:
    """Load schedule entries whose wake_at has passed (due to fire).

    Skips entries already attempted too many times or past the grace window
    (those are archived separately by :func:`archive_stale`).
    """
    path = config.schedule_path
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
            wake_at = datetime.fromisoformat(entry["wake_at"])
            if wake_at.tzinfo is None:
                wake_at = wake_at.replace(tzinfo=timezone.utc)
            if wake_at > now:
                continue
            # Skip if past the grace window — those get archived, not fired.
            if now - wake_at > grace:
                continue
            # Skip if too many failed attempts — also archived.
            if int(entry.get("attempts", 0)) >= MAX_ATTEMPTS:
                continue
            entry["_wake_utc"] = wake_at
            due.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return due


# ------------------------------------------------------------------
# Fault-tolerance constants
# ------------------------------------------------------------------

# If a wake is overdue by more than this, don't fire it — archive as missed.
# Covers daemon downtime / ISP reboots.
MISSED_GRACE_HOURS = 2

# After this many failed/deferred attempts, give up and archive to failed queue.
MAX_ATTEMPTS = 3

# Exponential backoff between retries (seconds): 5min, 20min, 80min.
RETRY_BACKOFF = [5 * 60, 20 * 60, 80 * 60]


def _archive_path(config: FiamConfig, kind: str) -> Path:
    """Path to schedule_missed.jsonl or schedule_failed.jsonl."""
    return config.self_dir / f"schedule_{kind}.jsonl"


def _append_archive(entry: dict, config: FiamConfig, kind: str, note: str = "") -> None:
    """Append an entry to the archive (missed/failed) for later review."""
    entry = {k: v for k, v in entry.items() if not k.startswith("_")}
    entry["archived_at"] = datetime.now(timezone.utc).isoformat()
    entry["archive_reason"] = note
    path = _archive_path(config, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archive_stale(config: FiamConfig) -> tuple[int, int]:
    """Archive entries that are past grace window or past max attempts.

    Returns (missed_count, failed_count). Called before :func:`load_due`.
    """
    path = config.schedule_path
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
            wake_at = datetime.fromisoformat(entry["wake_at"])
            if wake_at.tzinfo is None:
                wake_at = wake_at.replace(tzinfo=timezone.utc)
            attempts = int(entry.get("attempts", 0))
            if attempts >= MAX_ATTEMPTS:
                _append_archive(entry, config, "failed",
                                note=f"exceeded {MAX_ATTEMPTS} attempts")
                failed += 1
                continue
            if wake_at <= now and (now - wake_at) > grace:
                _append_archive(entry, config, "missed",
                                note=f"past grace window ({MISSED_GRACE_HOURS}h)")
                missed += 1
                continue
            keep.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            # Malformed line — drop silently to avoid blocking the queue.
            continue
    if missed or failed:
        _atomic_write_jsonl(path, keep)
    return missed, failed


def _atomic_write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write JSONL atomically (write to .tmp, fsync, rename)."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for entry in entries:
            clean = {k: v for k, v in entry.items() if not k.startswith("_")}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # e.g. on non-POSIX FS; best-effort only
    tmp.replace(path)  # atomic on same filesystem


def mark_fired(entry: dict, config: FiamConfig, success: bool) -> None:
    """After triggering a wake, update schedule.jsonl in place.

    - success=True: remove the entry entirely.
    - success=False: increment attempts, bump wake_at by backoff. If max
      attempts reached, next :func:`archive_stale` call moves it to failed.
    """
    path = config.schedule_path
    if not path.exists():
        return
    target_key = (entry.get("wake_at", ""), entry.get("reason", ""))
    now = datetime.now(timezone.utc)
    new_entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (e.get("wake_at", ""), e.get("reason", ""))
        if key != target_key:
            new_entries.append(e)
            continue
        if success:
            continue  # drop the successful entry
        # Defer: increment attempts, push wake_at to now + backoff.
        attempts = int(e.get("attempts", 0)) + 1
        backoff = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
        e["attempts"] = attempts
        e["last_attempt_at"] = now.isoformat()
        e["wake_at"] = (now + timedelta(seconds=backoff)).isoformat()
        new_entries.append(e)
    _atomic_write_jsonl(path, new_entries)


def queue_summary(config: FiamConfig) -> str:
    """Return a short summary for awareness injection."""
    pending = load_pending(config)
    if not pending:
        return "调度队列: 空（无计划唤醒）"
    lines = [f"调度队列: {len(pending)} 条"]
    for entry in pending[:5]:  # Show at most 5
        t = entry.get("wake_at", "?")
        r = entry.get("reason", "?")
        lines.append(f"  - {t}: {r}")
    if len(pending) > 5:
        lines.append(f"  ...及 {len(pending) - 5} 条更多")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Schedule maintenance
# ------------------------------------------------------------------

def _rewrite_schedule(config: FiamConfig) -> None:
    """Atomically compact schedule.jsonl (drop only fired/expired entries).

    Kept for backwards compatibility with callers that don't use
    :func:`mark_fired` per-entry. Uses :func:`_atomic_write_jsonl`.
    """
    pending = load_pending(config)
    _atomic_write_jsonl(config.schedule_path, pending)

