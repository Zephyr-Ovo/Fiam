"""
Graph builder — edge generation + DS naming for Pool events.

After new events are ingested into Pool, this module:
  1. Temporal edges: adjacent events within 10 min → weight decays linearly
  2. Semantic edges: cosine.npy > threshold → weight = similarity
  3. DS naming + typed edges: LLM call for causal/remind/contrast/elaboration
     edges plus meaningful names and tags

All edges are written directly to Pool's PyG format (edge_index + edge_attr).
Replaces the old temporal.py + semantic_link.py + edge_typer.py pipeline
that wrote to graph.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np

from fiam.config import FiamConfig
from fiam.store.pool import Pool

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

_TEMPORAL_MAX_GAP = 600  # 10 minutes in seconds
_SEMANTIC_THRESHOLD = 0.82


# ------------------------------------------------------------------
# 1. Temporal edges
# ------------------------------------------------------------------

def _temporal_edges(
    pool: Pool,
    new_indices: set[int],
) -> list[tuple[int, int, int, float]]:
    """Create temporal edges between chronologically adjacent events.

    Only considers pairs where at least one event is in new_indices.
    Returns list of (src_idx, dst_idx, type_id, weight).
    """
    events = pool.load_events()
    if len(events) < 2:
        return []

    # Sort by time, keep original fingerprint_idx
    sorted_evs = sorted(events, key=lambda e: e.t)
    edges: list[tuple[int, int, int, float]] = []

    for i in range(len(sorted_evs) - 1):
        a, b = sorted_evs[i], sorted_evs[i + 1]
        a_idx, b_idx = a.fingerprint_idx, b.fingerprint_idx
        if a_idx < 0 or b_idx < 0:
            continue
        # Only create if at least one is new
        if a_idx not in new_indices and b_idx not in new_indices:
            continue

        gap = abs((b.t - a.t).total_seconds())
        if gap > _TEMPORAL_MAX_GAP:
            continue
        weight = max(0.1, 1.0 - gap / _TEMPORAL_MAX_GAP)
        type_id = Pool.EDGE_TYPES["temporal"]
        edges.append((a_idx, b_idx, type_id, weight))

    return edges


# ------------------------------------------------------------------
# 2. Semantic edges (from cosine.npy)
# ------------------------------------------------------------------

def _semantic_edges(
    pool: Pool,
    new_indices: set[int],
    threshold: float = _SEMANTIC_THRESHOLD,
) -> list[tuple[int, int, int, float]]:
    """Create semantic edges from cosine similarity matrix.

    For each new event, find all existing events above threshold.
    Returns list of (src_idx, dst_idx, type_id, weight).
    """
    cos = pool.load_cosine()
    n = cos.shape[0]
    if n < 2:
        return []

    type_id = Pool.EDGE_TYPES["semantic"]
    edges: list[tuple[int, int, int, float]] = []

    for idx in new_indices:
        if idx >= n:
            continue
        for other in range(n):
            if other == idx:
                continue
            sim = float(cos[idx, other])
            if sim > threshold:
                edges.append((idx, other, type_id, sim))

    return edges


# ------------------------------------------------------------------
# 3. DS naming + typed edges (LLM)
# ------------------------------------------------------------------

def _call_ds(prompt: str, config: FiamConfig) -> dict[str, Any]:
    """Call DeepSeek API for edge typing + naming."""
    import urllib.request

    api_key = os.environ.get(config.graph_edge_api_key_env, "")
    if not api_key:
        logger.warning("Missing %s — skipping DS naming", config.graph_edge_api_key_env)
        return {}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps({
        "model": config.graph_edge_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2048,
    }).encode()

    req = urllib.request.Request(
        f"{config.graph_edge_base_url.rstrip('/')}/v1/chat/completions",
        data=body,
        headers=headers,
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    text = data["choices"][0]["message"]["content"].strip()

    # Extract JSON from possible markdown code fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)

    return json.loads(text)


def _format_events_for_ds(events: list[Pool.Event], pool: Pool) -> str:
    """Format events into the prompt block for DS."""
    # Actually Pool.Event doesn't have class-level reference, use module Event
    lines: list[str] = []
    for ev in events:
        body = pool.read_body(ev.id).strip()
        if len(body) > 400:
            body = body[:397] + "..."
        time_str = ev.t.strftime("%Y-%m-%d %H:%M")
        lines.append(f"### {ev.id} ({time_str})")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def _ds_name_and_type(
    new_events: list,
    context_events: list,
    pool: Pool,
    config: FiamConfig,
) -> tuple[list[tuple[int, int, int, float]], dict[str, str], dict[str, list[str]]]:
    """Call DS for typed edges, names, and tags.

    Returns (edges_tuples, names_dict, tags_dict).
    edges_tuples: list of (src_idx, dst_idx, type_id, weight)
    names_dict: {old_id: new_name}
    tags_dict: {event_id: [tag, ...]}
    """
    from fiam.prompts import load as load_prompt

    all_ev = list(new_events)
    existing_ids = {e.id for e in all_ev}
    for ev in context_events:
        if ev.id not in existing_ids and len(all_ev) < 12:
            all_ev.append(ev)

    if len(all_ev) < 2:
        # Single event — just name it
        if len(all_ev) == 1:
            # Still call DS for naming even with 1 event
            pass
        else:
            return [], {}, {}

    events_block = _format_events_for_ds(all_ev, pool)
    prompt_template = load_prompt("edge_typing")
    prompt = prompt_template.format(events_block=events_block)

    try:
        result = _call_ds(prompt, config)
    except Exception as e:
        logger.error("DS naming failed: %s", e)
        return [], {}, {}

    if not result:
        return [], {}, {}

    # Build id → fingerprint_idx mapping
    id_to_idx = {}
    for ev in all_ev:
        id_to_idx[ev.id] = ev.fingerprint_idx

    valid_ids = {ev.id for ev in all_ev}

    # Parse edges
    edges: list[tuple[int, int, int, float]] = []
    for raw_edge in result.get("edges", []):
        src_id = raw_edge.get("from", "")
        dst_id = raw_edge.get("to", "")
        if src_id not in valid_ids or dst_id not in valid_ids:
            continue
        src_idx = id_to_idx.get(src_id, -1)
        dst_idx = id_to_idx.get(dst_id, -1)
        if src_idx < 0 or dst_idx < 0:
            continue

        edge_type = raw_edge.get("type", "remind")
        type_id = Pool.edge_type_id(edge_type)

        raw_w = raw_edge.get("weight", 0.7)
        try:
            w = min(1.0, max(0.1, float(raw_w)))
        except (TypeError, ValueError):
            w = 0.7

        edges.append((src_idx, dst_idx, type_id, w))

    # Parse names
    names: dict[str, str] = {}
    _SAFE_NAME = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]+$")
    for old_id, new_name in result.get("names", {}).items():
        if old_id not in valid_ids:
            continue
        new_name = str(new_name).strip()
        if not new_name or not _SAFE_NAME.match(new_name):
            continue
        if len(new_name) > 60:
            new_name = new_name[:60]
        names[old_id] = new_name

    # Parse tags (stored as future-proofing, not used in edges)
    tags: dict[str, list[str]] = {}
    _SAFE_TAG = re.compile(r"^[a-z0-9_\u4e00-\u9fff]+$")
    for ev_id, tag_list in result.get("tags", {}).items():
        if ev_id not in valid_ids or not isinstance(tag_list, list):
            continue
        clean = []
        for t in tag_list:
            t = str(t).strip().lower()
            if t and _SAFE_TAG.match(t) and len(t) <= 40:
                clean.append(t)
        if clean:
            tags[ev_id] = clean[:5]

    return edges, names, tags


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def build_edges(
    pool: Pool,
    new_event_ids: list[str],
    config: FiamConfig,
    *,
    skip_ds: bool = False,
    context_window: int = 6,
) -> dict[str, Any]:
    """Generate all edge types for newly ingested events.

    Args:
        pool: The Pool instance.
        new_event_ids: IDs of events just created.
        config: FiamConfig for DS API settings.
        skip_ds: If True, only create temporal+semantic edges (no LLM call).
        context_window: Number of recent context events to include for DS.

    Returns:
        Summary dict with counts: {temporal, semantic, ds, names_applied}.
    """
    events = pool.load_events()
    id_to_event = {ev.id: ev for ev in events}
    new_events = [id_to_event[eid] for eid in new_event_ids if eid in id_to_event]

    if not new_events:
        return {"temporal": 0, "semantic": 0, "ds": 0, "names_applied": 0}

    new_indices = {ev.fingerprint_idx for ev in new_events if ev.fingerprint_idx >= 0}

    # Collect all edge tuples: (src_idx, dst_idx, type_id, weight)
    all_edges: list[tuple[int, int, int, float]] = []

    # 1. Temporal
    temporal = _temporal_edges(pool, new_indices)
    all_edges.extend(temporal)

    # 2. Semantic
    semantic = _semantic_edges(pool, new_indices)
    all_edges.extend(semantic)

    # 3. DS naming + typed edges
    ds_edges: list[tuple[int, int, int, float]] = []
    names: dict[str, str] = {}
    tags: dict[str, list[str]] = {}
    if not skip_ds:
        # Context: recent events not in new_events
        sorted_events = sorted(events, key=lambda e: e.t, reverse=True)
        context_events = [
            ev for ev in sorted_events
            if ev.id not in {e.id for e in new_events}
        ][:context_window]

        ds_edges, names, tags = _ds_name_and_type(
            new_events, context_events, pool, config,
        )
        all_edges.extend(ds_edges)

    # Deduplicate edges (same src+dst → keep highest weight)
    seen: dict[tuple[int, int], tuple[int, float]] = {}
    for src, dst, tid, w in all_edges:
        key = (src, dst)
        if key not in seen or w > seen[key][1]:
            seen[key] = (tid, w)

    # Write to Pool
    if seen:
        src_list = [k[0] for k in seen]
        dst_list = [k[1] for k in seen]
        tid_list = [v[0] for v in seen.values()]
        w_list = [v[1] for v in seen.values()]
        pool.add_edges_batch(src_list, dst_list, tid_list, w_list)

    # Apply names (rename events)
    names_applied = 0
    for old_id, new_name in names.items():
        try:
            pool.rename_event(old_id, new_name)
            names_applied += 1
            logger.info("renamed %s → %s", old_id, new_name)
        except Exception as e:
            logger.warning("rename %s → %s failed: %s", old_id, new_name, e)

    summary = {
        "temporal": len(temporal),
        "semantic": len(semantic),
        "ds": len(ds_edges),
        "names_applied": names_applied,
    }
    logger.info(
        "graph_builder: %d temporal, %d semantic, %d DS edges; %d names applied",
        summary["temporal"], summary["semantic"], summary["ds"], summary["names_applied"],
    )
    return summary
