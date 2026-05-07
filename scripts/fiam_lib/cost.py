"""Cost tracking and quota awareness for daemon wake cycles.

Tracks CC invocation costs in store/cost_log.jsonl. Provides:
  - Logging of per-wake costs
  - Daily spend summation
  - Budget checking (configurable daily limit)
  - Awareness injection (human-readable spend summary for context)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _cost_log_path(config: FiamConfig) -> Path:
    return config.store_dir / "cost_log.jsonl"


def log_cost(config: FiamConfig, cost_usd: float, session_id: str = "",
             tag: str = "", turns: int = 0) -> None:
    """Append a cost entry after each CC invocation."""
    path = _cost_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cost_usd": round(cost_usd, 6),
        "session": session_id[:12] if session_id else "",
        "tag": tag,
        "turns": turns,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def daily_spend(config: FiamConfig) -> float:
    """Sum cost_usd for entries from today in the project timezone."""
    path = _cost_log_path(config)
    if not path.exists():
        return 0.0
    today = config.now_local().date()
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if config.ensure_timezone(ts).astimezone(config.project_tz()).date() == today:
                total += entry.get("cost_usd", 0.0)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return total


def recent_spend(config: FiamConfig, hours: int = 1) -> float:
    """Sum cost_usd for the last N hours."""
    path = _cost_log_path(config)
    if not path.exists():
        return 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if ts >= cutoff:
                total += entry.get("cost_usd", 0.0)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return total


def wake_count_today(config: FiamConfig) -> int:
    """Count number of wakes today in the project timezone."""
    path = _cost_log_path(config)
    if not path.exists():
        return 0
    today = config.now_local().date()
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if config.ensure_timezone(ts).astimezone(config.project_tz()).date() == today:
                count += 1
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return count


def check_budget(config: FiamConfig) -> tuple[bool, str]:
    """Check if daily budget allows another wake.

    Returns (allowed, reason).
    - allowed=True: budget OK
    - allowed=False: budget exceeded, reason explains
    """
    budget = getattr(config, "daily_budget_usd", 0.0)
    if budget <= 0:
        return True, ""  # no budget configured

    spent = daily_spend(config)
    if spent >= budget:
        return False, f"日预算已用完 (${spent:.2f}/${budget:.2f})"
    return True, ""


def budget_awareness(config: FiamConfig) -> list[str]:
    """Generate quota awareness lines for context injection."""
    budget = getattr(config, "daily_budget_usd", 0.0)
    spent = daily_spend(config)
    wakes = wake_count_today(config)
    last_hour = recent_spend(config, hours=1)

    lines = []

    if budget > 0:
        pct = (spent / budget) * 100 if budget else 0
        remaining = max(0, budget - spent)
        lines.append(f"今日额度: ${spent:.2f}/${budget:.2f} ({pct:.0f}% 已用, 剩余 ${remaining:.2f})")
        if pct >= 90:
            lines.append("  ⚠ 额度即将用完，请节省 token — 减少工具调用，精简回复")
        elif pct >= 70:
            lines.append("  ⚡ 额度过半，注意效率")
    else:
        if spent > 0:
            lines.append(f"今日支出: ${spent:.2f} ({wakes} 次唤醒)")

    if last_hour > 0.5:
        lines.append(f"  近 1 小时支出: ${last_hour:.2f} — 注意频率")

    return lines
