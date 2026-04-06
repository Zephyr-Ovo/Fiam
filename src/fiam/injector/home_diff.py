"""
Home diff detector — surfaces uncommitted changes as user's recent actions.

In the home workflow, the AI (Claude Code) is the sole Git committer.
Any uncommitted changes represent the user's physical edits since the
last session. This module detects those changes and produces a
human-readable summary for injection into the background.

Auto-generated files are ignored (recall.md, CLAUDE.md).
Events, embeddings live in code_path/store/, not the home directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fiam.config import FiamConfig


# Paths that fiam itself writes — not the user's actions
_IGNORE_PATTERNS = {
    "recall.md",
    "CLAUDE.md",
    ".obsidian/",
}

_IGNORE_PREFIXES = (
    ".obsidian/",
)


def _is_auto_generated(path: str) -> bool:
    """Return True if *path* is written by fiam automation, not by the user."""
    if path in _IGNORE_PATTERNS:
        return True
    for prefix in _IGNORE_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _run_git(home_path: Path, *args: str) -> str | None:
    """Run a git command in the home directory. Returns stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(home_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _classify_status(code: str) -> str:
    """Map git status short code to Chinese verb."""
    if code in ("A", "?"):
        return "新建了"
    if code == "M":
        return "修改了"
    if code == "D":
        return "删除了"
    if code == "R":
        return "重命名了"
    return "变更了"


def detect_uncommitted(config: FiamConfig) -> str:
    """Detect uncommitted changes in the AI's home and return a summary.

    Returns an empty string if:
    - home has no .git directory
    - no meaningful uncommitted changes exist
    - git is not available

    Otherwise returns a block like:
        {user_name}进行了如下修改：
        - 修改了 self/journal/04-05.md
        - 新建了 {user_name}/notes.md
    """
    home = config.home_path
    if not (home / ".git").is_dir():
        return ""

    # Get short status of working tree + index
    raw = _run_git(home, "status", "--porcelain=v1")
    if raw is None:
        return ""

    lines: list[str] = []
    for line in raw.strip().splitlines():
        if len(line) < 4:
            continue
        # porcelain v1 format: XY <path>  or  XY <old> -> <new>
        index_code = line[0]
        worktree_code = line[1]
        path_part = line[3:]

        # Handle renames: "R  old -> new"
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]

        path_part = path_part.strip().strip('"')

        if _is_auto_generated(path_part):
            continue

        # Pick the most informative status code
        code = worktree_code if worktree_code not in (" ", "?") else index_code
        if code == "?" and index_code == "?":
            code = "A"  # untracked → treat as new

        verb = _classify_status(code)
        lines.append(f"- {verb} {path_part}")

    if not lines:
        return ""

    header = f"{config.user_name}进行了如下修改："
    return header + "\n" + "\n".join(lines)
