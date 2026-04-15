"""
Text intensity heuristic — language-agnostic surface-level intensity detection.

Replaces V/A emotion classification for the pipeline's structural needs
(narrative detail level, significance scoring). Does NOT attempt to
detect emotion — only textual intensity signals like punctuation,
repetition, and exclamatory patterns.

For semantic emotion understanding, see the Phase 2 appraisal system.
"""

from __future__ import annotations

import re


# ── Patterns ─────────────────────────────────────────────────────────

_RE_REPEATED_CHAR = re.compile(r"(.)\1{2,}")           # aaa, 啊啊啊, !!!
_RE_EXCLAMATION = re.compile(r"!{2,}|！{2,}")          # !! or ！！
_RE_QUESTION_BURST = re.compile(r"\?{2,}|？{2,}")      # ?? or ？？
_RE_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")       # BUG, EVERYWHERE
_RE_LAUGH_ZH = re.compile(r"[哈嘿呵]{3,}")              # 哈哈哈
_RE_SCREAM_ZH = re.compile(r"[啊嗷呜哇]{3,}")           # 啊啊啊, 嗷嗷嗷


def text_intensity(text: str) -> float:
    """Detect textual intensity from surface features, language-agnostic.

    Returns a score in [0.0, 0.85].  This captures exclamatory style,
    not semantic emotion — "我不想继续了" scores 0 and that's correct;
    the appraisal system handles calm-but-emotional content.
    """
    if not text:
        return 0.0

    score = 0.0

    repeats = _RE_REPEATED_CHAR.findall(text)
    if repeats:
        score += min(0.3, len(repeats) * 0.1)

    exc = _RE_EXCLAMATION.findall(text)
    if exc:
        score += min(0.3, len(exc) * 0.15)

    qs = _RE_QUESTION_BURST.findall(text)
    if qs:
        score += 0.15

    caps = _RE_ALL_CAPS_WORD.findall(text)
    if caps:
        score += min(0.25, len(caps) * 0.1)

    if _RE_LAUGH_ZH.search(text):
        score += 0.3
    if _RE_SCREAM_ZH.search(text):
        score += 0.35

    return min(score, 0.85)


def pair_intensity(user_text: str, asst_text: str) -> float:
    """Compute text intensity for a user-assistant pair.

    Takes the max of both sides — if either party is intense, the
    pair is intense.
    """
    return max(text_intensity(user_text), text_intensity(asst_text))
