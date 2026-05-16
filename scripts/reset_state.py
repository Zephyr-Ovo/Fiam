"""Reset the AI's runtime state to a clean slate.

Removes:
- store/flow.jsonl (event flow)
- store/pool/* (pool vectors and event metadata)
- store/features/* (frozen beat embeddings)
- store/transcripts/* (shared runtime message transcripts)
- store/annotation_state.json (process progress)
- transcript/*.jsonl (Favilla chat history)
- pending_recall.md (one-turn recall handoff)
- session_state.json (rollover counter)
- self/active_session.json (CC session id)
- self/state.md, self/personality.md, self/journal/* (persona files)
- ai_state.json (current state)
- app_studio/state.json (legacy Studio editor state)
- pending_external.txt and lock files
- store/held.jsonl (private held-turn read model)

Preserves:
- fiam.toml + fiam.toml.example
- src/, scripts/, channels/, plugins/, packages/, deploy/
- self/identity.md, self/impressions.md, self/lessons.md, self/commitments.md
  (system-prompt-level identity files — unless --wipe-identity is set)
- studio/ (the new Studio v0.1 vault — managed via /studio endpoints)

Refuses to run unless --yes is passed. Refuses to run on a path that does
not look like a fiam home/code workspace.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _safe_unlink(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            return False
    except OSError as exc:
        print(f"  [warn] {path}: {exc}", file=sys.stderr)
        return False
    return True


def _wipe(home: Path, store: Path, *, wipe_identity: bool) -> list[str]:
    actions: list[str] = []

    targets_under_store = [
        store / "flow.jsonl",
        store / "pool",
        store / "features",
        store / "transcripts",
        store / "held.jsonl",
        store / "annotation_state.json",
        store / "wearable",
    ]
    for tgt in targets_under_store:
        if _safe_unlink(tgt):
            actions.append(f"removed {tgt}")

    targets_under_home = [
        home / "transcript",
        home / "pending_recall.md",
        home / "pending_recall.processing",
        home / "session_state.json",
        home / "pending_external.txt",
        home / "pending_external.processing",
        home / "ai_state.json",
        home / "app_cuts.jsonl",
        home / "app_studio",
        home / "inbox" / "processed",
        home / "self" / "active_session.json",
        home / "self" / "state.md",
        home / "self" / "journal",
        home / "self" / "retired",
        home / "self" / "ai_state.json",
        home / "self" / "daily_summary.md",
    ]
    if wipe_identity:
        targets_under_home.extend([
            home / "self" / "personality.md",
            home / "self" / "identity.md",
            home / "self" / "impressions.md",
            home / "self" / "lessons.md",
            home / "self" / "commitments.md",
            home / "self" / "goals.md",
        ])
    for tgt in targets_under_home:
        if _safe_unlink(tgt):
            actions.append(f"removed {tgt}")

    # Recreate empty fundamentals so the daemon can boot
    (home / "self").mkdir(parents=True, exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    (store / "pool").mkdir(parents=True, exist_ok=True)

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", required=True, help="AI home directory (e.g. /home/live)")
    parser.add_argument("--store", required=True, help="Code-side store/ directory (e.g. /home/fiet/fiam-code/store)")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive action")
    parser.add_argument("--wipe-identity", action="store_true", help="Also wipe self/personality, identity, impressions, lessons, commitments, goals")
    args = parser.parse_args()

    home = Path(args.home).expanduser().resolve()
    store = Path(args.store).expanduser().resolve()

    if not home.exists():
        print(f"home not found: {home}", file=sys.stderr)
        return 2
    if not store.exists():
        print(f"store not found: {store}", file=sys.stderr)
        return 2

    # Sanity guard: refuse to wipe paths that look like the wrong thing.
    if home == Path("/") or str(home) in {"/home", "/root"}:
        print(f"refusing to wipe suspicious home: {home}", file=sys.stderr)
        return 2
    if store == Path("/") or store.name not in {"store", "store-test"}:
        print(f"refusing to wipe suspicious store (must be named 'store'): {store}", file=sys.stderr)
        return 2

    print(f"home  = {home}")
    print(f"store = {store}")
    print(f"wipe_identity = {args.wipe_identity}")
    print()

    if not args.yes:
        print("Dry run (no --yes). Would remove:")
        for tgt in [
            store / "flow.jsonl",
            store / "pool",
            store / "features",
            store / "transcripts",
            store / "held.jsonl",
            store / "annotation_state.json",
            home / "transcript",
            home / "pending_recall.md",
            home / "pending_recall.processing",
            home / "session_state.json",
            home / "self" / "active_session.json",
            home / "ai_state.json",
            home / "app_cuts.jsonl",
            home / "app_studio",
        ]:
            print(f"  {tgt}")
        if args.wipe_identity:
            print("  (and self/{personality,identity,impressions,lessons,commitments,goals}.md)")
        print()
        print("Re-run with --yes to actually delete.")
        return 0

    actions = _wipe(home, store, wipe_identity=args.wipe_identity)
    for line in actions:
        print(line)
    print()
    print(f"done — {len(actions)} target(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
