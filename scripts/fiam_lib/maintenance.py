"""Maintenance commands that do not depend on the legacy event store."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _is_daemon_running
from fiam_lib.jsonl import _claude_projects_dir, _sanitize_home_path


def cmd_find_sessions(args: argparse.Namespace) -> None:
    """Diagnostic: list Claude Code JSONL session files for a home path."""
    home_arg = getattr(args, "home", None)
    if home_arg:
        home_path = Path(home_arg).resolve()
    else:
        from fiam.config import FiamConfig
        config = FiamConfig.from_toml(_toml_path(), _project_root())
        home_path = config.home_path

    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(home_path)
    expected_dir = projects_dir / sanitized

    print(f"Home path:        {home_path}")
    print(f"Projects dir:     {projects_dir}")
    print(f"Sanitized name:   {sanitized}")
    print(f"Expected dir:     {expected_dir}")
    print(f"Expected exists:  {expected_dir.is_dir()}")
    print()

    if not projects_dir.is_dir():
        print(f"Projects directory does not exist: {projects_dir}")
        return

    found_any = False
    for subdir in sorted(projects_dir.iterdir()):
        if not subdir.is_dir():
            continue
        jsonl_files = sorted(
            subdir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not jsonl_files:
            continue
        found_any = True
        marker = " <- MATCH" if subdir.name == sanitized else ""
        print(f"  {subdir.name}/{marker}")
        for jf in jsonl_files:
            size_kb = jf.stat().st_size / 1024
            mtime = datetime.fromtimestamp(jf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {jf.name}  ({size_kb:.1f} KB, modified {mtime})")

    if not found_any:
        print("  (no JSONL files found in any project directory)")


def cmd_clean(args: argparse.Namespace) -> None:
    """Reset generated store data. Refuses to run while daemon is active."""
    code_path = _project_root()
    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  Error: daemon is running (PID {pid}). Run 'fiam stop' first.", file=sys.stderr)
        sys.exit(1)

    store_dir = code_path / "store"
    logs_sessions = code_path / "logs" / "sessions"

    def _count(path: Path) -> int:
        if path.is_dir():
            return sum(1 for item in path.rglob("*") if item.is_file())
        return 1 if path.exists() else 0

    targets: list[tuple[Path, str]] = []
    for label, path in [
        ("pool", store_dir / "pool"),
        ("features", store_dir / "features"),
        ("legacy events", store_dir / "events"),
        ("legacy embeddings", store_dir / "embeddings"),
        ("legacy graph", store_dir / "graph"),
        ("sessions", logs_sessions),
    ]:
        n = _count(path)
        if n:
            suffix = "s" if n != 1 else ""
            targets.append((path, f"{n} file{suffix} ({label})"))

    for label, path in [
        ("flow", store_dir / "flow.jsonl"),
        ("cursor", store_dir / "cursor.json"),
        ("annotation state", store_dir / "annotation_state.json"),
        ("legacy cache", store_dir / "narrative_cache.json"),
        ("legacy graph jsonl", store_dir / "graph.jsonl"),
    ]:
        if path.exists():
            targets.append((path, label))

    recall_path: Path | None = None
    toml = _toml_path()
    if toml.exists():
        try:
            from fiam.config import FiamConfig
            cfg = FiamConfig.from_toml(toml, code_path)
            if cfg.background_path.exists():
                recall_path = cfg.background_path
        except Exception:
            pass

    if not targets and recall_path is None:
        print("\n  Already clean — nothing to remove.\n")
        return

    print("\n  fiam clean\n")
    for path, label in targets:
        try:
            rel = path.relative_to(code_path)
        except ValueError:
            rel = path
        print(f"  {label:<32}  {rel}")
    if recall_path:
        print(f"  {'recall.md':<32}  {recall_path}")
    print()

    if not getattr(args, "yes", False):
        confirm = input("  Proceed? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.\n")
            return

    for path, _label in targets:
        if path.is_dir():
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        elif path.exists():
            path.unlink()

    if recall_path and recall_path.exists():
        recall_path.unlink()

    print("\n  Done. fiam is clean. Run 'fiam start' to begin.\n")