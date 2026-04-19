"""
Reprocess CC JSONL sessions into Pool via Conductor.

- Reads all CC JSONL files from ~/.claude/projects/<home>/
- Runs each through Conductor.ingest_cc_output() → gorge → pool
- Does NOT write to flow.jsonl (skip_flow=True)
- Clears old pool data first (--force required)

Usage:
  python scripts/reprocess_pool.py --force [--code-path ~/fiam-code]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Allow running from repo root — src/ must come BEFORE scripts/ to avoid
# scripts/fiam.py shadowing the fiam package.
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here.parent / "src"))

from fiam.config import FiamConfig
from fiam.store.pool import Pool
from fiam.conductor import Conductor
from fiam.retriever.embedder import Embedder


def _load_config(code_path: Path) -> FiamConfig:
    toml_path = code_path / "fiam.toml"
    if toml_path.exists():
        return FiamConfig.from_toml(toml_path, code_path)
    raise FileNotFoundError(f"fiam.toml not found at {toml_path}")


def _find_jsonl_dir(config: FiamConfig) -> Path:
    """Find the CC JSONL directory for this home."""
    import re
    projects = Path.home() / ".claude" / "projects"
    sanitized = re.sub(r"[^\w.-]", "-", str(config.home_path))
    d = projects / sanitized
    if d.is_dir():
        return d
    # Try all subdirs for a match
    for sub in projects.iterdir():
        if sub.is_dir() and str(config.home_path).replace("/", "-").replace("\\", "-") in sub.name:
            return sub
    raise FileNotFoundError(f"JSONL dir not found under {projects} for home={config.home_path}")


def reprocess(code_path: Path, *, force: bool = False) -> None:
    config = _load_config(code_path)
    pool_dir = config.pool_dir

    if pool_dir.exists() and not force:
        print("Pool already exists. Use --force to clear and reprocess.")
        return

    # Clear old pool (keep captures — 04*_ prefixed events from /api/capture)
    events_dir = pool_dir / "events"
    capture_backup: dict[str, str] = {}
    if events_dir.is_dir():
        for f in events_dir.iterdir():
            if f.name.startswith("04") and f.suffix == ".md":
                capture_backup[f.name] = f.read_text(encoding="utf-8")

    # Wipe pool
    for f in pool_dir.glob("*.npy"):
        f.unlink()
    for f in pool_dir.glob("*.jsonl"):
        f.unlink()
    if events_dir.is_dir():
        shutil.rmtree(events_dir)

    # Re-create pool
    pool = Pool(pool_dir, dim=config.embedding_dim)
    embedder = Embedder(config)

    # Restore capture events (they're not from CC sessions)
    if capture_backup:
        events_dir.mkdir(parents=True, exist_ok=True)
        for name, body in capture_backup.items():
            (events_dir / name).write_text(body, encoding="utf-8")
        print(f"Restored {len(capture_backup)} capture events")

    # Create conductor with a /dev/null flow path (don't write flow.jsonl)
    flow_path = pool_dir / "_reprocess_flow.jsonl"  # temp, will delete
    conductor = Conductor(
        pool=pool,
        embedder=embedder,
        config=config,
        flow_path=flow_path,
        recall_path=pool_dir / "_reprocess_recall.md",  # temp
    )

    # Find CC JSONL files
    jsonl_dir = _find_jsonl_dir(config)
    jsonl_files = sorted(jsonl_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    print(f"Found {len(jsonl_files)} JSONL files in {jsonl_dir}")

    total_beats = 0
    total_events = 0

    for jf in jsonl_files:
        results, _ = conductor.ingest_cc_output(jf, byte_offset=0)
        n_beats = len(results)
        n_events = sum(1 for r in results if r is not None)
        total_beats += n_beats
        total_events += n_events
        if n_beats:
            print(f"  {jf.name[:20]}…  beats={n_beats}  events={n_events}")

    # Flush remaining buffer
    final = conductor.flush_all()
    total_events += len(final)
    if final:
        print(f"  flush: {len(final)} final event(s)")

    # Clean up temp files
    flow_path.unlink(missing_ok=True)
    (pool_dir / "_reprocess_recall.md").unlink(missing_ok=True)

    # Show result
    events = pool.load_events()
    print(f"\nReprocess complete:")
    print(f"  beats:  {total_beats}")
    print(f"  events: {total_events} (pool has {len(events)} total)")
    fingerprints = pool.load_fingerprints()
    print(f"  fingerprints: {fingerprints.shape}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reprocess CC sessions into Pool")
    parser.add_argument("--code-path", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--force", action="store_true", help="Clear and reprocess")
    args = parser.parse_args()
    reprocess(args.code_path, force=args.force)
