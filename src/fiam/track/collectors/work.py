from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .edit import EditEvent, _GIT_FMT, _parse_log

import subprocess


def collect_work_events(
    code_dir: Path,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[EditEvent]:
    """Read `git log` from the fiam-code repo and return parsed events.

    Same shape as edit events — only the source repo differs.
    """
    code_dir = Path(code_dir)
    if not (code_dir / ".git").exists():
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
    try:
        proc = subprocess.run(
            cmd, cwd=str(code_dir), check=False,
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return _parse_log(proc.stdout.decode("utf-8", errors="replace"))
