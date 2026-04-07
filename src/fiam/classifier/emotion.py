"""
Weighted Dimensional Interpolation (WDI) emotion classifier.

▸ Core insight: instead of mapping a single top-1 emotion label to fixed
  Russell circumplex coordinates (lossy, discrete, jumpy), WDI computes
  valence and arousal as probability-weighted averages across the FULL
  model output distribution. This gives smooth, continuous V-A values
  that leverage every label the model predicts.

Model selection by language profile:
  en   — SamLowe/roberta-base-go_emotions  (28 labels, multi-label)
  zh   — Johnson8187/Chinese-Emotion        (8 labels, single-label)
  multi — auto-detect language per snippet → route to zh or en model

Text heuristic — language-agnostic arousal floor in all profiles.
  → catches exclamation marks, repeated chars, 哈哈哈, 啊啊啊 etc.

Models are lazy-loaded on first use — only the profile's models are downloaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import torch
from transformers import pipeline as hf_pipeline

from fiam.config import FiamConfig


# ── GoEmotions V-A affective norms (28 labels) ──────────────────────
# Valence: [-1.0, 1.0], Arousal: [0.0, 1.0]
# Sources: Bradley & Lang (1999), Warriner et al. (2013), Russell (2003),
# calibrated for conversational context.

_GO_EMOTIONS_VA: dict[str, tuple[float, float]] = {
    "admiration":     ( 0.50, 0.50),
    "amusement":      ( 0.80, 0.65),
    "anger":          (-0.80, 0.80),
    "annoyance":      (-0.50, 0.55),
    "approval":       ( 0.40, 0.30),
    "caring":         ( 0.60, 0.40),
    "confusion":      (-0.20, 0.50),
    "curiosity":      ( 0.30, 0.60),
    "desire":         ( 0.40, 0.70),
    "disappointment": (-0.60, 0.30),
    "disapproval":    (-0.50, 0.45),
    "disgust":        (-0.70, 0.55),
    "embarrassment":  (-0.50, 0.60),
    "excitement":     ( 0.80, 0.90),
    "fear":           (-0.70, 0.85),
    "gratitude":      ( 0.70, 0.40),
    "grief":          (-0.90, 0.40),
    "joy":            ( 0.90, 0.70),
    "love":           ( 0.90, 0.50),
    "nervousness":    (-0.40, 0.75),
    "optimism":       ( 0.60, 0.50),
    "pride":          ( 0.70, 0.60),
    "realization":    ( 0.10, 0.50),
    "relief":         ( 0.50, 0.20),
    "remorse":        (-0.70, 0.45),
    "sadness":        (-0.80, 0.30),
    "surprise":       ( 0.10, 0.85),
    "neutral":        ( 0.00, 0.10),
}


# ── Chinese-Emotion V-A affective norms (8 labels) ─────────────────
# Labels from Johnson8187/Chinese-Emotion (xlm-roberta-large fine-tuned).
# Both Chinese label names and LABEL_N fallback for robustness.

_CHINESE_EMOTION_VA: dict[str, tuple[float, float]] = {
    "平淡語氣": ( 0.00, 0.10),  # neutral
    "關切語調": ( 0.50, 0.50),  # caring
    "開心語調": ( 0.80, 0.70),  # happy
    "憤怒語調": (-0.80, 0.85),  # angry
    "悲傷語調": (-0.80, 0.30),  # sad
    "疑問語調": (-0.10, 0.55),  # questioning
    "驚奇語調": ( 0.10, 0.85),  # surprised
    "厭惡語調": (-0.70, 0.55),  # disgusted
    # LABEL_N fallback (if model's id2label mapping isn't set)
    "LABEL_0": ( 0.00, 0.10),
    "LABEL_1": ( 0.50, 0.50),
    "LABEL_2": ( 0.80, 0.70),
    "LABEL_3": (-0.80, 0.85),
    "LABEL_4": (-0.80, 0.30),
    "LABEL_5": (-0.10, 0.55),
    "LABEL_6": ( 0.10, 0.85),
    "LABEL_7": (-0.70, 0.55),
}

# Normalised English names for Chinese-Emotion labels (for dominant_label)
_CHINESE_LABEL_NAMES: dict[str, str] = {
    "平淡語氣": "neutral",     "LABEL_0": "neutral",
    "關切語調": "caring",      "LABEL_1": "caring",
    "開心語調": "happy",       "LABEL_2": "happy",
    "憤怒語調": "angry",       "LABEL_3": "angry",
    "悲傷語調": "sad",         "LABEL_4": "sad",
    "疑問語調": "questioning", "LABEL_5": "questioning",
    "驚奇語調": "surprised",   "LABEL_6": "surprised",
    "厭惡語調": "disgusted",   "LABEL_7": "disgusted",
}


# ── Text heuristic patterns ─────────────────────────────────────────

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
    dominant_label: str = ""                  # e.g. "joy", "angry", "caring"
    label_scores: dict[str, float] = field(default_factory=dict)


# ── Text heuristic ──────────────────────────────────────────────────

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


# ── Language detection ──────────────────────────────────────────────

def _is_chinese(text: str) -> bool:
    """Detect if text is predominantly Chinese (CJK content > 10%)."""
    if not text:
        return False
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk / len(text) > 0.1


# ── WDI core ────────────────────────────────────────────────────────

def _wdi(
    scores: list[dict[str, Any]],
    va_norms: dict[str, tuple[float, float]],
) -> tuple[float, float, str, dict[str, float]]:
    """Weighted Dimensional Interpolation.

    Computes V and A as probability-weighted averages across ALL labels.
    Returns (valence, arousal, dominant_label, label_scores).
    """
    total_w = 0.0
    v_sum = 0.0
    a_sum = 0.0
    top_label = ""
    top_score = 0.0
    label_dict: dict[str, float] = {}

    for item in scores:
        label = str(item["label"])
        prob = float(item["score"])
        label_dict[label] = prob

        if prob > top_score:
            top_score = prob
            top_label = label

        va = va_norms.get(label)
        if va is not None:
            v, a = va
            v_sum += prob * v
            a_sum += prob * a
            total_w += prob

    if total_w > 0:
        valence = max(-1.0, min(1.0, v_sum / total_w))
        arousal = max(0.0, min(1.0, a_sum / total_w))
    else:
        valence, arousal = 0.0, 0.1

    return valence, arousal, top_label, label_dict


# ── Classifier ──────────────────────────────────────────────────────

class EmotionClassifier:
    """WDI-based emotion classifier with per-profile language routing."""

    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        self._pipes: dict[str, Any] = {}

    def _get_pipe(self, key: str) -> Any:
        """Lazy-load an emotion model pipeline. key is 'zh' or 'en'."""
        if key in self._pipes:
            return self._pipes[key]

        model_name = (
            self.config.emotion_model_zh if key == "zh"
            else self.config.emotion_model_en
        )
        if not model_name:
            return None

        device = 0 if torch.cuda.is_available() else -1
        pipe = hf_pipeline(
            "text-classification",
            model=model_name,
            top_k=None,   # return ALL labels with scores
            device=device,
        )
        self._pipes[key] = pipe
        return pipe

    def _route(self, text: str) -> tuple[Any, dict[str, tuple[float, float]]]:
        """Select the right model and V-A norms based on profile and text."""
        profile = self.config.language_profile

        if profile == "zh":
            return self._get_pipe("zh"), _CHINESE_EMOTION_VA
        elif profile == "en":
            return self._get_pipe("en"), _GO_EMOTIONS_VA
        else:  # "multi" — auto-detect per snippet
            if _is_chinese(text):
                pipe = self._get_pipe("zh")
                if pipe is not None:
                    return pipe, _CHINESE_EMOTION_VA
            pipe = self._get_pipe("en")
            if pipe is not None:
                return pipe, _GO_EMOTIONS_VA
            # Fallback to zh if en not configured
            return self._get_pipe("zh"), _CHINESE_EMOTION_VA

    def analyze(self, text: str) -> EmotionResult:
        """Run WDI classifier on text, return continuous V-A EmotionResult."""
        if not text or not text.strip():
            return EmotionResult(valence=0.0, arousal=0.1, confidence=0.0)

        snippet = text[:512]
        pipe, va_norms = self._route(snippet)

        if pipe is None:
            heuristic = _text_arousal_signal(text)
            return EmotionResult(valence=0.0, arousal=heuristic, confidence=0.0)

        # Run model → full probability distribution
        results = pipe(snippet)
        scores: list[dict[str, Any]] = results[0] if results else []

        # WDI interpolation across all labels
        valence, model_arousal, top_label, label_dict = _wdi(scores, va_norms)

        # Text heuristic serves as arousal floor
        heuristic_arousal = _text_arousal_signal(text)
        arousal = max(model_arousal, heuristic_arousal)

        # Confidence = peak probability (how decisive the model is)
        confidence = max((float(s["score"]) for s in scores), default=0.0)

        # Normalise Chinese labels to English names (both dominant and label_scores)
        dominant = _CHINESE_LABEL_NAMES.get(top_label, top_label)
        normalised_scores = {
            _CHINESE_LABEL_NAMES.get(k, k): v for k, v in label_dict.items()
        }

        return EmotionResult(
            valence=valence,
            arousal=arousal,
            confidence=confidence,
            dominant_label=dominant,
            label_scores=normalised_scores,
        )

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
            dominant_label=ai_emotion.dominant_label,
            label_scores=ai_emotion.label_scores,
        )

    def classify(self, text: str) -> EmotionResult:
        """Pipeline entry point — delegates to analyze()."""
        return self.analyze(text)


# ── API-based emotion classifier ────────────────────────────────────

_API_SYSTEM_PROMPT = """\
You are an emotion annotator. Given a text snippet, output exactly one JSON object:
{"valence": float, "arousal": float, "dominant_label": string}
valence in [-1.0, 1.0], arousal in [0.0, 1.0], dominant_label is a single English emotion word.
No explanation, no markdown, just JSON."""


class ApiEmotionClassifier:
    """Emotion classifier that delegates to an LLM API.

    Uses the same narrative_llm_* config fields as the synthesizer.
    Falls back to text heuristic if the API call fails.
    """

    def __init__(self, config: FiamConfig) -> None:
        self.config = config

    def _call_api(self, text: str) -> EmotionResult:
        """Call the LLM and parse a JSON V-A response."""
        import json as _json

        snippet = text[:512]
        user_prompt = f"Annotate this text:\n\n{snippet}"

        try:
            from fiam.synthesizer.narrative import _call_llm
            raw = _call_llm(self.config, _API_SYSTEM_PROMPT, user_prompt)
        except Exception:
            heuristic = _text_arousal_signal(text)
            return EmotionResult(valence=0.0, arousal=heuristic, confidence=0.0)

        try:
            data = _json.loads(raw)
            valence = max(-1.0, min(1.0, float(data.get("valence", 0.0))))
            arousal = max(0.0, min(1.0, float(data.get("arousal", 0.1))))
            label = str(data.get("dominant_label", "neutral"))
        except (ValueError, KeyError, _json.JSONDecodeError):
            heuristic = _text_arousal_signal(text)
            return EmotionResult(valence=0.0, arousal=heuristic, confidence=0.0)

        # Text heuristic as arousal floor
        heuristic = _text_arousal_signal(text)
        arousal = max(arousal, heuristic)

        return EmotionResult(
            valence=valence,
            arousal=arousal,
            confidence=0.8,  # API assumed reasonably confident
            dominant_label=label,
        )

    def analyze(self, text: str) -> EmotionResult:
        if not text or not text.strip():
            return EmotionResult(valence=0.0, arousal=0.1, confidence=0.0)
        return self._call_api(text)

    def analyze_event(self, user_text: str, ai_text: str) -> EmotionResult:
        user_emotion = self.analyze(user_text)
        ai_emotion = self.analyze(ai_text)
        return EmotionResult(
            valence=ai_emotion.valence,
            arousal=max(user_emotion.arousal, ai_emotion.arousal),
            confidence=(user_emotion.confidence + ai_emotion.confidence) / 2,
            dominant_label=ai_emotion.dominant_label,
            label_scores=ai_emotion.label_scores,
        )

    def classify(self, text: str) -> EmotionResult:
        return self.analyze(text)


# ── Factory ─────────────────────────────────────────────────────────

def get_classifier(config: FiamConfig) -> EmotionClassifier | ApiEmotionClassifier:
    """Return the appropriate emotion classifier based on config.emotion_provider."""
    if config.emotion_provider == "api":
        return ApiEmotionClassifier(config)
    return EmotionClassifier(config)
