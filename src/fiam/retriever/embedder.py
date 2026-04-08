"""
Profile-aware sentence-transformer embedder.

Language profiles:
  zh    — BAAI/bge-base-zh-v1.5 (768-dim, Chinese-optimized)
  en    — BAAI/bge-base-en-v1.5 (768-dim, English-optimized)
  multi — BAAI/bge-m3 (1024-dim, unified multilingual vector space)

Models are lazy-loaded on first use — only the profile's model is downloaded.
"""

from __future__ import annotations

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from fiam.config import FiamConfig


class Embedder:
    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        self._model: SentenceTransformer | None = None

    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            device = "cpu"
            if torch.cuda.is_available():
                # Check VRAM — bge-m3 needs ~1.5GB headroom
                free = torch.cuda.mem_get_info()[0]
                if free > 1.5e9:
                    device = "cuda"
            self._model = SentenceTransformer(
                self.config.embedding_model,
                device=device,
            )
        return self._model

    def embed(self, text: str) -> np.ndarray:
        """Return a float32 embedding vector for *text*.

        Dimension depends on the language profile (768 or 1024).
        """
        model = self._get_model()
        vec = model.encode(text, convert_to_numpy=True)
        return vec.astype(np.float32)

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Batch-encode a list of texts. Returns (N, dim) float32 array.

        Much faster than calling embed() N times because the model
        processes multiple texts in a single forward pass on GPU/CPU.
        """
        model = self._get_model()
        vecs = model.encode(texts, batch_size=batch_size, convert_to_numpy=True)
        return vecs.astype(np.float32)

    def save(self, vec: np.ndarray, event_id: str) -> str:
        """Save *vec* to embeddings/{event_id}.npy.

        Returns the relative path string stored in EventRecord.embedding.
        """
        out_dir = self.config.embeddings_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        np.save(out_dir / f"{event_id}.npy", vec)
        return f"embeddings/{event_id}.npy"

    def embed_and_save(self, text: str, event_id: str) -> str:
        """Convenience wrapper: embed + save in one call."""
        return self.save(self.embed(text), event_id)
