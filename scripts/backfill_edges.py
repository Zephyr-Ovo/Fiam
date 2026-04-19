#!/usr/bin/env python3
"""
Backfill script — generate edges + DS names for existing Pool events.

Run once on ISP after deploying the new graph_builder:
    cd ~/fiam-code && .venv/bin/python scripts/backfill_edges.py

By default runs DS naming. Use --skip-ds to only create temporal+semantic edges.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src/ is on the path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Pool edges + DS naming")
    parser.add_argument("--skip-ds", action="store_true", help="Skip DS API calls")
    parser.add_argument("--batch", type=int, default=6, help="Events per DS call")
    args = parser.parse_args()

    from fiam.config import FiamConfig
    from fiam.store.pool import Pool
    from fiam.retriever.graph_builder import build_edges

    toml_path = _ROOT / "fiam.toml"
    config = FiamConfig.from_toml(toml_path, _ROOT)
    pool = Pool(config.pool_dir, dim=config.embedding_dim)

    events = pool.load_events()
    n = len(events)
    print(f"Pool: {n} events, {pool.edge_count} existing edges")

    if n == 0:
        print("No events to process.")
        return

    # Process in batches for DS (to avoid token limits)
    all_ids = [ev.id for ev in events]
    batch_size = args.batch
    total_summary = {"temporal": 0, "semantic": 0, "ds": 0, "names_applied": 0}

    for i in range(0, n, batch_size):
        batch_ids = all_ids[i : i + batch_size]
        print(f"\nBatch {i // batch_size + 1}: events {i+1}-{min(i+batch_size, n)}")

        # For temporal+semantic, we pass ALL new indices since this is a backfill
        # (all events are "new" relative to the empty edge set)
        summary = build_edges(
            pool,
            batch_ids,
            config,
            skip_ds=args.skip_ds,
            context_window=4,
        )
        for k in total_summary:
            total_summary[k] += summary.get(k, 0)

        print(f"  temporal={summary['temporal']} semantic={summary['semantic']} "
              f"ds={summary['ds']} names={summary['names_applied']}")

    print(f"\nDone. Total edges: {pool.edge_count}")
    print(f"Summary: {total_summary}")


if __name__ == "__main__":
    main()
