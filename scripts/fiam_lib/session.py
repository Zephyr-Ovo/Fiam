"""Manual pipeline commands — pre, post, session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fiam_lib.core import _build_config
from fiam_lib.jsonl import _find_latest_jsonl, _load_cursor, _save_cursor, _parse_jsonl, _parse_jsonl_from


def cmd_pre(args: argparse.Namespace) -> None:
    """Run pre_session pipeline."""
    config = _build_config(args)
    from fiam.pipeline import pre_session

    result = pre_session(config)
    print(f"pre_session complete (session: {result['session_id']})")
    print(f"  events in home: {result['event_count']}")
    print(f"  background written to: {config.background_path}")


def cmd_post(args: argparse.Namespace) -> None:
    """Run post_session pipeline on the latest JSONL or a test file."""
    config = _build_config(args)
    from fiam.pipeline import post_session

    test_file = getattr(args, "test_file", None)
    if test_file:
        test_path = Path(test_file).resolve()
        if not test_path.exists():
            print(f"Error: test file not found: {test_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Loading test fixture: {test_path}")
        conversation = json.loads(test_path.read_text(encoding="utf-8"))
    else:
        jsonl_path = _find_latest_jsonl(config.home_path, debug=config.debug_mode)
        if jsonl_path is None:
            from fiam_lib.jsonl import _claude_projects_dir
            print("Error: no JSONL session file found for this home.", file=sys.stderr)
            print("Looked in:", _claude_projects_dir(), file=sys.stderr)
            sys.exit(1)

        force = getattr(args, "force", False)
        jkey = jsonl_path.name  # platform-independent: just filename
        cursor = _load_cursor(config.code_path)
        entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})

        if not force and entry["byte_offset"] > 0:
            # Check if there's new content
            current_size = jsonl_path.stat().st_size
            if entry["byte_offset"] >= current_size:
                print(f"Already fully processed: {jsonl_path.name}")
                print(f"  (cursor at byte {entry['byte_offset']}, file is {current_size} bytes)")
                print()
                print("  This may happen if:")
                print("  - The daemon already processed this session")
                print("  - The new conversation is in a different JSONL file")
                print()
                print("  Try: fiam post --force   (reprocess from start)")
                print("       fiam post --debug   (see which file is selected)")
                return

        if force:
            entry["byte_offset"] = 0

        print(f"Processing: {jsonl_path.name} (offset {entry['byte_offset']})")
        conversation, new_offset = _parse_jsonl_from(jsonl_path, entry["byte_offset"])

    if not conversation:
        print("Warning: no conversation turns found.", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(conversation)} turns parsed")
    result = post_session(config, conversation)

    # Update cursor AFTER successful processing (not before)
    if not test_file:
        cursor[jkey] = {"byte_offset": new_offset, "mtime": jsonl_path.stat().st_mtime}
        _save_cursor(config.code_path, cursor)

    print(f"post_session complete (session: {result['session_id']})")
    print(f"  events written: {result['events_written']}")
    print(f"  report: {result['report_path']}")


def cmd_session(args: argparse.Namespace) -> None:
    """Legacy: pre → claude → post. Use 'fiam start' instead."""
    import subprocess

    config = _build_config(args)
    from fiam.pipeline import pre_session, post_session

    pre_result = pre_session(config)
    print(f"Background ready: {config.background_path}\n")

    try:
        subprocess.run(["claude"], cwd=str(config.home_path), check=False)
    except FileNotFoundError:
        print("Error: 'claude' not found in PATH.", file=sys.stderr)
        sys.exit(1)

    jsonl_path = _find_latest_jsonl(config.home_path, debug=config.debug_mode)
    if jsonl_path is None:
        print("Warning: no JSONL found. Skipping post.", file=sys.stderr)
        return

    conversation = _parse_jsonl(jsonl_path)
    if not conversation:
        print("Warning: no turns found. Skipping post.", file=sys.stderr)
        return

    result = post_session(config, conversation, session_id=pre_result["session_id"])
    print(f"Done: {result['events_written']} events, report: {result['report_path']}")
