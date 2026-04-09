"""Store management commands — reindex, scan, clean, find-sessions."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _build_config, _is_daemon_running
from fiam_lib.jsonl import (
    _claude_projects_dir,
    _sanitize_home_path,
    _load_cursor,
    _save_cursor,
    _parse_jsonl_from,
)


def cmd_reindex(args: argparse.Namespace) -> None:
    """Rebuild all embeddings with current models."""
    config = _build_config(args)
    from fiam.retriever.embedder import Embedder
    from fiam.store.home import HomeStore

    store = HomeStore(config)
    embedder = Embedder(config)
    events = store.all_events()

    if not events:
        print("No events to reindex.")
        return

    print(f"Reindexing {len(events)} events...")
    for i, event in enumerate(events, 1):
        if not event.body.strip():
            continue
        vec = embedder.embed(event.body)
        emb_path = embedder.save(vec, event.event_id)
        event.embedding = emb_path
        event.embedding_dim = vec.shape[-1]
        store.update_metadata(event)
        if config.debug_mode or i % 10 == 0:
            print(f"  [{i}/{len(events)}] {event.filename}")

    print(f"Done. All embeddings are now {config.embedding_dim}-dim.")


def cmd_find_sessions(args: argparse.Namespace) -> None:
    """Diagnostic: list all JSONL session files found under ~/.claude/projects/."""
    home_path = Path(args.home).resolve()
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

    # List all subdirectories and their JSONL files
    found_any = False
    for subdir in sorted(projects_dir.iterdir()):
        if not subdir.is_dir():
            continue
        jsonl_files = sorted(subdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not jsonl_files:
            continue
        found_any = True
        marker = " ← MATCH" if subdir.name == sanitized else ""
        print(f"  {subdir.name}/{marker}")
        for jf in jsonl_files:
            size_kb = jf.stat().st_size / 1024
            mtime = datetime.fromtimestamp(jf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {jf.name}  ({size_kb:.1f} KB, modified {mtime})")

    if not found_any:
        print("  (no JSONL files found in any project directory)")


def cmd_scan(args: argparse.Namespace) -> None:
    """One-time scan: process all historical JSONL files into memory."""
    config = _build_config(args)
    from fiam.pipeline import post_session

    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(config.home_path)
    jsonl_dir = projects_dir / sanitized

    if not jsonl_dir.is_dir():
        print(f"No project directory found: {jsonl_dir}", file=sys.stderr)
        print("Open Claude Code in your home directory first.", file=sys.stderr)
        sys.exit(1)

    jsonl_files = sorted(jsonl_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not jsonl_files:
        print("No JSONL session files found.")
        return

    # Check cursor to skip already-processed files (prevents duplicates)
    # Cache mtimes upfront — files may disappear during a long scan (Bug #3)
    cursor = _load_cursor(config.code_path)
    force = getattr(args, "force", False)

    file_mtimes: dict[str, float] = {}
    to_process = []
    for jf in jsonl_files:
        jkey = str(jf.resolve())
        try:
            file_mtimes[jkey] = jf.stat().st_mtime
        except FileNotFoundError:
            continue  # already gone, skip
        entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})
        if not force and entry["byte_offset"] > 0:
            continue  # already scanned
        to_process.append(jf)

    if not to_process:
        print(f"\n  All {len(jsonl_files)} session file(s) already scanned.")
        print("  Use --force to re-scan from scratch.\n")
        return

    print(f"\n  Scanning {len(to_process)} session file(s)"
          f" ({len(jsonl_files) - len(to_process)} already processed)...\n")

    total_events = 0

    for i, jf in enumerate(to_process, 1):
        jkey = str(jf.resolve())
        try:
            turns, new_offset = _parse_jsonl_from(jf, 0)
        except FileNotFoundError:
            print(f"  [{i}/{len(to_process)}] {jf.name}: file disappeared, skipped")
            continue
        if not turns:
            print(f"  [{i}/{len(to_process)}] {jf.name}: 0 turns, skipped")
            continue
        print(f"  [{i}/{len(to_process)}] {jf.name}: {len(turns)} turns", end="", flush=True)

        # Use JSONL file mtime as session timestamp (not extraction time)
        mtime = file_mtimes.get(jkey, 0.0)
        session_time = datetime.fromtimestamp(mtime, tz=timezone.utc) if mtime else None

        try:
            r = post_session(config, turns, session_time=session_time)
            n = r["events_written"]
            total_events += n
            print(f" → {n} events")
        except Exception as e:
            print(f" → error: {e}")
        cursor[jkey] = {"byte_offset": new_offset, "mtime": mtime}

    _save_cursor(config.code_path, cursor)
    print(f"\n  Done. {total_events} events stored.")
    print(f"  Run 'fiam start' to begin live tracking.\n")


# ------------------------------------------------------------------
# clean — reset store to factory-fresh state
# ------------------------------------------------------------------

def cmd_clean(args: argparse.Namespace) -> None:
    """Reset store to factory-fresh state (events, embeddings, logs)."""
    import shutil

    code_path = _project_root()

    # Refuse if daemon is running
    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  Error: daemon is running (PID {pid}). Run 'fiam stop' first.",
              file=sys.stderr)
        sys.exit(1)

    store_dir = code_path / "store"
    logs_sessions = code_path / "logs" / "sessions"

    def _count(path: Path) -> int:
        if path.is_dir():
            return sum(1 for _ in path.rglob("*") if _.is_file())
        return 1 if path.exists() else 0

    # Build list of things to wipe
    targets: list[tuple[Path, str]] = []
    for label, path in [
        ("events",     store_dir / "events"),
        ("embeddings", store_dir / "embeddings"),
        ("graph",      store_dir / "graph"),
        ("sessions",   logs_sessions),
    ]:
        n = _count(path)
        if n:
            s = "s" if n != 1 else ""
            targets.append((path, f"{n} file{s} ({label})"))

    for label, path in [
        ("cursor",    store_dir / "cursor.json"),
        ("cache",     store_dir / "narrative_cache.json"),
    ]:
        if path.exists():
            targets.append((path, label))

    # Check recall.md in home
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
        print()
        print("  Already clean — nothing to remove.")
        print()
        return

    print()
    print("  fiam clean")
    print()
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
            print("  Cancelled.")
            print()
            return

    # Execute
    for path, _ in targets:
        if path.is_dir():
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        elif path.exists():
            path.unlink()

    if recall_path and recall_path.exists():
        recall_path.unlink()

    print()
    print("  Done. fiam is clean. Run 'fiam scan' or 'fiam start' to begin.")
    print()
