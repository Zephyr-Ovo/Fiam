"""
LLM-based edge typing and event naming.

After temporal + semantic edges are created, this module optionally calls
an LLM (DeepSeek by default) to:
  1. Classify edge types (cause/remind/contrast/elaboration) with reasons
  2. Suggest meaningful event names (replacing ev_0408_001 style IDs)

Cost: ~$0.0003 per call (6 events, DeepSeek V3 pricing as of 2026-04).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from fiam.store.graph_store import Edge


# ------------------------------------------------------------------
# Prompts — loaded from src/fiam/prompts/edge_typing.txt
# ------------------------------------------------------------------

_PROMPT_CACHE: str | None = None


def _get_prompt_template() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        from fiam.prompts import load
        _PROMPT_CACHE = load("edge_typing")
    return _PROMPT_CACHE


# ------------------------------------------------------------------
# API call
# ------------------------------------------------------------------

def _call_llm(prompt: str, config: FiamConfig) -> dict[str, Any]:
    """Call the configured LLM API and return parsed JSON response."""
    import urllib.request

    api_key = os.environ.get(config.graph_edge_api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing env var: {config.graph_edge_api_key_env}")

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


# ------------------------------------------------------------------
# Event formatting
# ------------------------------------------------------------------

def _format_events_block(events: list[EventRecord]) -> str:
    """Format events into the prompt block."""
    lines: list[str] = []
    for ev in events:
        body = ev.body.strip()
        if len(body) > 400:
            body = body[:397] + "..."
        time_str = ev.time.strftime("%Y-%m-%d %H:%M")
        lines.append(f"### {ev.event_id} ({time_str}, v={ev.valence:+.2f} a={ev.arousal:.2f})")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def type_edges_and_name(
    new_events: list[EventRecord],
    config: FiamConfig,
    *,
    context_events: list[EventRecord] | None = None,
) -> tuple[list[Edge], dict[str, str], dict[str, float]]:
    """Call LLM to type edges and name events.

    Args:
        new_events: The newly created events to process.
        context_events: Optional additional events for richer context.

    Returns:
        (edges, names, new_types) where edges is a list of Edge objects,
        names maps old event_id → suggested name, and new_types maps
        any newly discovered edge type → importance weight.

    Raises:
        RuntimeError: If the LLM call fails (DS naming is mandatory).
    """

    # Combine new + context events (cap total to avoid token bloat)
    all_ev = list(new_events)
    if context_events:
        existing_ids = {e.event_id for e in all_ev}
        for ev in context_events:
            if ev.event_id not in existing_ids and len(all_ev) < 12:
                all_ev.append(ev)

    if len(all_ev) < 2:
        # Need at least 2 events to find relationships
        if len(all_ev) == 1:
            return [], {}, {}
        return [], {}, {}

    events_block = _format_events_block(all_ev)
    prompt = _get_prompt_template().format(events_block=events_block)

    result = _call_llm(prompt, config)

    # Parse edges
    edges: list[Edge] = []
    known_types = {"cause", "remind", "contrast", "elaboration", "causal", "semantic", "temporal"}
    new_types: dict[str, float] = {}  # type → importance for newly discovered types
    valid_ids = {ev.event_id for ev in all_ev}
    for raw_edge in result.get("edges", []):
        src = raw_edge.get("from", "")
        dst = raw_edge.get("to", "")
        if src not in valid_ids or dst not in valid_ids:
            continue
        edge_type = raw_edge.get("type", "remind")
        # Accept new types from DS but track them
        if edge_type not in known_types:
            new_types[edge_type] = min(1.0, max(0.1, float(raw_edge.get("importance", 0.5))))
        # Parse weight from DS (default 0.7, clamp to [0.1, 1.0])
        raw_w = raw_edge.get("weight", 0.7)
        try:
            w = min(1.0, max(0.1, float(raw_w)))
        except (TypeError, ValueError):
            w = 0.7
        edges.append(Edge(
            src=src,
            dst=dst,
            type=edge_type,
            weight=w,
            reason=raw_edge.get("reason", ""),
        ))

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

    return edges, names, new_types
