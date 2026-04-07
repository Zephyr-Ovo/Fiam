"""
Event extractor — multi-signal significance gate.

Groups turns into user-assistant pairs, classifies each pair,
evaluates significance across three independent channels
(emotional / novelty / elaboration), and merges nearby significant
segments. Any single channel exceeding its threshold is enough to store.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from fiam.classifier.emotion import EmotionClassifier, ApiEmotionClassifier, EmotionResult
    from fiam.retriever.embedder import Embedder


@dataclass
class Significance:
    """Multi-signal significance scores for a conversation pair."""
    emotional: float    # max(user, asst) arousal
    novelty: float      # 1 - max_cosine_sim with stored events
    elaboration: float  # log2(user_chars / session_median_chars)


@dataclass
class ExtractedEvent:
    text: str               # full user+assistant conversation fragment (for embedding)
    emotion: EmotionResult  # emotion for this segment
    thinking: str = ""      # assistant thinking chain (stored, NOT embedded)
    topic_hint: str = ""    # keywords for topic overlap check
    pair_count: int = 1     # how many pairs were merged into this event
    significance: Significance | None = None


# --- Significance thresholds ---
_AROUSAL_THRESHOLD = 0.6
_NOVELTY_THRESHOLD = 0.7
_ELABORATION_THRESHOLD = 1.5

# --- Topic segmentation (TextTiling depth score) ---
_DEPTH_WINDOW = 2              # block width for depth-score computation
_DEPTH_CUTOFF = 0.1            # minimum depth to be a topic boundary

# --- Paste detection ---
# If user text has high code/markup ratio vs original prose, discount elaboration
_RE_CODEBLOCK = re.compile(r"```[\s\S]*?```")
_RE_TERMINAL = re.compile(r"(?:^[\$>].*$|^\s{2,}[\w/\\]+)", re.MULTILINE)
_RE_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s", re.MULTILINE)
_PASTE_INDICATOR_COUNT = 3  # 3+ code blocks or terminal lines = likely paste


@dataclass
class _Pair:
    """One user-assistant exchange."""
    turns: list[dict[str, str]]
    emotion: EmotionResult
    arousal: float
    valence: float
    keywords: set[str]
    index: int = 0          # position among all pairs (for adjacency check)
    significance: Significance | None = None


def segment(
    conversation: list[dict[str, str]],
    classifier: EmotionClassifier | ApiEmotionClassifier,
    *,
    arousal_threshold: float = _AROUSAL_THRESHOLD,
    novelty_threshold: float = _NOVELTY_THRESHOLD,
    elaboration_threshold: float = _ELABORATION_THRESHOLD,
    embedder: Embedder | None = None,
    stored_vecs: list[np.ndarray] | None = None,
    debug: bool = False,
) -> list[ExtractedEvent]:
    """Extract significant events via multi-signal gate.

    A pair is stored if ANY of these channels exceeds its threshold:
      - emotional: max(user, asst) arousal > arousal_threshold
      - novelty:   semantic distance from stored events > novelty_threshold
      - elaboration: user message length vs session median > elaboration_threshold

    Returns an empty list if no segment is significant.
    """
    if not conversation:
        return []

    # Step 1: group into pairs and classify
    pairs = _make_pairs(conversation, classifier)
    if not pairs:
        return []

    # Compute session median user chars for elaboration
    user_char_counts = []
    for p in pairs:
        chars = sum(len(t.get("text", "")) for t in p.turns if t.get("role") == "user")
        user_char_counts.append(chars)
    session_median_chars = float(sorted(user_char_counts)[len(user_char_counts) // 2])

    # Step 2: compute significance for each pair and filter
    significant: list[_Pair] = []
    for p in pairs:
        sig = _compute_significance(
            p, embedder, stored_vecs, session_median_chars,
        )
        p.significance = sig

        passes = (
            sig.emotional > arousal_threshold
            or sig.novelty > novelty_threshold
            or sig.elaboration > elaboration_threshold
        )

        if debug:
            channels = []
            if sig.emotional > arousal_threshold:
                channels.append("emo")
            if sig.novelty > novelty_threshold:
                channels.append("nov")
            if sig.elaboration > elaboration_threshold:
                channels.append("elab")
            tag = f"*** {'+'.join(channels)}" if channels else "skip"
            preview = p.turns[0].get("text", "")[:40].replace("\n", " ")
            print(f"  pair {p.index}: a={sig.emotional:.2f} n={sig.novelty:.2f} "
                  f"e={sig.elaboration:.2f} {tag} | {preview}")

        if passes and not _is_meta_reference(p):
            significant.append(p)

    if not significant:
        if debug:
            print(f"[extractor] No pairs passed significance gate")
        return []

    # Step 3: topic segmentation via depth score
    #
    # Instead of merging adjacent pairs by heuristics, we segment ALL
    # pairs using TextTiling depth scoring. This finds natural topic
    # boundaries by looking for relative dips in coherence — not
    # absolute thresholds. Then we assign each significant pair to
    # its topic segment.
    boundaries = _depth_score_boundaries(pairs, embedder, debug)

    # Build topic segments: each segment is a range [start, end) of pair indices
    cut_indices = sorted(set(boundaries))
    seg_ranges: list[tuple[int, int]] = []
    prev_start = 0
    for ci in cut_indices:
        seg_ranges.append((prev_start, ci + 1))
        prev_start = ci + 1
    seg_ranges.append((prev_start, len(pairs)))

    # Group significant pairs by which topic segment they fall in
    groups: list[list[_Pair]] = []
    for seg_start, seg_end in seg_ranges:
        group = [p for p in significant if seg_start <= p.index < seg_end]
        if group:
            groups.append(group)

    if not groups:
        return []

    return [_build_event(g) for g in groups]


# ------------------------------------------------------------------
# Multi-signal significance
# ------------------------------------------------------------------

def _compute_significance(
    pair: _Pair,
    embedder: Embedder | None,
    stored_vecs: list[np.ndarray] | None,
    session_median_chars: float,
) -> Significance:
    """Compute significance across three independent channels."""
    emotional = pair.arousal

    # Novelty: semantic distance from all stored events
    novelty = 1.0  # cold start = everything is novel
    if embedder is not None and stored_vecs:
        user_text = "\n".join(
            t.get("text", "") for t in pair.turns if t.get("role") == "user"
        )
        if user_text.strip():
            vec = embedder.embed(user_text)
            max_sim = max(
                float(np.dot(vec, sv) / (np.linalg.norm(vec) * np.linalg.norm(sv) + 1e-9))
                for sv in stored_vecs
            )
            novelty = 1.0 - max(0.0, max_sim)

    # Elaboration: relative user message length (log scale)
    user_chars = sum(
        len(t.get("text", "")) for t in pair.turns if t.get("role") == "user"
    )
    if session_median_chars > 0 and user_chars > 0:
        elaboration = math.log2(user_chars / session_median_chars)
    else:
        elaboration = 0.0

    # Paste discount: if user text is mostly pasted code/terminal/markdown,
    # the length is misleading — discount elaboration signal
    user_text = "\n".join(
        t.get("text", "") for t in pair.turns if t.get("role") == "user"
    )
    paste_signals = (
        len(_RE_CODEBLOCK.findall(user_text))
        + len(_RE_TERMINAL.findall(user_text))
        + len(_RE_MARKDOWN_HEADER.findall(user_text))
    )
    if paste_signals >= _PASTE_INDICATOR_COUNT:
        elaboration *= 0.3  # heavy discount — paste ≠ elaboration

    return Significance(
        emotional=emotional,
        novelty=novelty,
        elaboration=elaboration,
    )


# ------------------------------------------------------------------
# Meta-reference detection
# ------------------------------------------------------------------

# Patterns indicating the pair is *about* a past conversation
_META_PATTERNS = re.compile(
    r"那次|之前|上次|记得|还记得|你说过|我们聊过|前几天|昨天你"
)
# Patterns indicating fresh content (overrides meta detection)
_FRESH_PATTERNS = re.compile(
    r"今天|刚才|现在|又|还是|新的"
)


def _is_meta_reference(pair: _Pair) -> bool:
    """Detect whether a pair is purely referencing a past conversation.

    Returns True when the user is reminiscing about a prior exchange
    without introducing new emotional content — storing such pairs
    would create feedback loops in the memory system.
    """
    user_text = "\n".join(
        t.get("text", "") for t in pair.turns if t.get("role") == "user"
    )
    if not _META_PATTERNS.search(user_text):
        return False
    # If fresh-content markers are present, it's not pure meta
    if _FRESH_PATTERNS.search(user_text):
        return False
    return True


# ------------------------------------------------------------------
# Paste dump detection
# ------------------------------------------------------------------

_PASTE_DUMP_MIN_CHARS = 500  # only check messages above this length


def _is_paste_dump(text: str) -> bool:
    """Detect if a user message is a bulk paste (code / markdown / terminal dump).

    Returns True when the text contains enough structural indicators
    (code fences, terminal prompts, markdown headers) to be confidently
    classified as pasted rather than typed.
    """
    if len(text) < _PASTE_DUMP_MIN_CHARS:
        return False
    paste_signals = (
        len(_RE_CODEBLOCK.findall(text))
        + len(_RE_TERMINAL.findall(text))
        + len(_RE_MARKDOWN_HEADER.findall(text))
    )
    return paste_signals >= _PASTE_INDICATOR_COUNT


# ------------------------------------------------------------------
# Merge check
# ------------------------------------------------------------------

def _depth_score_boundaries(
    pairs: list[_Pair],
    embedder: Embedder | None,
    debug: bool = False,
) -> list[int]:
    """Find topic boundaries using TextTiling depth scoring.

    For each gap between consecutive pairs, compute the block
    similarity (average embedding of the `window` pairs before/after).
    Then compute a depth score at each gap: how much the similarity
    dips relative to the nearest peaks on either side. Cut at local
    maxima of depth that exceed the cutoff.

    This is fundamentally different from threshold-based segmentation:
    - It's RELATIVE: a drop from 0.9 to 0.7 is a cut even though 0.7
      is "high" in absolute terms.
    - Gradual topic drift → no sharp dip → no cut (correct).
    - Abrupt topic change → sharp dip → cut (correct).
    """
    n = len(pairs)
    if n < 3 or embedder is None:
        return []

    # Embed all pairs (user + assistant text combined)
    vecs: list[np.ndarray] = []
    for p in pairs:
        text = " ".join(
            t.get("text", "") for t in p.turns
        ).strip()
        if text:
            vecs.append(embedder.embed(text))
        else:
            vecs.append(np.zeros(embedder.config.embedding_dim, dtype=np.float32))

    embeddings = np.array(vecs)
    window = _DEPTH_WINDOW

    # Step 1: block similarities at each gap
    sims = np.zeros(n - 1)
    for i in range(n - 1):
        left_start = max(0, i - window + 1)
        right_end = min(n, i + 1 + window)
        left_block = embeddings[left_start:i + 1].mean(axis=0)
        right_block = embeddings[i + 1:right_end].mean(axis=0)
        denom = np.linalg.norm(left_block) * np.linalg.norm(right_block)
        sims[i] = float(np.dot(left_block, right_block) / (denom + 1e-9)) if denom > 1e-9 else 0.0

    # Step 2: depth score at each gap
    depths = np.zeros(len(sims))
    for i in range(len(sims)):
        # Left peak: walk left until similarity stops increasing
        left_peak = sims[i]
        for j in range(i - 1, -1, -1):
            if sims[j] >= left_peak:
                left_peak = sims[j]
            else:
                break
        # Right peak: walk right
        right_peak = sims[i]
        for j in range(i + 1, len(sims)):
            if sims[j] >= right_peak:
                right_peak = sims[j]
            else:
                break
        depths[i] = (left_peak - sims[i]) + (right_peak - sims[i])

    # Step 3: cut at local maxima of depth that exceed cutoff
    boundaries: list[int] = []
    for i in range(len(depths)):
        if depths[i] < _DEPTH_CUTOFF:
            continue
        is_peak = True
        if i > 0 and depths[i - 1] > depths[i]:
            is_peak = False
        if i < len(depths) - 1 and depths[i + 1] > depths[i]:
            is_peak = False
        if is_peak:
            boundaries.append(i)

    if debug:
        print(f"  [depth] {len(boundaries)} topic boundaries found at gaps: {boundaries}")
        for i, (s, d) in enumerate(zip(sims, depths)):
            marker = " ← CUT" if i in boundaries else ""
            print(f"    gap {i}: sim={s:.3f} depth={d:.3f}{marker}")

    return boundaries


# ------------------------------------------------------------------
# Pair construction
# ------------------------------------------------------------------

def _make_pairs(
    conversation: list[dict[str, str]],
    classifier: EmotionClassifier | ApiEmotionClassifier,
) -> list[_Pair]:
    """Group turns into user-assistant pairs and classify each."""
    pairs: list[_Pair] = []
    buf: list[dict[str, str]] = []

    for turn in conversation:
        role = turn.get("role", "")
        if role == "user" and buf and any(
            t.get("role") == "assistant" for t in buf
        ):
            pairs.append(_classify_pair(buf, classifier))
            buf = [turn]
        else:
            buf.append(turn)

    if buf:
        if pairs and not any(t.get("role") == "assistant" for t in buf):
            pairs[-1].turns.extend(buf)
        else:
            pairs.append(_classify_pair(buf, classifier))

    # Assign indices for adjacency checks
    for i, p in enumerate(pairs):
        p.index = i

    return pairs


def _classify_pair(
    turns: list[dict[str, str]],
    classifier: EmotionClassifier | ApiEmotionClassifier,
) -> _Pair:
    """Classify one pair and extract keywords."""
    user_text = "\n".join(
        t.get("text", "") for t in turns if t.get("role") == "user"
    )
    asst_text = "\n".join(
        t.get("text", "") for t in turns if t.get("role") == "assistant"
    )

    if user_text and asst_text:
        emotion = classifier.analyze_event(user_text, asst_text)
    elif asst_text:
        emotion = classifier.analyze(asst_text)
    elif user_text:
        emotion = classifier.analyze(user_text)
    else:
        emotion = classifier.analyze("")

    keywords = _extract_keywords(user_text + " " + asst_text)

    return _Pair(
        turns=turns,
        emotion=emotion,
        arousal=emotion.arousal,
        valence=emotion.valence,
        keywords=keywords,
    )


def _extract_keywords(text: str) -> set[str]:
    """Extract simple content keywords from text (lowercase, 4+ chars, no stopwords)."""
    _STOPWORDS = {
        "this", "that", "with", "from", "have", "been", "were", "they",
        "their", "what", "when", "where", "which", "about", "would",
        "could", "should", "there", "these", "those", "your", "will",
        "just", "like", "some", "than", "them", "then", "into", "also",
        "very", "much", "more", "here", "being", "does", "doing",
    }
    words = set()
    for w in text.lower().split():
        cleaned = "".join(c for c in w if c.isalnum())
        if len(cleaned) >= 4 and cleaned not in _STOPWORDS:
            words.add(cleaned)
    return words


# ------------------------------------------------------------------
# Event construction
# ------------------------------------------------------------------

def _build_event(pairs: list[_Pair]) -> ExtractedEvent:
    """Build an ExtractedEvent from a group of pairs.

    For merged events: arousal = max, valence = average.
    Paste-heavy user turns are replaced with (略) — the AI response
    already summarises what the user pasted.
    """
    all_turns: list[dict[str, str]] = []
    for p in pairs:
        all_turns.extend(p.turns)

    # Build conversation text; redact paste-heavy user turns
    parts: list[str] = []
    for t in all_turns:
        role = t.get("role", "unknown")
        body = t.get("text", "")
        if role == "user" and _is_paste_dump(body):
            body = "(略)"
        parts.append(f"[{role}]\n{body}")
    text = "\n\n".join(parts)

    # Collect thinking chains from assistant turns (stored, not embedded)
    thinking_parts: list[str] = []
    for t in all_turns:
        th = t.get("thinking", "")
        if th:
            thinking_parts.append(th)
    thinking = "\n\n".join(thinking_parts)

    # Merge rule: max arousal, average valence, average confidence
    max_a = max(p.arousal for p in pairs)
    avg_v = sum(p.valence for p in pairs) / len(pairs)
    avg_c = sum(p.emotion.confidence for p in pairs) / len(pairs)

    ER = type(pairs[0].emotion)
    emotion = ER(valence=avg_v, arousal=max_a, confidence=avg_c)

    # Combine keywords for topic hint
    all_kw = set()
    for p in pairs:
        all_kw |= p.keywords
    hint = ", ".join(sorted(all_kw)[:5]) if all_kw else ""

    # Pick the strongest significance from the group
    best_sig = None
    for p in pairs:
        if p.significance is not None:
            if best_sig is None or p.significance.emotional > best_sig.emotional:
                best_sig = p.significance

    return ExtractedEvent(
        text=text,
        thinking=thinking,
        emotion=emotion,
        topic_hint=hint,
        pair_count=len(pairs),
        significance=best_sig,
    )
