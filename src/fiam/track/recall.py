from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# STUDIO_CONVENTIONS.md §6 — time-decay rendering tiers.
# (max_age_days, deepest header level kept). level 0 means "title list only".
_TIERS: tuple[tuple[float, int], ...] = (
    (7.0, 3),       # ≤7d  → keep ###
    (30.0, 2),      # ≤30d → keep ##
    (90.0, 1),      # ≤90d → keep #
    (float("inf"), 0),  # >90d → titles only
)


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MONTH_RE = re.compile(r"\b(\d{4})-(\d{2})\b")
_DAY_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_HM_RE = re.compile(r"\b(\d{2}):(\d{2})\b")


@dataclass(slots=True)
class _Section:
    level: int                  # 1=#, 2=##, 3=###
    header: str                 # full header line, without leading hashes
    body: list[str]             # lines until next header or end
    date: datetime | None       # best-effort date parsed from header / context


def recall(
    vault_dir: Path,
    name: str,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> str:
    """Return a folded view of `track/<name>.md` per the §6 decay schedule.

    `since`: drop sections older than this (and any deeper sections under them).
    `now`:   anchor for "today"; defaults to wall clock.
    """
    track_file = Path(vault_dir) / "track" / f"{name}.md"
    if not track_file.exists():
        return ""
    text = track_file.read_text(encoding="utf-8")
    text = _strip_frontmatter(text)
    sections = _parse_sections(text)
    anchor = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since_utc = since.astimezone(timezone.utc) if since else None
    out: list[str] = []
    for sec in sections:
        if since_utc and sec.date and sec.date < since_utc:
            continue
        keep_level = _keep_level_for(sec.date, anchor)
        if sec.level > keep_level:
            continue
        hashes = "#" * sec.level
        out.append(f"{hashes} {sec.header}")
        if keep_level >= 3:
            # full detail tier
            body = "\n".join(sec.body).rstrip()
            if body:
                out.append(body)
        # for keep_level <= 2 we still emit headers but drop the body lines
        # (which are commit detail bullets); that's the "fold" behavior.
    return ("\n".join(out).rstrip() + "\n") if out else ""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    rest = text[end + 4:]
    return rest.lstrip("\n")


def _parse_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    current: _Section | None = None
    current_day: datetime | None = None
    current_month: datetime | None = None
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            level = len(m.group(1))
            header = m.group(2)
            date = _parse_header_date(level, header, current_day, current_month)
            if level == 1:
                current_month = date
                current_day = None
            elif level == 2:
                current_day = date
            current = _Section(level=level, header=header, body=[], date=date)
            sections.append(current)
        else:
            if current is not None:
                current.body.append(line)
    return sections


def _parse_header_date(
    level: int,
    header: str,
    current_day: datetime | None,
    current_month: datetime | None,
) -> datetime | None:
    if level == 1:
        m = _MONTH_RE.search(header)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
        return None
    if level == 2:
        m = _DAY_RE.search(header)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        return current_month
    # level >= 3: inherit day, refine with HH:MM if present
    if current_day is None:
        return None
    hm = _HM_RE.search(header)
    if hm:
        return current_day.replace(hour=int(hm.group(1)), minute=int(hm.group(2)))
    return current_day


def _keep_level_for(date: datetime | None, now: datetime) -> int:
    if date is None:
        # Undated content — treat as fresh so the writer's intent isn't lost.
        return 3
    age_days = (now - date).total_seconds() / 86400.0
    if age_days < 0:
        return 3
    for max_age, level in _TIERS:
        if age_days <= max_age:
            return level
    return 0
