"""Gorge — depth-based topic segmentation with peak-valley confirmation.

Provides both batch and streaming interfaces for TextTiling-style
segmentation over embedding sequences. Also includes drift detection
(hard cosine threshold) as a separate concern.

The algorithms operate on embedding vectors only — embedding computation
and Pool integration belong to the caller.

Batch API:
    gorge(embeddings)  → (boundaries, sims, depths)
    detect_drift(v1, v2, threshold)  → bool

Streaming API:
    sg = StreamGorge()
    for vec in new_vecs:
        cut = sg.push(vec)
        if cut is not None:
            process(sg.consume(cut))
"""

from __future__ import annotations

import numpy as np


# ── Batch API ─────────────────────────────────────────────────────


def block_similarities(
    embeddings: np.ndarray,
    window: int = 2,
) -> list[float]:
    """Compute inter-block cosine similarities at each gap.

    For gap *i*, average embeddings[i-window+1 : i+1] vs
    embeddings[i+1 : i+1+window] and return their cosine.

    Returns a list of length (n - 1).
    """
    n = len(embeddings)
    if n < 2:
        return []

    sims: list[float] = []
    for i in range(n - 1):
        left_start = max(0, i - window + 1)
        right_end = min(n, i + 1 + window)
        left_block = embeddings[left_start : i + 1].mean(axis=0)
        right_block = embeddings[i + 1 : right_end].mean(axis=0)
        denom = np.linalg.norm(left_block) * np.linalg.norm(right_block)
        s = float(np.dot(left_block, right_block) / (denom + 1e-9)) if denom > 1e-9 else 0.0
        sims.append(s)
    return sims


def depth_scores(sims: list[float]) -> list[float]:
    """Compute TextTiling depth scores from a similarity sequence.

    depth[i] = (left_peak - sims[i]) + (right_peak - sims[i])
    where left_peak and right_peak are the nearest uphill values
    on each side. This makes depth a *relative* measure — even high
    similarity values create deep valleys if surrounded by higher peaks.
    """
    n = len(sims)
    depths = [0.0] * n
    for i in range(n):
        lp = sims[i]
        for j in range(i - 1, -1, -1):
            if sims[j] >= lp:
                lp = sims[j]
            else:
                break
        rp = sims[i]
        for j in range(i + 1, n):
            if sims[j] >= rp:
                rp = sims[j]
            else:
                break
        depths[i] = (lp - sims[i]) + (rp - sims[i])
    return depths


def _confirm_peaks(depths: list[float], confirm: int) -> list[int]:
    """Peak-valley confirmation: walk depths, confirm running maxima
    after *confirm* consecutive declines."""
    boundaries: list[int] = []
    cand_idx, cand_val, decline = -1, 0.0, 0
    for i in range(len(depths)):
        if depths[i] > cand_val:
            cand_idx, cand_val = i, depths[i]
            decline = 0
        elif depths[i] < cand_val:
            decline += 1
            if decline >= confirm and cand_idx >= 0:
                boundaries.append(cand_idx)
                cand_idx, cand_val, decline = i, depths[i], 0
    return boundaries


def gorge(
    embeddings: np.ndarray,
    window: int = 2,
    confirm: int = 2,
) -> tuple[list[int], list[float], list[float]]:
    """Batch topic segmentation.

    Returns (boundaries, sims, depths).
    boundaries: list of gap indices where topic shifts occur.
    """
    n = len(embeddings)
    if n < 3:
        return [], [], []
    sims = block_similarities(embeddings, window)
    depths = depth_scores(sims)
    boundaries = _confirm_peaks(depths, confirm)
    return boundaries, sims, depths


def detect_drift(v1: np.ndarray, v2: np.ndarray, threshold: float = 0.65) -> bool:
    """Return True if cosine similarity between v1 and v2 is below *threshold*."""
    denom = np.linalg.norm(v1) * np.linalg.norm(v2)
    if denom < 1e-9:
        return True
    sim = float(np.dot(v1, v2) / denom)
    return sim < threshold


# ── Streaming API ─────────────────────────────────────────────────


class StreamGorge:
    """Incremental segmentation over a growing embedding sequence.

    Usage::

        sg = StreamGorge()
        for vec in new_embeddings:
            cut = sg.push(vec)
            if cut is not None:
                beats_to_flush = sg.consume(cut)
                # caller sends beats_to_flush to Pool.ingest_event()
    """

    def __init__(
        self,
        window: int = 2,
        depth_confirm: int = 2,
        stream_confirm: int = 2,
        max_blocks: int = 20,
        min_depth: float = 0.01,
    ) -> None:
        self._vecs: list[np.ndarray] = []
        self._window = window
        self._depth_confirm = depth_confirm
        self._stream_confirm = stream_confirm
        self._max_blocks = max_blocks
        self._min_depth = min_depth

        # Streaming cut tracking
        self._cut_cand: int | None = None
        self._cut_depth: float = 0.0
        self._cut_confirm: int = 0

    @property
    def size(self) -> int:
        """Number of embeddings currently in buffer."""
        return len(self._vecs)

    def push(self, vec: np.ndarray) -> int | None:
        """Add an embedding to the buffer.

        Returns a gap index if a cut is confirmed (either by depth
        confirmation or safety valve), else None.
        The caller should then call :meth:`consume` with that gap index.
        """
        self._vecs.append(vec)
        n = len(self._vecs)

        if n < 3:
            return None

        embeddings = np.array(self._vecs)
        sims = block_similarities(embeddings, self._window)
        depths = depth_scores(sims)

        # Find best confirmed peak via peak-valley in depth sequence
        peaks = [p for p in _confirm_peaks(depths, self._depth_confirm)
                 if depths[p] >= self._min_depth]
        if not peaks:
            self._cut_cand = None
            self._cut_depth = 0.0
            self._cut_confirm = 0
            # Safety valve
            if n > self._max_blocks:
                return n // 2 - 1  # cut in the middle
            return None

        best_idx = max(peaks, key=lambda i: depths[i])

        # Safety valve: force cut at best peak
        if n > self._max_blocks:
            return best_idx

        # Stream confirmation: same gap must survive stream_confirm more pushes
        if self._cut_cand == best_idx:
            self._cut_confirm += 1
        else:
            self._cut_cand = best_idx
            self._cut_depth = depths[best_idx]
            self._cut_confirm = 0

        if self._cut_confirm >= self._stream_confirm:
            return best_idx

        return None

    def consume(self, gap_index: int) -> list[np.ndarray]:
        """Remove and return embeddings 0..gap_index (inclusive).

        Resets streaming cut state. The returned vectors correspond
        1:1 with the beats the caller has been tracking externally.
        """
        flushed = self._vecs[: gap_index + 1]
        self._vecs = self._vecs[gap_index + 1 :]
        self._cut_cand = None
        self._cut_depth = 0.0
        self._cut_confirm = 0
        return flushed

    def flush_all(self) -> list[np.ndarray]:
        """Force-flush the entire buffer (e.g. on session end).

        Returns all remaining vectors. Buffer is emptied.
        """
        flushed = self._vecs
        self._vecs = []
        self._cut_cand = None
        self._cut_depth = 0.0
        self._cut_confirm = 0
        return flushed
