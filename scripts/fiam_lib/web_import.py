"""Import conversations from Claude Web export (conversations.json)."""

from __future__ import annotations

import argparse
from pathlib import Path

from fiam_lib.core import _build_config


def cmd_import(args: argparse.Namespace) -> None:
    """Import a Claude Web conversations.json into fiam memory."""
    config = _build_config(args)
    from fiam.adapter import get_adapter
    from fiam.pipeline import post_session

    source = Path(args.file).resolve()
    if not source.exists():
        import sys
        print(f"  File not found: {source}", file=sys.stderr)
        sys.exit(1)

    adapter = get_adapter("claude_web")
    conversations = adapter.parse_multi(source)

    if not conversations:
        print("  No conversations found in file.")
        return

    print(f"\n  Found {len(conversations)} conversation(s) in {source.name}\n")

    total_events = 0
    for i, (name, turns) in enumerate(conversations, 1):
        if not turns:
            print(f"  [{i}/{len(conversations)}] {name[:60]}: 0 turns, skipped")
            continue
        print(f"  [{i}/{len(conversations)}] {name[:60]}: {len(turns)} turns", end="", flush=True)
        try:
            r = post_session(config, turns)
            n = r["events_written"]
            total_events += n
            print(f" → {n} events")
        except Exception as e:
            print(f" → error: {e}")

    print(f"\n  Done. {total_events} events stored from {len(conversations)} conversation(s).\n")
