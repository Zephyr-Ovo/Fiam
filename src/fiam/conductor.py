"""Conductor — stateless information hub.

Every message enters through ``receive()`` / ``receive_cc()``, gets
written to flow.jsonl, embedded, segmented by Gorge, and stored in Pool.
Outbound messages leave via ``dispatch()``.

Conductor has **no heartbeat and no scheduling**.  It is driven entirely
by external callers (daemon, channels, dashboard).

Recall is the one exception that does NOT flow through Conductor.
Drift detection fires an ``on_drift`` callback so the owner (daemon)
can run retrieval and write recall.md independently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import numpy as np

from fiam.config import FiamConfig
from fiam.gorge import StreamGorge, detect_drift
from fiam.store.beat import Beat, append_beat
from fiam.store.features import FeatureStore
from fiam.store.pool import Pool

if TYPE_CHECKING:
    from fiam.retriever.embedder import Embedder
    from fiam.store.beat import AiStatus, BeatSource, UserStatus

logger = logging.getLogger(__name__)


class Conductor:
    """Stateless information hub: receive → flow + embed + segment + store.

    Two entry points:
      - ``receive()``    — external messages (TG, email, favilla, ...)
      - ``receive_cc()`` — Claude Code JSONL delta

    One exit:
      - ``dispatch()``   — send outbound message via the right channel

    Drift detection fires ``on_drift(query_vec)`` so the caller can
    handle recall independently (recall never enters flow).
    """

    def __init__(
        self,
        pool: Pool,
        embedder: "Embedder",
        config: FiamConfig,
        flow_path: Path | None,
        *,
        user_status: "UserStatus" = "away",
        ai_status: "AiStatus" = "online",
        drift_threshold: float = 0.65,
        gorge_max_beat: int = 30,
        gorge_min_depth: float = 0.01,
        gorge_stream_confirm: int = 2,
        on_drift: Callable[[np.ndarray], None] | None = None,
        bus: object | None = None,
        memory_mode: str | None = None,
        feature_store: FeatureStore | None = None,
    ) -> None:
        self.pool = pool
        self.embedder = embedder
        self.config = config
        self.flow_path = flow_path
        self.bus = bus  # fiam.bus.Bus | None — optional, for dispatch publishing
        self.feature_store = feature_store
        self.memory_mode = (memory_mode or getattr(config, "memory_mode", "auto")).lower()

        self.user_status: UserStatus = user_status
        self.ai_status: AiStatus = ai_status

        self._drift_threshold = drift_threshold
        self._last_vec: np.ndarray | None = None
        self._last_ingested_vec: np.ndarray | None = None
        self._on_drift = on_drift

        # StreamGorge for real-time segmentation
        self._gorge = StreamGorge(
            max_beat=gorge_max_beat,
            min_depth=gorge_min_depth,
            stream_confirm=gorge_stream_confirm,
        )

        # Beat buffer (parallel to gorge's embedding buffer)
        self._beat_buf: list[Beat] = []

    @property
    def last_ingested_vector(self) -> np.ndarray | None:
        """Embedding vector for the most recently ingested beat, if available."""
        return self._last_ingested_vec

    # ==================================================================
    # Status
    # ==================================================================

    def set_status(
        self,
        *,
        user: "UserStatus | None" = None,
        ai: "AiStatus | None" = None,
    ) -> None:
        if user is not None:
            self.user_status = user
        if ai is not None:
            self.ai_status = ai

    # ==================================================================
    # Receive: the unified entry point
    # ==================================================================

    def _ingest_beat(self, beat: Beat) -> str | None:
        """Process one beat: write to flow.jsonl, embed, feed gorge.

        Returns event_id if gorge cuts a segment, else None.
        """
        # 1. Append to flow.jsonl (skip when flow_path is None, e.g. reprocess)
        if self.flow_path is not None:
            append_beat(self.flow_path, beat)

        # 2. Embed (may fail — beat is persisted in flow.jsonl regardless)
        try:
            vec = self.embedder.embed(beat.text)
        except Exception:
            logger.error("embed failed for beat at %s, skipping gorge", beat.t.isoformat())
            return None
        self._last_ingested_vec = vec

        if self.feature_store is not None:
            try:
                self.feature_store.append_beat_vector(
                    beat,
                    vec,
                    model_id=getattr(self.config, "embedding_model", ""),
                )
            except Exception:
                logger.error("feature_store append failed", exc_info=True)

        if self.memory_mode == "manual":
            return None

        # 3. Drift detection → fire callback (recall is caller's responsibility)
        if (self._last_vec is not None
                and beat.source not in ("action",)
                and self._on_drift is not None):
            if detect_drift(self._last_vec, vec, self._drift_threshold):
                try:
                    self._on_drift(vec)
                except Exception:
                    logger.error("on_drift callback failed", exc_info=True)
        self._last_vec = vec

        # Interaction windows (readalong, calls, games, phone chats) are
        # time-sensitive activity streams. Keep drift detection, but do not
        # let them create automatic memory-event cuts.
        if self._no_event_cut(beat):
            return None

        # 4. Feed gorge + track beat
        self._beat_buf.append(beat)
        cut = self._gorge.push(vec)
        if cut is not None:
            return self._flush_segment(cut)

        return None

    @staticmethod
    def _no_event_cut(beat: Beat) -> bool:
        meta = beat.meta or {}
        return bool(
            meta.get("no_event_cut")
            or meta.get("kind") == "interaction"
            or meta.get("interaction")
        )

    def receive(
        self,
        text: str,
        source: "BeatSource",
        t: datetime | None = None,
        meta: dict | None = None,
    ) -> str | None:
        """Receive an external message: create beat, process, return event_id or None."""
        if t is None:
            t = datetime.now(timezone.utc)
        meta = meta or {}
        beat = Beat(
            t=t,
            text=self._format_external_text(text, source, meta),
            source=source,
            user=self.user_status,
            ai=self.ai_status,
            meta=meta,
        )
        return self._ingest_beat(beat)

    def _format_external_text(self, text: str, source: str, meta: dict) -> str:
        speaker = str(meta.get("speaker") or "").strip()
        if not speaker:
            if source in {"favilla", "app", "webapp"}:
                speaker = (self.config.user_name or "zephyr").strip()
            elif source in {"schedule", "limen", "xiao", "ring"}:
                speaker = source
            else:
                speaker = str(meta.get("from_name") or source).strip()
        clean = text.strip()
        if clean.startswith(f"{speaker}:") or clean.startswith(f"{speaker}："):
            return clean
        return f"{speaker.lower()}：{clean}"

    def receive_cc(
        self,
        jsonl_path: Path,
        byte_offset: int = 0,
    ) -> tuple[list[str | None], int]:
        """Parse CC JSONL → beats → process each.

        Returns (list of event_ids_or_None per beat, new_byte_offset).
        """
        from fiam.adapter.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        beats, new_offset = adapter.parse_beats(
            jsonl_path, byte_offset,
            user_status=self.user_status,
            ai_status=self.ai_status,
            user_name=getattr(self.config, "user_name", "") or "zephyr",
            ai_name=getattr(self.config, "ai_name", "") or "ai",
        )
        results = []
        for beat in beats:
            eid = self._ingest_beat(beat)
            results.append(eid)
        return results, new_offset

    # ==================================================================
    # Segment flushing
    # ==================================================================

    def _flush_segment(self, gap_index: int) -> str:
        """Cut beats 0..gap_index into a pool event."""
        consumed_vecs = self._gorge.consume(gap_index)
        consumed_beats = self._beat_buf[: gap_index + 1]
        self._beat_buf = self._beat_buf[gap_index + 1 :]

        # Build event body from beats
        body = "\n".join(b.text for b in consumed_beats)

        # Event fingerprint = mean of constituent beat embeddings
        fp = np.mean(consumed_vecs, axis=0).astype(np.float32)
        norm = np.linalg.norm(fp)
        if norm > 1e-9:
            fp = (fp / norm).astype(np.float32)

        # Timestamp = first beat's time
        t = consumed_beats[0].t if consumed_beats else datetime.now(timezone.utc)

        event_id = self.pool.new_event_id()
        try:
            self.pool.ingest_event(event_id, t, body, fp)
        except Exception:
            logger.error(
                "pool.ingest_event failed for %s (%d beats lost)",
                event_id, len(consumed_beats),
            )
            raise
        self._post_ingest([event_id])
        return event_id

    def flush_all(self) -> list[str]:
        """Force-flush all buffered beats as event(s). Call on session end."""
        if not self._beat_buf:
            return []

        vecs = self._gorge.flush_all()
        beats = self._beat_buf
        self._beat_buf = []

        if not beats:
            return []

        body = "\n".join(b.text for b in beats)
        fp = np.mean(vecs, axis=0).astype(np.float32) if vecs else np.zeros(
            self.pool.dim, dtype=np.float32
        )
        norm = np.linalg.norm(fp)
        if norm > 1e-9:
            fp = (fp / norm).astype(np.float32)
        t = beats[0].t

        event_id = self.pool.new_event_id()
        self.pool.ingest_event(event_id, t, body, fp)
        self._post_ingest([event_id])
        return [event_id]

    # ==================================================================
    # Post-ingest: edge generation + DS naming
    # ==================================================================

    def _post_ingest(self, event_ids: list[str]) -> None:
        """Run graph_builder after new events are created."""
        if self.config is None:
            return  # test environment — skip edge generation
        if self.memory_mode == "manual":
            return
        try:
            from fiam.retriever.graph_builder import build_edges
            summary = build_edges(self.pool, event_ids, self.config)
            logger.info("post_ingest: %s", summary)
        except Exception:
            logger.error("post_ingest graph_builder failed for %s", event_ids, exc_info=True)

    # ==================================================================
    # Dispatch: outbound messages
    # ==================================================================

    def dispatch(self, channel: str, recipient: str, text: str) -> bool:
        """Send outbound message via MQTT to the appropriate channel bridge.

        Publishes to ``fiam/dispatch/<target>``. The target bridge process
        (bridge_tg, bridge_email, ...) subscribes and performs the actual
        delivery. Conductor knows nothing about tokens, SMTP, or APIs.

        Returns True if the publish was accepted by the bus.
        """
        from fiam.plugins import resolve_dispatch_target
        target = resolve_dispatch_target(self.config, channel)
        if target is None:
            logger.info("dispatch skipped: plugin disabled (channel=%s)", channel)
            return False
        payload = {
            "text": text,
            "recipient": recipient,
        }
        if self.bus is None:
            logger.error("dispatch: no bus configured (channel=%s)", target)
            return False
        ok = self.bus.publish_dispatch(target, payload)
        if not ok:
            logger.warning("dispatch: bus rejected payload (channel=%s)", target)
        return ok
