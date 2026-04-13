"""
Profile-aware sentence-transformer embedder.

Language profiles:
  zh    — BAAI/bge-base-zh-v1.5 (768-dim, Chinese-optimized)
  en    — BAAI/bge-base-en-v1.5 (768-dim, English-optimized)
  multi — BAAI/bge-m3 (1024-dim, unified multilingual vector space)

Models are lazy-loaded on first use — only the profile's model is downloaded.

Embedding backends (config.embedding_backend):
  "local"  — run SentenceTransformer in-process (default, needs torch)
  "remote" — call a remote embedding API (serve_embeddings.py on DO)
"""

from __future__ import annotations

import numpy as np

from fiam.config import FiamConfig


class Embedder:
    def __init__(self, config: FiamConfig) -> None:
        self.config = config
        self._model = None  # lazy, only used for local backend

    # ------------------------------------------------------------------
    # Backend: local (SentenceTransformer in-process)
    # ------------------------------------------------------------------

    def _get_model(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            device = "cpu"
            if torch.cuda.is_available():
                free = torch.cuda.mem_get_info()[0]
                if free > 1.5e9:
                    device = "cuda"
            self._model = SentenceTransformer(
                self.config.embedding_model,
                device=device,
            )
        return self._model

    # ------------------------------------------------------------------
    # Backend: remote API (serve_embeddings.py or future HF Inference)
    # ------------------------------------------------------------------

    def _remote_embed(self, texts: list[str]) -> np.ndarray:
        import urllib.request
        import json

        url = self.config.embedding_remote_url.rstrip("/") + "/embed"
        # Batch in chunks of 8 to avoid timeouts on large payloads
        all_vecs = []
        for i in range(0, len(texts), 8):
            chunk = texts[i : i + 8]
            payload = json.dumps({"texts": chunk}).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read())
            all_vecs.extend(body["vectors"])
        return np.array(all_vecs, dtype=np.float32)

    # ------------------------------------------------------------------
    # Public API (auto-dispatch by config.embedding_backend)
    # ------------------------------------------------------------------

    @property
    def _is_remote(self) -> bool:
        return self.config.embedding_backend == "remote"

    def embed(self, text: str) -> np.ndarray:
        """Return a float32 embedding vector for *text*.

        Dimension depends on the language profile (768 or 1024).
        """
        if self._is_remote:
            vecs = self._remote_embed([text])
            return vecs[0]
        model = self._get_model()
        vec = model.encode(text, convert_to_numpy=True)
        return vec.astype(np.float32)

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Batch-encode a list of texts. Returns (N, dim) float32 array.

        Much faster than calling embed() N times because the model
        processes multiple texts in a single forward pass on GPU/CPU.
        """
        if self._is_remote:
            # Remote server handles batching internally
            return self._remote_embed(texts)
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
