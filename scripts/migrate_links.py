"""
Migrate event links → graph.jsonl + strip links from frontmatter.

What this does:
  1. Read all events, extract links from YAML frontmatter
  2. Write deduplicated edges to store/graph.jsonl (via GraphStore)
  3. Strip the `links:` block from each event file

Safe to run multiple times — skips if graph.jsonl already has edges
and events have no links left.

Usage:
    python scripts/migrate_links.py                  # auto-detect from fiam.toml
    python scripts/migrate_links.py --store store/   # explicit store dir
    python scripts/migrate_links.py --dry-run        # preview only
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import frontmatter as fm
from fiam.store.formats import parse_event
from fiam.store.graph_store import GraphStore


def _load_event(path: Path):
    """Load an event file using python-frontmatter."""
    post = fm.load(str(path))
    return parse_event(
        frontmatter=dict(post.metadata),
        body=post.content,
        filename=path.stem,
    )


def _strip_links_from_file(path: Path) -> bool:
    """Remove the links: block from YAML frontmatter. Returns True if modified."""
    text = path.read_text(encoding="utf-8")

    # Match frontmatter block
    import re
    m = re.match(r"^---\n(.*?)\n---$(.*)", text, re.DOTALL | re.MULTILINE)
    if not m:
        return False

    yaml_block = m.group(1)
    rest = m.group(2)

    # Remove "links:" line + all following lines that are list items or indented continuations
    # Also handles orphaned list items (if links: was already removed by a broken prior run)
    # Pattern: optional "links:..." line, then "- id: ..." blocks with indented children
    new_yaml = re.sub(
        r"(?:^links:.*\n)?(?:^- id:.*\n(?:^ .*\n)*)*",
        "",
        yaml_block + "\n",
        flags=re.MULTILINE,
    ).rstrip("\n")

    # Also handle "links: []" on a single line
    new_yaml = re.sub(r"^links: \[\]\n?", "", new_yaml, flags=re.MULTILINE).rstrip("\n")

    if new_yaml == yaml_block:
        return False

    path.write_text(f"---\n{new_yaml}\n---{rest}", encoding="utf-8")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate event links → graph.jsonl")
    parser.add_argument("--store", help="Path to store/ directory (default: from fiam.toml)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    if args.store:
        store_dir = Path(args.store)
    else:
        code_path = Path(__file__).resolve().parent.parent
        store_dir = code_path / "store"

    events_dir = store_dir / "events"
    graph_path = store_dir / "graph.jsonl"

    if not events_dir.exists():
        print(f"[migrate] No events dir at {events_dir}")
        return

    event_files = sorted(events_dir.glob("*.md"))
    print(f"[migrate] Found {len(event_files)} event files in {events_dir}")

    # Step 1: First pass — fix any files broken by partial prior strip
    # (orphaned list items without a links: key)
    import re
    fixed = 0
    for f in event_files:
        text = f.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---$(.*)", text, re.DOTALL | re.MULTILINE)
        if not m:
            continue
        yaml_block = m.group(1)
        # Check for orphaned "- id:" lines outside any key
        if "\n- id:" in yaml_block and "links:" not in yaml_block:
            cleaned = re.sub(r"^- id:.*\n(?:^ .*\n)*", "", yaml_block + "\n", flags=re.MULTILINE).rstrip("\n")
            if cleaned != yaml_block:
                rest = m.group(2)
                f.write_text(f"---\n{cleaned}\n---{rest}", encoding="utf-8")
                fixed += 1
    if fixed:
        print(f"[migrate] Fixed {fixed} files with orphaned list items from prior run")

    # Step 2: Load events and count links
    events = []
    for f in event_files:
        try:
            events.append(_load_event(f))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")

    total_links = sum(len(ev.links) for ev in events)
    print(f"[migrate] Total links in frontmatter: {total_links}")

    # Step 2: Migrate links → graph.jsonl
    if total_links > 0:
        if not args.dry_run:
            gs = GraphStore.migrate_from_events(events, graph_path)
            print(f"[migrate] Wrote {gs.edge_count()} edges to {graph_path}")
        else:
            print(f"[migrate] DRY RUN: would write edges to {graph_path}")
    else:
        existing = GraphStore(graph_path).edge_count() if graph_path.exists() else 0
        print(f"[migrate] No links in frontmatter. graph.jsonl has {existing} edges.")

    # Step 3: Strip links from frontmatter
    stripped = 0
    for f in event_files:
        if args.dry_run:
            text = f.read_text(encoding="utf-8")
            if "links:" in text or "\n- id:" in text.split("---")[1] if text.count("---") >= 2 else False:
                stripped += 1
        else:
            if _strip_links_from_file(f):
                stripped += 1

    label = "DRY RUN: would strip" if args.dry_run else "Stripped"
    print(f"[migrate] {label} links from {stripped}/{len(event_files)} files")


if __name__ == "__main__":
    main()
