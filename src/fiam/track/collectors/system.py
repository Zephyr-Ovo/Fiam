from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SystemEvent:
    ts: datetime
    phase: str
    status: str
    channel: str
    surface: str
    duration_ms: int = 0
    turn_id: str = ""
    request_id: str = ""
    error: str = ""
    model: str = ""

    def kind_label(self) -> str:
        if self.phase.startswith("dashboard."):
            return self.phase.split(".", 1)[1]
        return self.phase


def collect_system_events(
    store_dir: Path,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[SystemEvent]:
    """Read turn_traces.jsonl and return runtime-phase events, newest first."""
    traces_file = Path(store_dir) / "turn_traces.jsonl"
    if not traces_file.exists():
        return []
    try:
        raw_lines = traces_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    since_utc = since.astimezone(timezone.utc) if since else None
    events: list[SystemEvent] = []
    for line in reversed(raw_lines):
        if limit is not None and len(events) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        phase = str(row.get("phase") or "")
        if not phase:
            continue
        started_at = str(row.get("started_at") or "")
        if not started_at:
            continue
        try:
            ts = datetime.fromisoformat(started_at).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if since_utc and ts < since_utc:
            continue
        refs = row.get("refs") if isinstance(row.get("refs"), dict) else {}
        events.append(SystemEvent(
            ts=ts,
            phase=phase,
            status=str(row.get("status") or ""),
            channel=str(row.get("channel") or ""),
            surface=str(row.get("surface") or ""),
            duration_ms=int(row.get("duration_ms") or 0),
            turn_id=str(row.get("turn_id") or ""),
            request_id=str(row.get("request_id") or ""),
            error=str(row.get("error") or ""),
            model=str(refs.get("model") or ""),
        ))
    return events
