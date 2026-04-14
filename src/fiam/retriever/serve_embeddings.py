"""
Minimal embedding + emotion API server — deploy on DO (or any compute node).

Usage:
    pip install fastapi uvicorn sentence-transformers transformers torch
    python serve_embeddings.py                          # default: bge-m3
    python serve_embeddings.py --model BAAI/bge-m3 --port 8819

Endpoints:
    POST /embed          {"texts": ["hello", "world"]}  →  {"vectors": [[...], [...]]}
    POST /emotion        {"text": "I'm so happy!"}      →  {"valence": 0.8, ...}
    POST /emotion_batch  {"texts": ["hello", "great"]}  →  {"results": [{...}, {...}]}
    GET  /health                                         →  {"status": "ok", ...}
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="fiam-compute-server")

# ---------------------------------------------------------------------------
# Global model handles (lazy-loaded on first request)
# ---------------------------------------------------------------------------
_model = None
_model_name: str = ""

_emotion_pipes: dict[str, Any] = {}


def _load_model(name: str):
    global _model, _model_name
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(name, device="cpu")
        _model_name = name
    return _model


def _load_emotion_pipe(key: str) -> Any:
    """Lazy-load an emotion HF pipeline.  key is 'zh' or 'en'."""
    if key in _emotion_pipes:
        return _emotion_pipes[key]

    import torch
    from transformers import pipeline as hf_pipeline

    model_name = (
        "Johnson8187/Chinese-Emotion-Small" if key == "zh"
        else "SamLowe/roberta-base-go_emotions"
    )
    device: int | str = -1
    if torch.cuda.is_available():
        free = torch.cuda.mem_get_info()[0]
        if free > 0.8e9:
            device = 0

    pipe = hf_pipeline("text-classification", model=model_name,
                        top_k=None, device=device)
    _emotion_pipes[key] = pipe
    print(f"  Emotion model loaded: {model_name} (device={device})")
    return pipe


# ---------------------------------------------------------------------------
# GoEmotions V-A affective norms (28 labels)
# ---------------------------------------------------------------------------
_GO_EMOTIONS_VA: dict[str, tuple[float, float]] = {
    "admiration":     ( 0.50, 0.50), "amusement":      ( 0.80, 0.65),
    "anger":          (-0.80, 0.80), "annoyance":      (-0.50, 0.55),
    "approval":       ( 0.40, 0.30), "caring":         ( 0.60, 0.40),
    "confusion":      (-0.20, 0.50), "curiosity":      ( 0.30, 0.60),
    "desire":         ( 0.40, 0.70), "disappointment": (-0.60, 0.30),
    "disapproval":    (-0.50, 0.45), "disgust":        (-0.70, 0.55),
    "embarrassment":  (-0.50, 0.60), "excitement":     ( 0.80, 0.90),
    "fear":           (-0.70, 0.85), "gratitude":      ( 0.70, 0.40),
    "grief":          (-0.90, 0.40), "joy":            ( 0.90, 0.70),
    "love":           ( 0.90, 0.50), "nervousness":    (-0.40, 0.75),
    "optimism":       ( 0.60, 0.50), "pride":          ( 0.70, 0.60),
    "realization":    ( 0.10, 0.50), "relief":         ( 0.50, 0.20),
    "remorse":        (-0.70, 0.45), "sadness":        (-0.80, 0.30),
    "surprise":       ( 0.10, 0.85), "neutral":        ( 0.00, 0.10),
}

# ---------------------------------------------------------------------------
# Chinese-Emotion V-A affective norms (8 labels)
# ---------------------------------------------------------------------------
_CHINESE_EMOTION_VA: dict[str, tuple[float, float]] = {
    "平淡語氣": ( 0.00, 0.10), "關切語調": ( 0.50, 0.50),
    "開心語調": ( 0.80, 0.70), "憤怒語調": (-0.80, 0.85),
    "悲傷語調": (-0.80, 0.30), "疑問語調": (-0.10, 0.55),
    "驚奇語調": ( 0.10, 0.85), "厭惡語調": (-0.70, 0.55),
    "LABEL_0": ( 0.00, 0.10), "LABEL_1": ( 0.50, 0.50),
    "LABEL_2": ( 0.80, 0.70), "LABEL_3": (-0.80, 0.85),
    "LABEL_4": (-0.80, 0.30), "LABEL_5": (-0.10, 0.55),
    "LABEL_6": ( 0.10, 0.85), "LABEL_7": (-0.70, 0.55),
}

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

# ---------------------------------------------------------------------------
# Text heuristic (identical to classifier/emotion.py)
# ---------------------------------------------------------------------------
_RE_REPEATED_CHAR = re.compile(r"(.)\1{2,}")
_RE_EXCLAMATION   = re.compile(r"!{2,}|！{2,}")
_RE_QUESTION_BURST = re.compile(r"\?{2,}|？{2,}")
_RE_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")
_RE_LAUGH_ZH      = re.compile(r"[哈嘿呵]{3,}")
_RE_SCREAM_ZH     = re.compile(r"[啊嗷呜哇]{3,}")


def _text_arousal_signal(text: str) -> float:
    score = 0.0
    if repeats := _RE_REPEATED_CHAR.findall(text):
        score += min(0.3, len(repeats) * 0.1)
    if exc := _RE_EXCLAMATION.findall(text):
        score += min(0.3, len(exc) * 0.15)
    if _RE_QUESTION_BURST.findall(text):
        score += 0.15
    if caps := _RE_ALL_CAPS_WORD.findall(text):
        score += min(0.25, len(caps) * 0.1)
    if _RE_LAUGH_ZH.search(text):
        score += 0.3
    if _RE_SCREAM_ZH.search(text):
        score += 0.35
    return min(score, 0.85)


def _is_chinese(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk / len(text) > 0.1


# ---------------------------------------------------------------------------
# WDI core
# ---------------------------------------------------------------------------
def _wdi(
    scores: list[dict[str, Any]],
    va_norms: dict[str, tuple[float, float]],
) -> tuple[float, float, str, dict[str, float]]:
    total_w = v_sum = a_sum = 0.0
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


def _analyze_single(text: str) -> dict:
    """Run WDI on one text, return serialisable dict."""
    if not text or not text.strip():
        return {"valence": 0.0, "arousal": 0.1, "confidence": 0.0,
                "dominant_label": "", "label_scores": {}}

    snippet = text[:512]
    # Route by language
    if _is_chinese(snippet):
        pipe = _load_emotion_pipe("zh")
        va_norms = _CHINESE_EMOTION_VA
    else:
        pipe = _load_emotion_pipe("en")
        va_norms = _GO_EMOTIONS_VA

    results = pipe(snippet)
    scores: list[dict[str, Any]] = results[0] if results else []
    valence, model_arousal, top_label, label_dict = _wdi(scores, va_norms)
    heuristic_arousal = _text_arousal_signal(text)
    arousal = max(model_arousal, heuristic_arousal)
    confidence = max((float(s["score"]) for s in scores), default=0.0)
    dominant = _CHINESE_LABEL_NAMES.get(top_label, top_label)
    normalised_scores = {
        _CHINESE_LABEL_NAMES.get(k, k): v for k, v in label_dict.items()
    }
    return {
        "valence": round(valence, 4),
        "arousal": round(arousal, 4),
        "confidence": round(confidence, 4),
        "dominant_label": dominant,
        "label_scores": normalised_scores,
    }


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    vectors: list[list[float]]


class EmotionRequest(BaseModel):
    text: str


class EmotionBatchRequest(BaseModel):
    texts: list[str]


class EmotionResultSchema(BaseModel):
    valence: float
    arousal: float
    confidence: float
    dominant_label: str = ""
    label_scores: dict[str, float] = {}


class EmotionBatchResponse(BaseModel):
    results: list[EmotionResultSchema]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
_CHUNK_CHARS = 512  # chunk size for mean-pool encoding


def _mean_pool_encode(model, text: str) -> np.ndarray:
    """Encode a text of any length via chunk-level mean pooling.

    Splits *text* into ~512-char chunks, encodes each independently,
    then returns the L2-normalised mean of all chunk vectors.
    This preserves information from long texts without the O(n^2) cost
    of encoding a single very-long sequence.
    """
    if len(text) <= _CHUNK_CHARS:
        vec = model.encode([text], convert_to_numpy=True)[0]
    else:
        chunks = [text[i:i + _CHUNK_CHARS]
                  for i in range(0, len(text), _CHUNK_CHARS)]
        chunk_vecs = model.encode(chunks, convert_to_numpy=True)  # (N, dim)
        vec = chunk_vecs.mean(axis=0)
    # L2 normalise
    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec = vec / norm
    return vec


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if not req.texts:
        raise HTTPException(400, "texts must be non-empty")
    if len(req.texts) > 128:
        raise HTTPException(400, "max 128 texts per request")
    model = _load_model(_model_name)
    vecs = np.stack([_mean_pool_encode(model, t) for t in req.texts])
    return EmbedResponse(vectors=vecs.astype(np.float32).tolist())


@app.post("/emotion", response_model=EmotionResultSchema)
def emotion(req: EmotionRequest):
    if not req.text:
        raise HTTPException(400, "text must be non-empty")
    return _analyze_single(req.text)


@app.post("/emotion_batch", response_model=EmotionBatchResponse)
def emotion_batch(req: EmotionBatchRequest):
    if not req.texts:
        raise HTTPException(400, "texts must be non-empty")
    if len(req.texts) > 256:
        raise HTTPException(400, "max 256 texts per request")

    # Group by language for efficient batching
    zh_indices: list[int] = []
    en_indices: list[int] = []
    results: list[dict | None] = [None] * len(req.texts)

    for i, text in enumerate(req.texts):
        if not text or not text.strip():
            results[i] = {"valence": 0.0, "arousal": 0.1, "confidence": 0.0,
                          "dominant_label": "", "label_scores": {}}
            continue
        snippet = text[:512]
        if _is_chinese(snippet):
            zh_indices.append(i)
        else:
            en_indices.append(i)

    # Batch each language group through HF pipeline
    for key, indices in [("zh", zh_indices), ("en", en_indices)]:
        if not indices:
            continue
        pipe = _load_emotion_pipe(key)
        va_norms = _CHINESE_EMOTION_VA if key == "zh" else _GO_EMOTIONS_VA
        snippets = [req.texts[i][:512] for i in indices]
        batch_out = pipe(snippets, batch_size=32)

        for idx, scores_list in zip(indices, batch_out):
            full_text = req.texts[idx]
            scores: list[dict[str, Any]] = scores_list if scores_list else []
            valence, model_arousal, top_label, label_dict = _wdi(scores, va_norms)
            heuristic_arousal = _text_arousal_signal(full_text)
            arousal = max(model_arousal, heuristic_arousal)
            confidence = max((float(s["score"]) for s in scores), default=0.0)
            dominant = _CHINESE_LABEL_NAMES.get(top_label, top_label)
            normalised_scores = {
                _CHINESE_LABEL_NAMES.get(k, k): v for k, v in label_dict.items()
            }
            results[idx] = {
                "valence": round(valence, 4),
                "arousal": round(arousal, 4),
                "confidence": round(confidence, 4),
                "dominant_label": dominant,
                "label_scores": normalised_scores,
            }

    return EmotionBatchResponse(results=results)  # type: ignore[arg-type]


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": _model_name,
        "emotion_models": list(_emotion_pipes.keys()),
    }


@app.post("/unload_emotion")
def unload_emotion():
    """Unload emotion models to free RAM for embedding.

    Call this after emotion_batch processing is done for a session.
    Models will be lazy-loaded again on next emotion request.
    """
    import gc
    import torch

    unloaded = list(_emotion_pipes.keys())
    _emotion_pipes.clear()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print(f"  Emotion models unloaded: {unloaded}")
    return {"unloaded": unloaded}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("FIAM_EMBED_MODEL", "BAAI/bge-m3"))
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Use 127.0.0.1 (Tailscale/SSH tunnel) for safety.")
    parser.add_argument("--port", type=int, default=8819)
    parser.add_argument("--preload-emotion", action="store_true",
                        help="Pre-load emotion models at startup (uses more RAM).")
    args = parser.parse_args()

    _model_name = args.model
    print(f"Loading embedding model {_model_name} ...")
    _load_model(_model_name)
    print("Embedding model ready.")

    if args.preload_emotion:
        print("Pre-loading emotion models ...")
        _load_emotion_pipe("en")
        _load_emotion_pipe("zh")
        print("Emotion models ready.")

    uvicorn.run(app, host=args.host, port=args.port)
