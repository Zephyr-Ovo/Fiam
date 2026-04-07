"""
Topic segmentation benchmark — compare approaches on conversations.json.

Approaches tested:
  1. cosine_threshold  — current fiam: cut when sim < threshold (binary)
  2. depth_score       — TextTiling-inspired: cut at local minima of
                         smoothed similarity, using relative depth not
                         absolute threshold
  3. cross_segment     — cross-encoder model: is this pair of segments
                         about the same topic? (supervised signal)

Usage:
  uv run python scripts/bench_segment.py
"""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ── Load conversations ──────────────────────────────────────────────

CONV_PATH = Path(__file__).resolve().parent.parent / "test_vault" / "fixtures" / "conversations.json"

def load_conversations() -> list[tuple[str, list[dict]]]:
    """Return [(name, [messages])] with ≥4 messages."""
    raw = json.loads(CONV_PATH.read_text("utf-8"))
    out = []
    for conv in raw:
        msgs = conv.get("chat_messages", [])
        if len(msgs) < 4:
            continue
        name = conv.get("name", "(unnamed)")
        # extract turns
        turns = []
        for m in msgs:
            sender = m.get("sender", "")
            if sender == "human":
                role = "user"
            elif sender == "assistant":
                role = "assistant"
            else:
                continue
            # extract text from content blocks
            text_parts = []
            for block in m.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        text_parts.append(t)
            if not text_parts:
                t = m.get("text", "").strip()
                if t:
                    text_parts.append(t)
            text = "\n".join(text_parts)
            if text:
                turns.append({"role": role, "text": text})
        if len(turns) >= 4:
            out.append((name, turns))
    return out


# ── Pair turns into user+assistant exchanges ────────────────────────

def make_pairs(turns: list[dict]) -> list[str]:
    """Group into user+assistant pairs, return combined text per pair."""
    pairs = []
    i = 0
    while i < len(turns):
        if turns[i]["role"] == "user":
            user_text = turns[i]["text"]
            asst_text = ""
            if i + 1 < len(turns) and turns[i + 1]["role"] == "assistant":
                asst_text = turns[i + 1]["text"]
                i += 2
            else:
                i += 1
            pairs.append(f"[user] {user_text}\n[assistant] {asst_text}")
        else:
            # orphan assistant message
            pairs.append(f"[assistant] {turns[i]['text']}")
            i += 1
    return pairs


# ══════════════════════════════════════════════════════════════════════
# Method 1: Cosine Threshold (current fiam approach)
# ══════════════════════════════════════════════════════════════════════

def segment_cosine_threshold(
    embeddings: np.ndarray,
    threshold: float = 0.75,
) -> list[int]:
    """Return boundary indices where we cut. Index i means cut AFTER pair i."""
    boundaries = []
    for i in range(len(embeddings) - 1):
        sim = _cosine(embeddings[i], embeddings[i + 1])
        if sim < threshold:
            boundaries.append(i)
    return boundaries


# ══════════════════════════════════════════════════════════════════════
# Method 2: TextTiling Depth Score
# ══════════════════════════════════════════════════════════════════════

