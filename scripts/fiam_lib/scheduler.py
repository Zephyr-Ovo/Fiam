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
import subprocess
import sys
import time
from datetime import datetime, timezone
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
    """Append extracted WAKE tags to schedule.jsonl. Returns count added."""
    if not tags:
        return 0
    schedule_path = config.schedule_path
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    with open(schedule_path, "a", encoding="utf-8") as f:
        for tag in tags:
            f.write(json.dumps(tag, ensure_ascii=False) + "\n")
    return len(tags)


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
    """Load schedule entries whose wake_at has passed (due to fire)."""
    path = config.schedule_path
    if not path.exists():
        return []

    now = datetime.now(timezone.utc)
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
            if wake_at <= now:
                entry["_wake_utc"] = wake_at
                due.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return due


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
# Trigger logic
# ------------------------------------------------------------------

def _trigger_session(entry: dict, config: FiamConfig) -> None:
    """Launch a CC session with the scheduled note as context."""
    reason = entry.get("reason", "scheduled wake")
    wake_type = entry.get("type", "private")

    # Write the wake note to a temp file that the hook will pick up
    note_path = config.home_path / ".wake_note"

    type_labels = {
        "private": "私人任务（静默，不推送用户）",
        "notify": "通知任务（完成后推送用户）",
        "seek": "寻找用户（尝试联系）",
        "check": "环境检查（静默观察）",
    }

    note_path.write_text(
        f"[自主调度唤醒] {reason}\n"
        f"类型: {type_labels.get(wake_type, wake_type)}\n"
        f"计划时间: {entry.get('wake_at', '?')}\n"
        f"\n"
        f"{'这是你自己的时间，产出写入 journal/ 或 self/，不推送用户。' if wake_type == 'private' else ''}"
        f"{'完成后将结果推送给用户（TG/email）。' if wake_type == 'notify' else ''}"
        f"{'你想找 Zephyr。先发 TG，如果无回复可以 SSH 到 Local 查 AW 了解情况。' if wake_type == 'seek' else ''}"
        f"{'静默检查环境状态：inbox、AW、tunnel、schedule。不推送不打扰。' if wake_type == 'check' else ''}"
        ,
        encoding="utf-8",
    )

    # Build type-appropriate prompt
    prompts = {
        "private": f"你之前安排了这次私人活动: {reason}。这是你自己的时间，不需要通知任何人。",
        "notify":  f"你之前安排了这次任务: {reason}。完成后把结果发送给 Zephyr。",
        "seek":    f"你之前安排了找 Zephyr: {reason}。先尝试 TG 联系，必要时 SSH 到 Local 查看。",
        "check":   f"你安排了一次环境检查: {reason}。静默查看状态，不打扰用户。",
    }
    prompt = prompts.get(wake_type, f"你之前安排了这次活动: {reason}。")

    try:
        subprocess.run(
            ["claude", "-p", prompt, "--no-input"],
            cwd=str(config.home_path),
            timeout=300,  # 5 minute max per scheduled session
        )
    except FileNotFoundError:
        print("[scheduler] claude command not found")
    except subprocess.TimeoutExpired:
        print("[scheduler] Session timed out (5min)")
    finally:
        note_path.unlink(missing_ok=True)


def _rewrite_schedule(config: FiamConfig) -> None:
    """Remove past entries from schedule.jsonl (compact)."""
    pending = load_pending(config)
    path = config.schedule_path
    with open(path, "w", encoding="utf-8") as f:
        for entry in pending:
            entry.pop("_wake_utc", None)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------
# Daemon loop
# ------------------------------------------------------------------

def run_scheduler_loop(config: FiamConfig, poll_seconds: int = 30) -> None:
    """Poll schedule.jsonl and trigger sessions when wake times arrive."""
    print(f"[scheduler] Watching {config.schedule_path} (poll={poll_seconds}s)")
    while True:
        try:
            now = datetime.now(timezone.utc)
            pending = load_pending(config)

            for entry in pending:
                wake_at = entry["_wake_utc"]
                if wake_at <= now:
                    reason = entry.get("reason", "?")
                    print(f"[scheduler] ⏰ Triggering: {reason}")
                    _trigger_session(entry, config)

            # Compact: remove spent entries
            _rewrite_schedule(config)

        except KeyboardInterrupt:
            print("[scheduler] Stopped.")
            break
        except Exception as e:
            print(f"[scheduler] Error: {e}")

        time.sleep(poll_seconds)
