"""Conductor — information flow orchestrator.

Central routing layer between input sources, processing, and storage.
Replaces the ad-hoc injection/recall/segmentation logic scattered in daemon.

Responsibilities:
  - Beat ingestion: external messages → flow.jsonl + StreamGorge
  - CC output decomposition: JSONL → beats → ingest
  - Gorge integration: StreamGorge cuts → Pool.ingest_event()
  - Recall trigger: drift detection → retrieve → write recall.md
  - Injection preparation: format user field + additionalContext for `claude -p`
  - Status management: user_status + ai_status pair
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from fiam.gorge import StreamGorge, detect_drift
from fiam.retriever.spread import retrieve
from fiam.store.beat import Beat, append_beat
from fiam.store.pool import Pool

if TYPE_CHECKING:
    from fiam.retriever.embedder import Embedder
    from fiam.store.beat import AiStatus, BeatSource, UserStatus


class Conductor:
    """Orchestrates beat flow: ingest → embed → segment → store → recall."""

    def __init__(
        self,
        pool: Pool,
        embedder: "Embedder",
        flow_path: Path,
        recall_path: Path,
        *,
        user_status: "UserStatus" = "away",
        ai_status: "AiStatus" = "online",
        drift_threshold: float = 0.65,
        gorge_max_blocks: int = 20,
        gorge_min_depth: float = 0.01,
        recall_top_k: int = 3,
    ) -> None:
        self.pool = pool
        self.embedder = embedder
        self.flow_path = flow_path
        self.recall_path = recall_path

        self.user_status: UserStatus = user_status
        self.ai_status: AiStatus = ai_status

        self._drift_threshold = drift_threshold
        self._recall_top_k = recall_top_k
        self._last_vec: np.ndarray | None = None

        # StreamGorge for real-time segmentation
        self._gorge = StreamGorge(
            max_blocks=gorge_max_blocks,
            min_depth=gorge_min_depth,
        )

        # Beat buffer (parallel to gorge's embedding buffer)
        self._beat_buf: list[Beat] = []

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
    # Beat ingestion
    # ==================================================================

    def ingest_beat(self, beat: Beat) -> str | None:
        """Process one beat: write to flow.jsonl, embed, feed gorge.

        Returns event_id if gorge cuts a segment, else None.
        """
        # 1. Append to flow.jsonl
        append_beat(self.flow_path, beat)

        # 2. Embed
        vec = self.embedder.embed(beat.text)

        # 3. Drift detection → recall refresh
        if self._last_vec is not None and beat.source not in ("action",):
            if detect_drift(self._last_vec, vec, self._drift_threshold):
                self._refresh_recall(vec)
        self._last_vec = vec

        # 4. Feed gorge + track beat
        self._beat_buf.append(beat)
        cut = self._gorge.push(vec)
        if cut is not None:
            return self._flush_segment(cut)

        return None

    def ingest_external(
        self,
        text: str,
        source: "BeatSource",
        t: datetime | None = None,
    ) -> str | None:
        """Convenience: create a beat from an external message and ingest it."""
        if t is None:
            t = datetime.now(timezone.utc)
        beat = Beat(
            t=t,
            text=text,
            source=source,
            user=self.user_status,
            ai=self.ai_status,
        )
        return self.ingest_beat(beat)

    def ingest_cc_output(
        self,
        jsonl_path: Path,
        byte_offset: int = 0,
    ) -> tuple[list[str | None], int]:
        """Parse CC JSONL → beats → ingest each.

        Returns (list of event_ids_or_None per beat, new_byte_offset).
        """
        from fiam.adapter.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        beats, new_offset = adapter.parse_beats(
            jsonl_path, byte_offset,
            user_status=self.user_status,
            ai_status=self.ai_status,
        )
        results = []
        for beat in beats:
            eid = self.ingest_beat(beat)
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

        # Timestamp = first beat's time
        t = consumed_beats[0].t if consumed_beats else datetime.now(timezone.utc)

        event_id = self.pool.new_event_id()
        self.pool.ingest_event(event_id, t, body, fp)
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
        t = beats[0].t

        event_id = self.pool.new_event_id()
        self.pool.ingest_event(event_id, t, body, fp)
        return [event_id]

    # ==================================================================
    # Recall
    # ==================================================================

    def _refresh_recall(self, query_vec: np.ndarray) -> None:
        """Run spreading activation retrieval and write recall.md."""
        results = retrieve(
            query_vec,
            self.pool,
            shield_after=datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
            top_k=self._recall_top_k,
        )
        if not results:
            return

        now = datetime.now(timezone.utc)
        lines = [f"<!-- recall | {now.strftime('%Y-%m-%dT%H:%M:%SZ')} -->", ""]

        events = self.pool.load_events()
        idx_to_event = {ev.fingerprint_idx: ev for ev in events}

        for event_id, activation in results:
            ev = self.pool.get_event(event_id)
            if ev is None:
                continue
            body = self.pool.read_body(event_id)
            fragment = body.strip()[:200]
            if len(body.strip()) > 200:
                fragment += "..."

            age = now - ev.t
            if age.days > 30:
                hint = f"{age.days // 30}个月前"
            elif age.days > 0:
                hint = f"{age.days}天前"
            elif age.seconds > 3600:
                hint = f"{age.seconds // 3600}小时前"
            else:
                hint = "刚才"

            lines.append(f"- ({hint}) {fragment}")

            # Bump access count
            ev.access_count += 1

        self.pool.save_events()

        content = "\n".join(lines) + "\n"
        self.recall_path.parent.mkdir(parents=True, exist_ok=True)
        self.recall_path.write_text(content, encoding="utf-8")

    # ==================================================================
    # CC injection preparation
    # ==================================================================

    @staticmethod
    def format_user_message(
        messages: list[tuple[str, str]],
    ) -> str:
        """Format external messages for `claude -p` user field.

        messages: list of (source_label, text) — e.g. [("tg:Zephyr", "hello")]
        Returns a single string suitable for the -p argument.
        """
        if not messages:
            return ""
        parts = []
        for label, text in messages:
            parts.append(f"[{label}] {text}")
        return "\n\n".join(parts)

    @staticmethod
    def format_additional_context(
        recall_text: str = "",
        schedule_info: str = "",
    ) -> str:
        """Format internal info for hook additionalContext.

        This is what inject.sh would output — but prepared by conductor
        so the hook just echoes it.
        """
        sections: list[str] = []
        if recall_text:
            sections.append(f"[recall]\n{recall_text}")
        if schedule_info:
            sections.append(f"[schedule]\n{schedule_info}")
        return "\n\n".join(sections)