def segment_depth_score(
    embeddings: np.ndarray,
    window: int = 2,
    depth_cutoff: float = 0.1,
) -> list[int]:
    """TextTiling-style depth scoring.

    For each gap i, compute similarity between the average of the
    `window` embeddings before and after the gap. Then find local
    minima in the similarity curve. Cut at gaps whose depth score
    (how much the similarity dips relative to its neighbors) exceeds
    the cutoff.

    This is fundamentally different from threshold-based:
    - It's RELATIVE: a drop from 0.9 to 0.7 is a cut even though 0.7
      is "high" in absolute terms.
    - It handles gradual topic drift gracefully.
    """
    n = len(embeddings)
    if n < 3:
        return []

    # Step 1: Compute block similarities (average of window embeddings)
    sims = np.zeros(n - 1)
    for i in range(n - 1):
        left_start = max(0, i - window + 1)
        right_end = min(n, i + 1 + window)
        left_block = embeddings[left_start:i + 1].mean(axis=0)
        right_block = embeddings[i + 1:right_end].mean(axis=0)
        sims[i] = _cosine(left_block, right_block)

    # Step 2: Compute depth score at each gap
    # Depth at gap i = (left_peak - sim[i]) + (right_peak - sim[i])
    # where left_peak = max similarity going left from i until we go up
    # and right_peak = max similarity going right from i until we go up
    depths = np.zeros(len(sims))
    for i in range(len(sims)):
        # Find left peak
        left_peak = sims[i]
        for j in range(i - 1, -1, -1):
            if sims[j] >= left_peak:
                left_peak = sims[j]
            else:
                break
        # Find right peak
        right_peak = sims[i]
        for j in range(i + 1, len(sims)):
            if sims[j] >= right_peak:
                right_peak = sims[j]
            else:
                break
        depths[i] = (left_peak - sims[i]) + (right_peak - sims[i])

    # Step 3: Cut at local maxima of depth that exceed cutoff
    boundaries = []
    for i in range(len(depths)):
        if depths[i] < depth_cutoff:
            continue
        # Check if this is a local maximum of depth
        is_peak = True
        if i > 0 and depths[i - 1] > depths[i]:
            is_peak = False
        if i < len(depths) - 1 and depths[i + 1] > depths[i]:
            is_peak = False
        if is_peak:
            boundaries.append(i)

    return boundaries


# ══════════════════════════════════════════════════════════════════════
# Method 3: Window-Diff / Coherence Gradient
# ══════════════════════════════════════════════════════════════════════

