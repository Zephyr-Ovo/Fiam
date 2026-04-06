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
from sentence_transformers import SentenceTransformer

from fiam.config import FiamConfig


class Embedder:
    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        self._model: SentenceTransformer | None = None

    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.config.embedding_model)
        return self._model

    def embed(self, text: str) -> np.ndarray:
        """Return a float32 embedding vector for *text*.

        Dimension depends on the language profile (768 or 1024).
        """
        model = self._get_model()
        vec = model.encode(text, convert_to_numpy=True)
        return vec.astype(np.float32)

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
