"""JSONL session file discovery, cursor management, adapter delegation."""

from __future__ import annotations

import json
from pathlib import Path

from fiam_lib.core import _project_root


# ------------------------------------------------------------------
# JSONL session file discovery
# ------------------------------------------------------------------

def _claude_projects_dir() -> Path:
    """Locate Claude Code's projects directory.

    All platforms: ~/.claude/projects/
    """
    return Path.home() / ".claude" / "projects"


def _sanitize_home_path(home_path: Path) -> str:
    """Derive the sanitized directory name Claude Code uses.

    Claude Code replaces path separators and colons with dashes.
    e.g. D:\\ai-home → D--ai-home, /root/fiet-home → -root-fiet-home
    """
    raw = str(home_path.resolve())
    # Replace all separators and colons with dashes
    sanitized = raw.replace("\\", "-").replace("/", "-").replace(":", "-")
    return sanitized


def _find_latest_jsonl(home_path: Path, *, debug: bool = False) -> Path | None:
    """Find the most recently modified JSONL file for the home project."""
    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(home_path)
    expected_dir = projects_dir / sanitized

    if debug:
        print(f"[find_jsonl] projects dir: {projects_dir}")
        print(f"[find_jsonl] sanitized home name: {sanitized}")
        print(f"[find_jsonl] expected dir: {expected_dir}")
        print(f"[find_jsonl] exists: {expected_dir.is_dir()}")

    # Try direct sanitized-path match first
    if expected_dir.is_dir():
        result = _latest_in_dir(expected_dir)
        if debug and result:
            print(f"[find_jsonl] matched exact dir → {result}")
        return result

    # Fallback: scan all project directories for the most recent JSONL
    if debug:
        print(f"[find_jsonl] exact dir not found, scanning all subdirs...")

    if projects_dir.is_dir():
        latest: Path | None = None
        latest_mtime = 0.0
        for subdir in projects_dir.iterdir():
            if subdir.is_dir():
                candidate = _latest_in_dir(subdir)
                if candidate and candidate.stat().st_mtime > latest_mtime:
                    latest = candidate
                    latest_mtime = candidate.stat().st_mtime
        if debug and latest:
            print(f"[find_jsonl] fallback found → {latest}")
        return latest

    return None


def _latest_in_dir(directory: Path) -> Path | None:
    """Return the most recently modified .jsonl file in a directory."""
    jsonl_files = list(directory.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


# ------------------------------------------------------------------
# Processed-session cursor (byte-offset tracking)
# ------------------------------------------------------------------

def _cursor_path(code_path: Path) -> Path:
    return code_path / "store" / "cursor.json"


def _load_cursor(code_path: Path) -> dict[str, dict]:
    """Load {jsonl_abs_path: {"byte_offset": int, "mtime": float}}."""
    cp = _cursor_path(code_path)
    if not cp.exists():
        return {}
    try:
        return json.loads(cp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cursor(code_path: Path, cursor: dict[str, dict]) -> None:
    cp = _cursor_path(code_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cursor, indent=2), encoding="utf-8")


# ------------------------------------------------------------------
# JSONL parsing — delegated to adapter
# ------------------------------------------------------------------

def _get_adapter():
    """Return the conversation adapter (currently Claude Code only)."""
    from fiam.adapter import get_adapter
    return get_adapter("claude_code")


def _parse_jsonl(jsonl_path: Path) -> list[dict[str, str]]:
    """Parse a Claude Code JSONL session file into conversation turns."""
    return _get_adapter().parse(jsonl_path)


def _parse_jsonl_from(jsonl_path: Path, byte_offset: int = 0) -> tuple[list[dict[str, str]], int]:
    """Parse JSONL starting from *byte_offset*. Returns (turns, new_offset)."""
    return _get_adapter().parse_incremental(jsonl_path, byte_offset)
