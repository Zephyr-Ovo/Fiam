from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EditEvent:
    sha: str
    ts: datetime              # author time, tz-aware UTC
    author: str
    subject: str
    files: tuple[str, ...] = field(default_factory=tuple)
    insertions: int = 0
    deletions: int = 0

    def short_sha(self) -> str:
        return self.sha[:7]


_GIT_FMT = "%H%x01%aI%x01%an%x01%s"


def collect_edit_events(
    vault_dir: Path,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[EditEvent]:
    """Read `git log` from a studio vault and return parsed events.

    Returns events in reverse-chronological order (newest first), matching
    `git log` default. `track/` files written by the 记录官 itself are
    excluded via pathspec so summarization doesn't feed on its own output.
    """
    vault_dir = Path(vault_dir)
    if not (vault_dir / ".git").exists():
        return []
    cmd = [
        "git", "log",
        f"--pretty=format:{_GIT_FMT}",
        "--numstat",
        "-z",
    ]
    if since is not None:
        cmd.append(f"--since={since.astimezone(timezone.utc).isoformat()}")
    if limit is not None and limit > 0:
        cmd.append(f"-n{int(limit)}")
    cmd += ["--", ".", ":(exclude)track/**"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(vault_dir), check=False,
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return _parse_log(proc.stdout.decode("utf-8", errors="replace"))


def _parse_log(raw: str) -> list[EditEvent]:
    if not raw.strip():
        return []
    events: list[EditEvent] = []
    # `git log -z` separates commits with NUL; within a commit, the header is
    # followed by an optional numstat block. We parse defensively.
    chunks = raw.split("\x00")
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        i += 1
        if not chunk:
            continue
        # numstat lines for the previous commit may share a chunk separator with
        # the next header — split on newline to recover.
        head, _, tail = chunk.partition("\n")
        parts = head.split("\x01")
        if len(parts) < 4:
            continue
        sha, iso_ts, author, subject = parts[0], parts[1], parts[2], parts[3]
        try:
            ts = datetime.fromisoformat(iso_ts).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        files: list[str] = []
        ins_total = 0
        del_total = 0
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            ins_s, del_s, path = cols[0], cols[1], cols[2]
            try:
                ins_total += int(ins_s) if ins_s.isdigit() else 0
                del_total += int(del_s) if del_s.isdigit() else 0
            except ValueError:
                pass
            if path.startswith("track/"):
                continue
            files.append(path)
        events.append(EditEvent(
            sha=sha, ts=ts, author=author, subject=subject,
            files=tuple(files), insertions=ins_total, deletions=del_total,
        ))
    return events
