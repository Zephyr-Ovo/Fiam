"""
Migrate legacy store/ data into the new pool format.

Reads:
  store/events/*.md          → pool/events/*.md (body only, no frontmatter)
  store/embeddings/*.npy     → pool/fingerprints.npy (stacked matrix)
  store/graph.jsonl          → pool/edge_index.npy + edge_attr.npy

Also generates:
  pool/events.jsonl          — event metadata index
  pool/cosine.npy            — pairwise similarity matrix

Usage:
  python scripts/migrate_to_pool.py [--code-path F:/fiam-code]

Idempotent: skips if pool/events.jsonl already exists.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone
from pathlib import Path

import numpy as np

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiam.config import FiamConfig
from fiam.store.home import HomeStore
from fiam.store.pool import Pool, Event
from fiam.store.graph_store import GraphStore


def _load_config(code_path: Path) -> FiamConfig:
    toml_path = code_path / "fiam.toml"
    if toml_path.exists():
        return FiamConfig.from_toml(toml_path, code_path)
    raise FileNotFoundError(f"fiam.toml not found at {toml_path}")


def migrate(code_path: Path, *, force: bool = False) -> None:
    config = _load_config(code_path)
    pool = Pool(config.pool_dir, dim=config.embedding_dim)

    if pool.meta_path.exists() and not force:
        print(f"pool/events.jsonl already exists. Use --force to overwrite.")
        return

    pool.ensure_dirs()

    # --- Load legacy data ---
    store = HomeStore(config)
    old_events = store.all_events()
    print(f"Legacy store: {len(old_events)} events")

    if not old_events:
        print("Nothing to migrate.")
        return

    # --- Gather embeddings ---
    embeddings: list[np.ndarray] = []
    id_to_idx: dict[str, int] = {}
    skipped_embed = 0

    for i, ev in enumerate(old_events):
        npy_path = config.store_dir / ev.embedding if ev.embedding else None
        if npy_path and npy_path.exists():
            vec = np.load(npy_path).astype(np.float32)
            # Handle dimension mismatch (old bge-m3 1024 vs new bge-zh 768)
            if vec.shape[-1] != config.embedding_dim:
                print(f"  SKIP embedding {ev.filename}: dim {vec.shape[-1]} != {config.embedding_dim}")
                skipped_embed += 1
                vec = np.zeros(config.embedding_dim, dtype=np.float32)
        else:
            skipped_embed += 1
            vec = np.zeros(config.embedding_dim, dtype=np.float32)

        idx = len(embeddings)
        embeddings.append(vec.flatten())
        id_to_idx[ev.filename] = idx

    # --- Build fingerprints matrix ---
    if embeddings:
        fp_matrix = np.stack(embeddings, axis=0)
    else:
        fp_matrix = np.empty((0, config.embedding_dim), dtype=np.float32)
    np.save(pool.fingerprints_path, fp_matrix)
    print(f"Fingerprints: {fp_matrix.shape} ({skipped_embed} zero-filled)")

    # --- Build events.jsonl + copy bodies ---
    events: list[Event] = []
    for ev in old_events:
        idx = id_to_idx.get(ev.filename, -1)
        new_ev = Event(
            id=ev.filename,
            t=ev.time if ev.time.tzinfo else ev.time.replace(tzinfo=timezone.utc),
            access_count=ev.access_count,
            fingerprint_idx=idx,
        )
        events.append(new_ev)
        # Copy body (strip frontmatter, just the content)
        pool.write_body(ev.filename, ev.body.strip())

    pool.save_events(events)
    print(f"Events index: {len(events)} entries")

    # --- Build cosine matrix ---
    pool._fingerprints = fp_matrix
    cos = pool.rebuild_cosine()
    print(f"Cosine matrix: {cos.shape}")

    # --- Migrate edges to PyG format ---
    graph_store = GraphStore(config.graph_jsonl_path)
    old_edges = graph_store.load()
    src_list, dst_list, type_list, weight_list = [], [], [], []
    edge_skip = 0
    for edge in old_edges:
        si = id_to_idx.get(edge.src)
        di = id_to_idx.get(edge.dst)
        if si is None or di is None:
            edge_skip += 1
            continue
        src_list.append(si)
        dst_list.append(di)
        type_list.append(Pool.edge_type_id(edge.type))
        weight_list.append(edge.weight)

    if src_list:
        ei = np.array([src_list, dst_list], dtype=np.int64)
        ea = np.column_stack([
            np.array(type_list, dtype=np.float32),
            np.array(weight_list, dtype=np.float32),
        ])
        np.save(pool.edge_index_path, ei)
        np.save(pool.edge_attr_path, ea)
    print(f"Edges: {len(src_list)} migrated, {edge_skip} skipped (missing node)")

    print("\nMigration complete.")
    print(f"  pool dir: {pool.root}")
    print(f"  events:   {len(events)}")
    print(f"  fingers:  {fp_matrix.shape}")
    print(f"  cosine:   {cos.shape}")
    print(f"  edges:    {len(src_list)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate legacy store to pool format")
    parser.add_argument("--code-path", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--force", action="store_true", help="Overwrite existing pool data")
    args = parser.parse_args()
    migrate(args.code_path, force=args.force)
