"""
Minimal embedding API server — deploy on DO (or any compute node).

Usage:
    pip install fastapi uvicorn sentence-transformers numpy
    python serve_embeddings.py                          # default: bge-m3
    python serve_embeddings.py --model BAAI/bge-m3 --port 8819

Endpoints:
    POST /embed          {"texts": ["hello", "world"]}  →  {"vectors": [[...], [...]]}
    GET  /health                                         →  {"status": "ok", ...}
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="fiam-compute-server")

# ---------------------------------------------------------------------------
# Global model handle (lazy-loaded on first request)
# ---------------------------------------------------------------------------
_model = None
_model_name: str = ""


def _load_model(name: str):
    global _model, _model_name
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(name, device="cpu")
        _model_name = name
    return _model


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    vectors: list[list[float]]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
_CHUNK_CHARS = 512  # chunk size for mean-pool encoding


def _mean_pool_encode(model, text: str) -> np.ndarray:
    """Encode a text of any length via chunk-level mean pooling.

    Splits *text* into ~512-char chunks, encodes each independently,
    then returns the L2-normalised mean of all chunk vectors.
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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": _model_name,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("FIAM_EMBED_MODEL", "BAAI/bge-m3"))
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Use 127.0.0.1 (Tailscale/SSH tunnel) for safety.")
    parser.add_argument("--port", type=int, default=8819)
    args = parser.parse_args()

    _model_name = args.model
    print(f"Loading embedding model {_model_name} ...")
    _load_model(_model_name)
    print("Embedding model ready.")

    uvicorn.run(app, host=args.host, port=args.port)