def segment_gradient(
    embeddings: np.ndarray,
    window: int = 3,
    z_threshold: float = 1.5,
) -> list[int]:
    """Cut where the coherence drops sharply relative to local variance.

    Compute a moving-window coherence score, take the first derivative
    (gradient), and cut where the gradient is more than z_threshold
    standard deviations below the mean — i.e. unusually sharp drops.

    This is adaptive: in a highly coherent conversation, even small
    drops get detected. In a noisy one, only large drops matter.
    """
    n = len(embeddings)
    if n < 4:
        return []

    # Coherence: cosine similarity between consecutive pairs
    raw_sims = np.array([
        _cosine(embeddings[i], embeddings[i + 1])
        for i in range(n - 1)
    ])

    # Smooth with moving average
    if len(raw_sims) < window:
        smoothed = raw_sims
    else:
        kernel = np.ones(window) / window
        # Pad to keep same length
        padded = np.pad(raw_sims, (window // 2, window - 1 - window // 2), mode="edge")
        smoothed = np.convolve(padded, kernel, mode="valid")

    # Gradient (first derivative of smoothed coherence)
    gradient = np.diff(smoothed)

    if len(gradient) == 0:
        return []

    # Z-score the gradient: cut where it's unusually negative
    mean_g = gradient.mean()
    std_g = gradient.std()
    if std_g < 1e-9:
        return []

    z_scores = (gradient - mean_g) / std_g

    boundaries = []
    for i in range(len(z_scores)):
        if z_scores[i] < -z_threshold:
            boundaries.append(i)

    return boundaries


# ══════════════════════════════════════════════════════════════════════
# Method 4: Supervised cross-encoder (if available)
# ══════════════════════════════════════════════════════════════════════

_CROSS_ENCODER = None

def _load_cross_encoder():
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    try:
        from sentence_transformers import CrossEncoder
        # Small cross-encoder for semantic similarity
        _CROSS_ENCODER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        return _CROSS_ENCODER
    except Exception as e:
        print(f"  [cross-encoder not available: {e}]")
        return None


def segment_cross_encoder(
    pairs_text: list[str],
    threshold: float = 0.3,
) -> list[int]:
    """Use a cross-encoder to judge if consecutive pairs are same topic.

    Cross-encoder sees the actual text of both segments (not just
    embeddings). This captures semantic nuance that bi-encoders miss.
    """
    model = _load_cross_encoder()
    if model is None:
        return []

    boundaries = []
    input_pairs = []
    for i in range(len(pairs_text) - 1):
        # Truncate to avoid OOM
        a = pairs_text[i][:512]
        b = pairs_text[i + 1][:512]
        input_pairs.append([a, b])

    scores = model.predict(input_pairs)
    for i, score in enumerate(scores):
        if score < threshold:
            boundaries.append(i)

    return boundaries


# ══════════════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════════════

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)


def _truncate(text: str, maxlen: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) > maxlen:
        return text[:maxlen] + "…"
    return text


def visualize_segments(
    pairs_text: list[str],
    boundaries: list[int],
    method_name: str,
) -> None:
    """Pretty-print the segmentation result."""
    print(f"\n{'─' * 70}")
    print(f"  {method_name}  ({len(boundaries)} cuts → {len(boundaries) + 1} segments)")
    print(f"{'─' * 70}")

    seg_id = 1
    for i, pair in enumerate(pairs_text):
        # Extract just the user portion for preview
        lines = pair.split("\n")
        user_line = ""
        for line in lines:
            if line.startswith("[user]"):
                user_line = line[6:].strip()
                break
        preview = _truncate(user_line, 70)
        print(f"    {seg_id}.{i:02d}  {preview}")

        if i in boundaries:
            seg_id += 1
            print(f"    {'═' * 66}  ← CUT")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    conversations = load_conversations()
    print(f"Loaded {len(conversations)} conversations with ≥4 messages")

    # Pick conversations with enough turns to be interesting
    interesting = [(n, t) for n, t in conversations if len(make_pairs(t)) >= 5]
    if not interesting:
        print("No conversations with ≥5 pairs found")
        sys.exit(1)

    # Limit to top 3 by size to keep benchmark fast
    interesting.sort(key=lambda x: len(make_pairs(x[1])), reverse=True)
    interesting = interesting[:3]

    print(f"Testing on {len(interesting)} conversations\n")

    # Load embedding model — use a fast small model for benchmarking
    from sentence_transformers import SentenceTransformer
    print("Loading embedding model (all-MiniLM-L6-v2)...")
    embedder = SentenceTransformer("all-MiniLM-L6-v2")
    print("Model loaded.\n")

    for conv_name, turns in interesting:
        pairs_text = make_pairs(turns)
        n_pairs = len(pairs_text)

        print(f"\n{'═' * 70}")
        print(f"  CONVERSATION: {conv_name}")
        print(f"  {n_pairs} pairs")
        print(f"{'═' * 70}")

        # Embed all pairs
        embeddings = embedder.encode(pairs_text, show_progress_bar=False)
        embeddings = np.array(embeddings)

        # --- Method 1: Cosine threshold (current fiam) ---
        for thresh in [0.65, 0.75, 0.85]:
            b = segment_cosine_threshold(embeddings, threshold=thresh)
            visualize_segments(pairs_text, b, f"cosine_threshold (τ={thresh})")

        # --- Method 2: TextTiling depth score ---
        for cutoff in [0.05, 0.1, 0.15]:
            b = segment_depth_score(embeddings, window=2, depth_cutoff=cutoff)
            visualize_segments(pairs_text, b, f"depth_score (w=2, cut={cutoff})")

        # --- Method 3: Gradient z-score ---
        for z in [1.0, 1.5, 2.0]:
            b = segment_gradient(embeddings, window=3, z_threshold=z)
            visualize_segments(pairs_text, b, f"gradient (w=3, z={z})")

        # --- Method 4: Cross-encoder ---
        # Skipped: ms-marco-MiniLM is a relevance model, not topic seg.
        # Results were similar to cosine threshold (10+ cuts). Too slow on CPU.
        # b = segment_cross_encoder(pairs_text, threshold=0.3)
        # if b is not None:
        #     visualize_segments(pairs_text, b, "cross_encoder (τ=0.3)")

        print()


if __name__ == "__main__":
    main()
