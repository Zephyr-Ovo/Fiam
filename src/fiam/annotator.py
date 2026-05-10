"""
DS-assisted annotation for fiam training data.

Two-phase workflow:
  Phase 1 (cuts): DS reads flow beats → proposes topic boundaries + event names.
  Phase 2 (edges): DS reads new + existing events → proposes relationships + weights.

Human reviews on dashboard before confirming.
Confirmed annotations feed CoSENT fine-tuning of bge-m3.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig
from fiam.prompts import load as load_prompt

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# LLM call (reuse pattern from edge_typer.py)
# ------------------------------------------------------------------

def _call_ds(prompt: str, config: FiamConfig, *, max_tokens: int = 4096) -> dict[str, Any]:
    """Call DS API and return parsed JSON."""
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
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        f"{config.graph_edge_base_url.rstrip('/')}/v1/chat/completions",
        data=body,
        headers=headers,
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())

    text = data["choices"][0]["message"]["content"].strip()

    # Extract JSON from possible markdown code fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)

    return json.loads(text)


# ------------------------------------------------------------------
# Phase 1: Flow → Binary cut list
# ------------------------------------------------------------------

def _format_beats(beats: list[dict]) -> str:
    """Format beats for prompt injection."""
    lines: list[str] = []
    for i, b in enumerate(beats):
        t = b.get("t", "?")
        if isinstance(t, str) and len(t) > 19:
            t = t[:19]
        src = b.get("channel") or b.get("actor", "?")
        text = b.get("text", "")
        if len(text) > 500:
            text = text[:497] + "..."
        lines.append(f"[{i}] {t} ({src}) {text}")
    return "\n".join(lines)


def propose_cuts(beats: list[dict], config: FiamConfig) -> list[int]:
    """Send beats to DS, get N-1 binary cut list.

    Args:
        beats: list of beat dicts with keys {t, text, actor, channel, ...}
        config: FiamConfig for API settings

    Returns:
        list of 0/1 with length len(beats)-1.
        cuts[i] = 1 means cut between beat[i] and beat[i+1].
    """
    n = len(beats)
    if n <= 1:
        return []

    prompt = load_prompt("annotate_flow").replace(
        "{{BEATS}}", _format_beats(beats)
    )
    result = _call_ds(prompt, config)

    cuts = result.get("cuts", [])
    # Validate: must be exactly N-1 ints of 0 or 1
    cuts = [int(bool(c)) for c in cuts]
    if len(cuts) < n - 1:
        cuts.extend([0] * (n - 1 - len(cuts)))
    elif len(cuts) > n - 1:
        cuts = cuts[: n - 1]

    return cuts


def cuts_to_segments(beats: list[dict], cuts: list[int]) -> list[dict]:
    """Convert binary cut list to segment ranges.

    Returns:
        [{"start": int, "end": int}]
    """
    segments: list[dict] = []
    start = 0
    for i, c in enumerate(cuts):
        if c == 1:
            segments.append({"start": start, "end": i})
            start = i + 1
    segments.append({"start": start, "end": len(beats) - 1})
    return segments


# ------------------------------------------------------------------
# Phase 2: Events → Edge proposals
# ------------------------------------------------------------------

def propose_edges(
    new_events: list[dict],
    existing_events: list[dict],
    config: FiamConfig,
) -> dict:
    """Send new + existing events to DS, get relationships.

    Args:
        new_events: [{"id": str, "time": str, "body": str}]
        existing_events: [{"id": str, "time": str, "body": str}]
        config: FiamConfig

        Returns:
                {
                    "names": {"seg_0": "high_information_name"},
                    "edges": [{"src": str, "dst": str, "type": str, "weight": float, "reason": str}]
                }
    """
    def _fmt(events: list[dict]) -> str:
        lines: list[str] = []
        for ev in events:
            body = ev.get("body", "").strip()
            if len(body) > 400:
                body = body[:397] + "..."
            lines.append(f"### {ev['id']} ({ev.get('time', '?')})")
            lines.append(body)
            lines.append("")
        return "\n".join(lines) if lines else "(none)"

    template = load_prompt("annotate_edges")
    prompt = (
        template
        .replace("{{NEW_EVENTS}}", _fmt(new_events))
        .replace("{{EXISTING_EVENTS}}", _fmt(existing_events))
    )

    result = _call_ds(prompt, config, max_tokens=8192)

    names = result.get("names", {})
    if not isinstance(names, dict):
        names = {}

    edges = result.get("edges", [])
    for e in edges:
        e["weight"] = float(e.get("weight", 0.5))
        e.setdefault("reason", "")

    return {"names": names, "edges": edges}


# ------------------------------------------------------------------
# Confirmation → Training data + Pool
# ------------------------------------------------------------------

def save_training_data(
    beats: list[dict],
    cuts: list[int],
    edges: list[dict],
    training_dir: Path,
    *,
    annotator: str = "ds+zephyr",
    beat_vectors: list | None = None,
    drift_cuts: list[int] | None = None,
) -> dict:
    """Save confirmed annotations as training data.

        Produces:
            - flow_cut_labels.jsonl: one record per beat gap with event/drift labels
            - beat_boundaries.jsonl: text-based boundary records (compat)
      - event_similarities.jsonl: event pairs with weights
            - batch_XXXX_vectors.npy + batch_XXXX_cuts.npy: two-column cut labels
    """
    training_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_iso()
    saved_boundaries = 0
    saved_pairs = 0

    segments = cuts_to_segments(beats, cuts)
    if drift_cuts is None:
        drift_cuts = [0] * max(0, len(beats) - 1)
    drift_cuts = [int(bool(c)) for c in drift_cuts[: max(0, len(beats) - 1)]]
    if len(drift_cuts) < max(0, len(beats) - 1):
        drift_cuts.extend([0] * (max(0, len(beats) - 1) - len(drift_cuts)))

    # --- Save vectors + cuts as npy for cut-head training ---
    if beat_vectors and any(v is not None for v in beat_vectors):
        import numpy as np
        # Find existing batch count
        existing = list(training_dir.glob("batch_*_cuts.npy"))
        batch_idx = len(existing)
        dim = None
        for v in beat_vectors:
            if v is not None:
                dim = len(v)
                break
        if dim:
            # Replace None vectors with zeros
            mat = np.zeros((len(beat_vectors), dim), dtype=np.float32)
            for i, v in enumerate(beat_vectors):
                if v is not None:
                    mat[i] = v
            cuts_arr = np.column_stack([
                np.array(cuts, dtype=np.int8),
                np.array(drift_cuts, dtype=np.int8),
            ])
            np.save(training_dir / f"batch_{batch_idx:04d}_vectors.npy", mat)
            np.save(training_dir / f"batch_{batch_idx:04d}_cuts.npy", cuts_arr)

    # --- Gap labels: event cut and drift cut are separate supervision targets ---
    labels_path = training_dir / "flow_cut_labels.jsonl"
    with open(labels_path, "a", encoding="utf-8") as f:
        for i in range(max(0, len(beats) - 1)):
            f.write(json.dumps({
                "beat_before": beats[i],
                "beat_after": beats[i + 1],
                "event_cut": int(bool(cuts[i])) if i < len(cuts) else 0,
                "drift_cut": int(bool(drift_cuts[i])) if i < len(drift_cuts) else 0,
                "annotator": annotator,
                "ts": ts,
            }, ensure_ascii=False) + "\n")

    # --- Beat boundaries (text-based, for provenance) ---
    boundaries_path = training_dir / "beat_boundaries.jsonl"
    with open(boundaries_path, "a", encoding="utf-8") as f:
        for seg in segments:
            start, end = seg["start"], seg["end"]
            seg_texts = [b.get("text", "") for b in beats[start:end + 1] if b.get("text")]
            if len(seg_texts) >= 2:
                f.write(json.dumps({
                    "type": "positive",
                    "beats": seg_texts,
                    "annotator": annotator,
                    "ts": ts,
                }, ensure_ascii=False) + "\n")
                saved_boundaries += 1

        # Negative pairs: beats on either side of each cut
        for i, c in enumerate(cuts):
            if c == 1:
                text_before = beats[i].get("text", "")
                text_after = beats[i + 1].get("text", "")
                if text_before and text_after:
                    f.write(json.dumps({
                        "type": "boundary",
                        "beat_before": text_before,
                        "beat_after": text_after,
                        "annotator": annotator,
                        "ts": ts,
                    }, ensure_ascii=False) + "\n")
                    saved_boundaries += 1

    # --- Event similarities ---
    if edges:
        pairs_path = training_dir / "event_similarities.jsonl"
        with open(pairs_path, "a", encoding="utf-8") as f:
            for e in edges:
                f.write(json.dumps({
                    "event_a": e["src"],
                    "event_b": e["dst"],
                    "edge_type": e.get("type", "semantic"),
                    "weight": e["weight"],
                    "reason": e.get("reason", ""),
                    "annotator": annotator,
                    "ts": ts,
                }, ensure_ascii=False) + "\n")
                saved_pairs += 1

    return {"saved_boundaries": saved_boundaries, "saved_pairs": saved_pairs}
