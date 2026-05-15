from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from .collectors.edit import EditEvent
from .collectors.system import SystemEvent
from .config import TrackConfig


SummarizeFn = Callable[[str, str], str]
"""(level, context_text) -> short markdown summary line(s)."""


def _llm_summarize(cfg: TrackConfig) -> SummarizeFn:
    """Build an OpenAI-compatible summarizer bound to `cfg`.

    Falls back to a deterministic line if the call errors out.
    """
    def summarize(level: str, context: str) -> str:
        body = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the 记录官 (track-keeper) for a personal vault. "
                        "Summarize editing activity at the given hierarchy level "
                        "in one to three concise sentences. Output plain text, "
                        "no markdown headings, no lists."
                    ),
                },
                {"role": "user", "content": f"level={level}\n\n{context}"},
            ],
            "temperature": 0.3,
            "max_tokens": 200,
        }
        req = urllib.request.Request(
            f"{cfg.endpoint.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            text = " ".join(text.split())
            if text:
                return text
        except Exception:
            pass
        return _fallback_summary(level, context)
    return summarize


def _fallback_summary(level: str, context: str) -> str:
    first = next((line.strip() for line in context.splitlines() if line.strip()), "")
    return first or f"({level} summary unavailable)"


@dataclass(frozen=True, slots=True)
class _DayBucket:
    day: str                       # YYYY-MM-DD
    events: tuple[EditEvent, ...]


def _group_by_day(events: Iterable[EditEvent]) -> list[_DayBucket]:
    by_day: dict[str, list[EditEvent]] = defaultdict(list)
    for ev in events:
        key = ev.ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day[key].append(ev)
    return [
        _DayBucket(day=day, events=tuple(sorted(by_day[day], key=lambda e: e.ts)))
        for day in sorted(by_day, reverse=True)
    ]


def _group_by_month(buckets: list[_DayBucket]) -> dict[str, list[_DayBucket]]:
    by_month: dict[str, list[_DayBucket]] = defaultdict(list)
    for b in buckets:
        by_month[b.day[:7]].append(b)
    return by_month


def _event_line(ev: EditEvent) -> str:
    hm = ev.ts.astimezone(timezone.utc).strftime("%H:%M")
    subject = ev.subject.strip() or "(no subject)"
    files_preview = ", ".join(ev.files[:3])
    if len(ev.files) > 3:
        files_preview += f", +{len(ev.files) - 3}"
    delta = f"+{ev.insertions}/-{ev.deletions}" if (ev.insertions or ev.deletions) else ""
    return f"### {hm} · {subject}\n- sha: `{ev.short_sha()}` · {delta}\n- files: {files_preview or '(none)'}".rstrip()


def summarize_edits(
    events: list[EditEvent],
    *,
    summarize_fn: SummarizeFn | None = None,
) -> str:
    """Build hierarchical markdown body: # month / ## day / ### commit.

    `summarize_fn(level, context)` produces narrative prose for level∈{month,day};
    if None or it returns empty, the first commit subject of that span is used.
    """
    if not events:
        return ""
    fn: SummarizeFn = summarize_fn or _fallback_summary
    buckets = _group_by_day(events)
    by_month = _group_by_month(buckets)

    parts: list[str] = []
    for month in sorted(by_month, reverse=True):
        day_buckets = by_month[month]
        month_events = [ev for db in day_buckets for ev in db.events]
        month_context = "\n".join(
            f"{ev.ts.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} {ev.subject}"
            for ev in month_events[:50]
        )
        parts.append(f"# {month}\n")
        narrative = fn("month", month_context)
        if narrative:
            parts.append(narrative + "\n")
        for db in day_buckets:
            day_context = "\n".join(
                f"{ev.ts.astimezone(timezone.utc).strftime('%H:%M')} {ev.subject}"
                for ev in db.events
            )
            parts.append(f"## {db.day}\n")
            day_narrative = fn("day", day_context)
            if day_narrative:
                parts.append(day_narrative + "\n")
            for ev in db.events:
                parts.append(_event_line(ev) + "\n")
    return "\n".join(parts).rstrip() + "\n"


@dataclass(frozen=True, slots=True)
class _SystemDayBucket:
    day: str
    events: tuple[SystemEvent, ...]


def _system_group_by_day(events: Iterable[SystemEvent]) -> list[_SystemDayBucket]:
    by_day: dict[str, list[SystemEvent]] = defaultdict(list)
    for ev in events:
        key = ev.ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day[key].append(ev)
    return [
        _SystemDayBucket(day=day, events=tuple(sorted(by_day[day], key=lambda e: e.ts)))
        for day in sorted(by_day, reverse=True)
    ]


def _system_event_line(ev: SystemEvent) -> str:
    hm = ev.ts.astimezone(timezone.utc).strftime("%H:%M")
    label = ev.kind_label()
    scene = ev.channel or "—"
    if ev.surface:
        scene += f"/{ev.surface}"
    dur = f"{ev.duration_ms}ms" if ev.duration_ms else ""
    status = ev.status or "—"
    parts = [f"### {hm} · {label} · {status}"]
    detail = f"- scene: {scene}"
    if dur:
        detail += f" · {dur}"
    if ev.model:
        detail += f" · model={ev.model}"
    if ev.error:
        detail += f"\n- error: {ev.error[:200]}"
    parts.append(detail)
    return "\n".join(parts)


def summarize_system(
    events: list[SystemEvent],
    *,
    summarize_fn: SummarizeFn | None = None,
) -> str:
    """Build hierarchical markdown for system events: # month / ## day / ### phase."""
    if not events:
        return ""
    fn: SummarizeFn = summarize_fn or _fallback_summary
    buckets = _system_group_by_day(events)
    by_month: dict[str, list[_SystemDayBucket]] = defaultdict(list)
    for b in buckets:
        by_month[b.day[:7]].append(b)

    parts: list[str] = []
    for month in sorted(by_month, reverse=True):
        day_buckets = by_month[month]
        month_events = [ev for db in day_buckets for ev in db.events]
        month_context = "\n".join(
            f"{ev.ts.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} {ev.kind_label()} {ev.status}"
            for ev in month_events[:50]
        )
        parts.append(f"# {month}\n")
        narrative = fn("month", month_context)
        if narrative:
            parts.append(narrative + "\n")
        for db in day_buckets:
            day_context = "\n".join(
                f"{ev.ts.astimezone(timezone.utc).strftime('%H:%M')} {ev.kind_label()} {ev.status} {ev.channel}/{ev.surface}"
                for ev in db.events
            )
            parts.append(f"## {db.day}\n")
            day_narrative = fn("day", day_context)
            if day_narrative:
                parts.append(day_narrative + "\n")
            for ev in db.events:
                parts.append(_system_event_line(ev) + "\n")
    return "\n".join(parts).rstrip() + "\n"


def build_summarizer(cfg: TrackConfig) -> SummarizeFn:
    """Return an LLM-backed summarizer if cfg is ready, else deterministic."""
    if cfg.llm_ready:
        return _llm_summarize(cfg)
    return _fallback_summary
