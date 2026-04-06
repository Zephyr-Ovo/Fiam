"""
Profile-aware emotion classifier.

Valence: always from cardiffnlp/twitter-xlm-roberta-base-sentiment (multilingual).

Arousal (depends on language_profile):
  zh/multi — cardiffnlp neutral-proxy: arousal ≈ 1 - P(neutral), language-agnostic
  en       — j-hartmann 7-class Russell mapping (fine-grained English arousal)

Text heuristic — language-agnostic arousal floor in all profiles.
  → catches exclamation marks, repeated chars, 哈哈哈, 啊啊啊 etc.

Final emotion = cardiffnlp valence + max(arousal signals)
Models are lazy-loaded on first use — only the profile's models are downloaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import torch
from transformers import pipeline as hf_pipeline

from fiam.config import FiamConfig

# Russell circumplex coordinates (only used in en profile with j-hartmann)
_RUSSELL: dict[str, tuple[float, float]] = {
    "anger":   (-0.8, 0.7),
    "disgust": (-0.6, 0.5),
    "fear":    (-0.7, 0.8),
    "joy":     ( 0.8, 0.6),
    "neutral": ( 0.0, 0.2),
    "sadness": (-0.7, 0.3),
    "surprise":( 0.1, 0.9),
}

# Regex patterns for text-based arousal detection
_RE_REPEATED_CHAR = re.compile(r"(.)\1{2,}")           # aaa, 啊啊啊, !!!
_RE_EXCLAMATION = re.compile(r"!{2,}|！{2,}")          # !! or ！！
_RE_QUESTION_BURST = re.compile(r"\?{2,}|？{2,}")      # ?? or ？？
_RE_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")       # BUG, EVERYWHERE
_RE_LAUGH_ZH = re.compile(r"[哈嘿呵]{3,}")              # 哈哈哈
_RE_SCREAM_ZH = re.compile(r"[啊嗷呜哇]{3,}")           # 啊啊啊, 嗷嗷嗷


@dataclass
class EmotionResult:
    valence: float    # [-1.0, 1.0]
    arousal: float    # [0.0,  1.0]
    confidence: float # [0.0,  1.0]


def _text_arousal_signal(text: str) -> float:
    """Detect emotional intensity from text features, language-agnostic."""
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


def _sentiment_to_valence(label: str, score: float) -> float:
    """Convert cardiffnlp 3-class output to continuous valence in [-1.0, 1.0]."""
    label = label.lower()
    if label == "positive":
        return score * 0.9
    elif label == "negative":
        return -score * 0.9
    else:
        return 0.0


def _sentiment_arousal_proxy(results: list[dict]) -> float:
    """Derive arousal from cardiffnlp's full probability distribution.

    Core insight: arousal ≈ 1 - P(neutral).
    Strong positive or negative sentiment implies emotional intensity.
    This is language-agnostic — works for Chinese, English, and all languages
    cardiffnlp supports.
    """
    # results is top_k=None → all 3 classes with scores
    neutral_score = 0.33  # fallback
    non_neutral_max = 0.0
    for item in results:
        label = item["label"].lower()
        s = float(item["score"])
        if label == "neutral":
            neutral_score = s
        else:
            non_neutral_max = max(non_neutral_max, s)

    # Primary signal: how non-neutral is this text?
    arousal = 1.0 - neutral_score

    # Scale: cardiffnlp is well-calibrated, but we want arousal in [0, 1]
    # A text with P(neutral)=0.3 → arousal=0.7 (strong signal)
    # A text with P(neutral)=0.8 → arousal=0.2 (weak signal)
    # Boost slightly when one sentiment dominates strongly
    if non_neutral_max > 0.7:
        arousal = min(1.0, arousal + 0.1)

    return min(arousal, 0.95)


def _sentiment_derived_arousal(label: str, score: float) -> float:
    """Legacy: derive arousal hint from top-1 sentiment (en profile fallback)."""
    label = label.lower()
    if label == "neutral":
        return 0.15
    return min(0.65, score * 0.6)


class EmotionClassifier:
    """Profile-aware emotion tagger."""

    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        self._sentiment_pipe = None   # cardiffnlp — always loaded
        self._emotion_pipe = None     # j-hartmann — only for en profile

    @property
    def _sentiment(self):
        if self._sentiment_pipe is None:
            device = 0 if torch.cuda.is_available() else -1
            self._sentiment_pipe = hf_pipeline(
                "text-classification",
                model=self.config.sentiment_model_name,
                top_k=None,  # return ALL classes with probabilities
                device=device,
            )
        return self._sentiment_pipe

    @property
    def _emotion(self):
        """j-hartmann emotion model — only loaded for en profile."""
        if self._emotion_pipe is None:
            if not self.config.use_emotion_model:
                return None
            device = 0 if torch.cuda.is_available() else -1
            self._emotion_pipe = hf_pipeline(
                "text-classification",
                model=self.config.emotion_model_name,
                top_k=1,
                device=device,
            )
        return self._emotion_pipe

    def analyze(self, text: str) -> EmotionResult:
        """Run classifier on *text*, return fused EmotionResult."""
        snippet = text[:512]

        # ── cardiffnlp → valence + arousal proxy ──
        sent_results = self._sentiment(snippet)
        # sent_results is [[{label, score}, ...]] — list of all 3 classes
        all_classes: list[dict] = sent_results[0]  # type: ignore[index]
        sent_top = max(all_classes, key=lambda x: x["score"])  # type: ignore[arg-type]
        sent_label = str(sent_top["label"])
        sent_score = float(sent_top["score"])
        valence = _sentiment_to_valence(sent_label, sent_score)

        # Arousal from cardiffnlp neutral-proxy (works for all languages)
        proxy_arousal = _sentiment_arousal_proxy(all_classes)

        # ── j-hartmann → fine-grained arousal (en profile only) ──
        jh_arousal = 0.0
        emo_score = 0.0
        emotion_pipe = self._emotion
        if emotion_pipe is not None:
            emo_results = emotion_pipe(snippet)
            emo_top = emo_results[0][0]  # type: ignore[index]
            emo_label = str(emo_top["label"]).lower()
            emo_score = float(emo_top["score"])
            _, jh_arousal = _RUSSELL.get(emo_label, (0.0, 0.2))

        # ── Text heuristic → arousal floor ──
        heuristic_arousal = _text_arousal_signal(text)

        # ── Fuse arousal: take the strongest signal ──
        arousal = max(proxy_arousal, jh_arousal, heuristic_arousal)

        # Confidence: sentiment model confidence (always available)
        confidence = sent_score
        if emotion_pipe is not None:
            confidence = (sent_score + emo_score) / 2.0

        return EmotionResult(valence=valence, arousal=arousal, confidence=confidence)

    def analyze_event(self, user_text: str, ai_text: str) -> EmotionResult:
        """Analyze both sides of a conversation pair.

        Valence comes from the AI side (its emotional stance).
        Arousal takes the max of both sides.
        """
        user_emotion = self.analyze(user_text)
        ai_emotion = self.analyze(ai_text)
        return EmotionResult(
            valence=ai_emotion.valence,
            arousal=max(user_emotion.arousal, ai_emotion.arousal),
            confidence=(user_emotion.confidence + ai_emotion.confidence) / 2,
        )

    def classify(self, text: str) -> EmotionResult:
        """Pipeline entry point — delegates to analyze()."""
        return self.analyze(text)
