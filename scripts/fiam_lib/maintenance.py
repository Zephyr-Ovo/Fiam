"""Maintenance commands that do not depend on the legacy event store."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _is_daemon_running
from fiam_lib.jsonl import _claude_projects_dir, _sanitize_home_path


@dataclass(frozen=True, slots=True)
class CleanTarget:
    path: Path
    label: str
    recreate_dir: bool = False


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
    """Reset generated runtime data. Refuses to run while daemon is active."""
    code_path = _project_root()
    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  Error: daemon is running (PID {pid}). Run 'fiam stop' first.", file=sys.stderr)
        sys.exit(1)

    config = _load_clean_config(args, code_path)
    targets = collect_clean_targets(code_path, config)

    if not targets:
        print("\n  Already clean — nothing to remove.\n")
        return

    print(f"\n  fiam {getattr(args, 'command', 'clean')}\n")
    for target in targets:
        try:
            rel = target.path.relative_to(code_path)
        except ValueError:
            rel = target.path
        print(f"  {target.label:<36}  {rel}")
    print()

    if not getattr(args, "yes", False):
        confirm = input("  Proceed? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.\n")
            return

    apply_clean_targets(targets, config)

    print("\n  Done. fiam is a blank test whiteboard. Run 'fiam start' to begin.\n")


def _load_clean_config(args: argparse.Namespace, code_path: Path):
    toml = _toml_path()
    if not toml.exists():
        return None
    try:
        from fiam.config import FiamConfig
        config = FiamConfig.from_toml(toml, code_path)
    except Exception:
        return None
    home_arg = getattr(args, "home", None)
    if home_arg:
        config.home_path = Path(home_arg).resolve()
    return config


def collect_clean_targets(code_path: Path, config=None) -> list[CleanTarget]:
    """Return generated runtime paths that are safe to delete for test reset.

    Keep configuration, source code, constitution/CLAUDE.md, and self/*.md
    identity/instruction files intact. Clear only generated conversation,
    memory, queue, upload, and session state.
    """
    store_dir = config.store_dir if config else code_path / "store"
    logs_sessions = code_path / "logs" / "sessions"
    targets: list[CleanTarget] = []
    seen: set[Path] = set()

    def add(path: Path, label: str, *, recreate_dir: bool = False) -> None:
        if not path.exists():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        count = _count_files(path)
        prefix = f"{count} file{'s' if count != 1 else ''}" if count else "empty"
        targets.append(CleanTarget(path, f"{prefix} ({label})", recreate_dir))

    for label, path in [
        ("pool events/vectors/edges", store_dir / "pool"),
        ("beat feature vectors", store_dir / "features"),
        ("wearable queues", store_dir / "wearable"),
        ("legacy events", store_dir / "events"),
        ("legacy embeddings", store_dir / "embeddings"),
        ("legacy graph", store_dir / "graph"),
        ("session logs", logs_sessions),
    ]:
        add(path, label, recreate_dir=True)

    for label, path in [
        ("flow", store_dir / "flow.jsonl"),
        ("cursor", store_dir / "cursor.json"),
        ("annotation state", store_dir / "annotation_state.json"),
        ("legacy cache", store_dir / "narrative_cache.json"),
        ("legacy graph jsonl", store_dir / "graph.jsonl"),
    ]:
        add(path, label)

    if config:
        home = config.home_path
        for label, path in [
            ("app chat history", home / "app_history"),
            ("uploaded test files", home / "uploads"),
            ("inbox queue", config.inbox_dir),
            ("outbox queue", config.outbox_dir),
            ("retired CC sessions", config.self_dir / "retired"),
        ]:
            add(path, label, recreate_dir=label in {"inbox queue", "outbox queue"})

        for label, path in [
            ("recall", config.background_path),
            ("recall dirty marker", home / ".recall_dirty"),
            ("app cut markers", home / "app_cuts.jsonl"),
            ("active CC session", config.active_session_path),
            ("todo", config.todo_path),
            ("AI state", config.ai_state_path),
            ("legacy sleep state", config.sleep_state_path),
            ("generated state", config.state_path),
            ("daily summary", config.daily_summary_path),
            ("pending external", config.pending_external_path),
            ("pending external processing", home / "pending_external.processing"),
            ("interactive lock", config.interactive_lock_path),
        ]:
            add(path, label)

        for path in sorted(config.self_dir.glob("todo_*.jsonl")):
            add(path, "todo auxiliary")

        claude_sessions = _claude_projects_dir() / _sanitize_home_path(home)
        if claude_sessions.is_dir():
            for path in sorted(claude_sessions.glob("*.jsonl")):
                add(path, "Claude Code history")

    return targets


def apply_clean_targets(targets: list[CleanTarget], config=None) -> None:
    for target in targets:
        path = target.path
        if path.is_dir():
            shutil.rmtree(path)
            if target.recreate_dir:
                path.mkdir(parents=True, exist_ok=True)
        elif path.exists():
            path.unlink()
    if config:
        config.ensure_dirs()


def _count_files(path: Path) -> int:
    if path.is_dir():
        return sum(1 for item in path.rglob("*") if item.is_file())
    return 1 if path.exists() else 0