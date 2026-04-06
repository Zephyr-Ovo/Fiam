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
    from fiam.classifier.emotion import EmotionClassifier, EmotionResult
    from fiam.retriever.embedder import Embedder


@dataclass
class Significance:
    """Multi-signal significance scores for a conversation pair."""
    emotional: float    # max(user, asst) arousal
    novelty: float      # 1 - max_cosine_sim with stored events
    elaboration: float  # log2(user_chars / session_median_chars)


@dataclass
class ExtractedEvent:
    text: str               # full user+assistant conversation fragment
    emotion: EmotionResult  # emotion for this segment
    topic_hint: str = ""    # keywords for topic overlap check
    pair_count: int = 1     # how many pairs were merged into this event
    significance: Significance | None = None


# --- Significance thresholds ---
_AROUSAL_THRESHOLD = 0.6
_NOVELTY_THRESHOLD = 0.7
_ELABORATION_THRESHOLD = 1.5

# --- Merge thresholds ---
_MERGE_AROUSAL_DIFF = 0.2
_MERGE_KEYWORD_OVERLAP = 1     # at least this many shared keywords to merge


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
    classifier: EmotionClassifier,
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

    # Step 3: merge consecutive significant pairs that are similar
    groups: list[list[_Pair]] = [[significant[0]]]
    for prev, cur in zip(significant, significant[1:]):
        if _should_merge(prev, cur):
            groups[-1].append(cur)
        else:
            groups.append([cur])

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
# Merge check
# ------------------------------------------------------------------

def _should_merge(prev: _Pair, cur: _Pair) -> bool:
    """Decide whether two consecutive significant pairs should merge."""
    # Arousal must be close
    if abs(prev.arousal - cur.arousal) >= _MERGE_AROUSAL_DIFF:
        return False
    # Adjacent pairs (no gap) always merge if arousal is close
    if cur.index == prev.index + 1:
        return True
    # Non-adjacent: require keyword overlap (topic continuity)
    overlap = len(prev.keywords & cur.keywords)
    if overlap < _MERGE_KEYWORD_OVERLAP:
        return False
    return True


# ------------------------------------------------------------------
# Pair construction
# ------------------------------------------------------------------

def _make_pairs(
    conversation: list[dict[str, str]],
    classifier: EmotionClassifier,
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
    classifier: EmotionClassifier,
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
    """
    all_turns: list[dict[str, str]] = []
    for p in pairs:
        all_turns.extend(p.turns)

    text = "\n\n".join(
        f"[{t.get('role', 'unknown')}]\n{t.get('text', '')}"
        for t in all_turns
    )

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
        emotion=emotion,
        topic_hint=hint,
        pair_count=len(pairs),
        significance=best_sig,
    )
