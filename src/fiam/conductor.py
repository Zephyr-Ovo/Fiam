"""Conductor — stateless information hub.

Every message enters through ``receive()`` / ``receive_cc()``, gets
written to SQLite events, embedded, segmented by Gorge, and stored in Pool.
Outbound messages leave via ``dispatch()``.

Conductor has **no heartbeat and no scheduling**.  It is driven entirely
by external callers (daemon, channels, dashboard).

Recall is the one exception that does NOT flow through Conductor.
Drift detection fires an ``on_drift`` callback so the owner (daemon)
can run retrieval and write recall.md independently.
"""

from __future__ import annotations

import logging
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import numpy as np

from fiam.config import FiamConfig
from fiam.gorge import StreamGorge, detect_drift
from fiam.store.beat import Beat, append_beat
from fiam.store.features import FeatureStore
from fiam.store.pool import Pool
from fiam.turn import DispatchRequest, DispatchService, TurnCommit, TurnRequest, TurnTraceStore, UiHistoryStore

if TYPE_CHECKING:
    from fiam.retriever.embedder import Embedder
    from fiam.store.beat import AiStatus, UserStatus

logger = logging.getLogger(__name__)


class Conductor:
    """Stateless information hub: receive → flow + embed + segment + store.

    Two entry points:
      - ``receive()``    — external messages (email, favilla, ...)
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
        """Process one beat: write to events, embed, feed gorge.

        Returns event_id if gorge cuts a segment, else None.
        """
        # 1. Persist event row (skip when flow_path is None, e.g. reprocess)
        event_id = None
        if self.flow_path is not None:
            event_id = append_beat(self.flow_path, beat)
            if event_id:
                meta = dict(beat.meta or {})
                meta.setdefault("event_id", event_id)
                beat = replace(beat, meta=meta)

        # 2. Embed (may fail — beat is persisted regardless)
        try:
            vec = self.embedder.embed(beat.content)
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
                if event_id:
                    from fiam.store.events import EventStore, db_path_for_flow, object_dir_for_flow
                    EventStore(
                        db_path_for_flow(self.flow_path),
                        object_dir=object_dir_for_flow(self.flow_path),
                    ).mark_embedded(
                        event_id,
                        model_id=getattr(self.config, "embedding_model", ""),
                        embedded_at=datetime.now(timezone.utc),
                    )
            except Exception:
                logger.error("feature_store append failed", exc_info=True)

        if self.memory_mode == "manual":
            return None

        # 3. Drift detection → fire callback (recall is caller's responsibility)
        if (self._last_vec is not None
                and not (beat.actor == "ai" and beat.kind == "action")
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
        # Reserved hook: no channel currently opts out of event-cut.
        return False

    def receive(
        self,
        text: str,
        channel: str,
        t: datetime | None = None,
        meta: dict | None = None,
    ) -> str | None:
        """Thin compatibility wrapper over receive_turn()."""
        if t is None:
            t = datetime.now(timezone.utc)
        meta = meta or {}
        commit = self.receive_turn(TurnRequest(
            channel=channel,
            actor=self._actor_for_channel(channel),
            text=text,
            source_meta={**meta, "t": t.isoformat()},
            received_at=t,
        ))
        first = commit.events[0].meta.get("event_id") if commit.events and commit.events[0].meta else None
        return str(first) if first else None

    def receive_turn(self, request: TurnRequest) -> TurnCommit:
        """Receive one normalized turn request and persist its fact event."""
        req = request.canonical()
        t_raw = req.source_meta.get("t") if isinstance(req.source_meta, dict) else ""
        if t_raw:
            try:
                t = datetime.fromisoformat(str(t_raw).replace("Z", "+00:00"))
            except ValueError:
                t = req.received_at
        else:
            t = req.received_at
        beat = Beat(
            t=t,
            actor=req.actor,
            channel=req.channel,
            kind="message",
            content=self._format_external_text(req.text, req.channel, req.source_meta),
            meta={
                **(req.source_meta or {}),
                "turn_id": req.turn_id,
                "request_id": req.request_id,
                "session_id": req.session_id,
            },
        )
        event_id = self._ingest_beat(beat)
        if event_id:
            beat = replace(beat, meta={**(beat.meta or {}), "event_id": event_id})
        return TurnCommit(
            turn_id=req.turn_id,
            request_id=req.request_id,
            session_id=req.session_id,
            events=(beat,),
            trace={"received": req.received_at.isoformat(), "commit_done": datetime.now(timezone.utc).isoformat()},
        )

    def commit_turn(self, commit: TurnCommit, *, channel: str = "") -> TurnCommit:
        """Commit events, clean transcript, UI read-model rows, side effects, and trace.

        This is the single turn commit boundary used by transports and runtime
        wrappers. Individual storage adapters remain small, but callers no
        longer own separate write paths for one turn.
        """
        committed_events: list[Beat] = []
        for beat in commit.events:
            meta = {
                **(beat.meta or {}),
                "turn_id": commit.turn_id,
                "request_id": commit.request_id,
                "session_id": commit.session_id,
            }
            event_id = self._ingest_beat(replace(beat, meta=meta))
            if event_id:
                committed_events.append(replace(beat, meta={**meta, "event_id": event_id}))
            else:
                committed_events.append(replace(beat, meta=meta))

        if commit.transcript_messages and self.config is not None:
            self._commit_runtime_transcript(channel, commit.transcript_messages)

        if commit.ui_history_rows and self.config is not None:
            UiHistoryStore(self.config.home_path).append_rows(channel, commit.ui_history_rows)

        for change in commit.todo_changes:
            self._commit_todo_change(change, commit)
        if commit.state_change is not None:
            self._commit_state_change(commit.state_change, commit)

        for request in commit.dispatch_requests:
            self._commit_dispatch_request(request, commit)

        trace = {
            **(commit.trace or {}),
            "commit_done": datetime.now(timezone.utc).isoformat(),
        }
        if self.config is not None:
            TurnTraceStore(self.config.store_dir / "turn_traces.jsonl").append(commit.turn_id, trace)

        return replace(commit, events=tuple(committed_events), trace=trace)

    def _commit_runtime_transcript(self, channel: str, messages: tuple[dict, ...]) -> None:
        try:
            from fiam.runtime.prompt import append_transcript_messages, trim_transcript_messages

            append_transcript_messages(self.config, channel, list(messages))
            trim_transcript_messages(self.config, channel)
        except Exception:
            logger.error("runtime transcript commit failed", exc_info=True)

    def _commit_todo_change(self, change, commit: TurnCommit) -> None:
        if self.config is None or not change.at:
            return
        try:
            at = datetime.fromisoformat(change.at.replace("Z", "+00:00"))
            at = self.config.ensure_timezone(at)
        except ValueError:
            return
        if at <= datetime.now(timezone.utc).astimezone(at.tzinfo):
            return
        row = {
            "at": at.isoformat(),
            "kind": change.kind,
            "reason": change.reason,
            "created": datetime.now(timezone.utc).isoformat(),
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "marker_index": change.marker_index,
            "idempotency_key": f"{commit.turn_id}:{change.marker_index}:{change.kind}",
        }
        path = self.config.todo_path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    existing.add(str(item.get("idempotency_key") or ""))
        if row["idempotency_key"] in existing:
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _commit_state_change(self, change, commit: TurnCommit) -> None:
        if self.config is None:
            return
        state = str(change.state or "").strip().lower()
        if state not in {"notify", "mute", "block", "sleep", "busy", "together", "online"}:
            return
        record = {
            "state": state,
            "since": self.config.now_local().isoformat(),
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "marker_index": change.marker_index,
            "idempotency_key": f"{commit.turn_id}:{change.marker_index}:state",
        }
        if change.until:
            record["until"] = change.until
        if change.reason:
            record["reason"] = change.reason
        self.config.ai_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.ai_state_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if state == "sleep":
            self.config.active_session_path.unlink(missing_ok=True)

    def _commit_dispatch_request(self, request: DispatchRequest, commit: TurnCommit) -> None:
        from fiam.plugins import resolve_dispatch_target

        target = resolve_dispatch_target(self.config, request.channel) if self.config is not None else request.channel
        if target is None:
            logger.info("dispatch skipped: plugin disabled (channel=%s)", request.channel)
            return
        request = DispatchRequest(
            channel=target,
            recipient=request.recipient,
            body=request.body,
            marker_index=request.marker_index,
            status=request.status,
            dispatch_id=request.dispatch_id,
        )
        accepted = DispatchService().event_for(
            request,
            turn_id=commit.turn_id,
            request_id=commit.request_id,
            session_id=commit.session_id,
            status="accepted",
        )
        self._ingest_beat(accepted)
        if self.bus is None:
            failed = DispatchService().event_for(
                request,
                turn_id=commit.turn_id,
                request_id=commit.request_id,
                session_id=commit.session_id,
                status="failed",
                attempts=0,
                last_error="no bus configured",
            )
            self._ingest_beat(failed)
            return
        ok = DispatchService(self.bus).publish(request)
        status = "published" if ok else "failed"
        followup = DispatchService().event_for(
            request,
            turn_id=commit.turn_id,
            request_id=commit.request_id,
            session_id=commit.session_id,
            status=status,
            attempts=1,
            last_error="" if ok else "bus rejected payload",
        )
        self._ingest_beat(followup)

    @staticmethod
    def _actor_for_channel(channel: str) -> str:
        """Default actor for inbound beats by channel."""
        from fiam.channels import actor_for_channel

        return actor_for_channel(channel)

    def _format_external_text(self, text: str, channel: str, meta: dict) -> str:
        speaker = str(meta.get("speaker") or "").strip()
        if not speaker:
            actor = self._actor_for_channel(channel)
            if actor == "user":
                speaker = (self.config.user_name or "zephyr").strip()
            else:
                speaker = str(meta.get("from_name") or channel).strip()
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
            user_name=getattr(self.config, "user_name", "") or "zephyr",
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
        body = "\n".join(b.content for b in consumed_beats)

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

        body = "\n".join(b.content for b in beats)
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

    def dispatch(self, channel: str, recipient: str, text: str, *, turn_id: str = "", request_id: str = "", session_id: str = "") -> bool:
        """Send outbound message via MQTT to the appropriate channel bridge.

        Publishes to ``fiam/dispatch/<target>``. The target bridge process
        (bridge_email, ...) subscribes and performs the actual
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
        dispatch_request = DispatchRequest(channel=target, recipient=recipient, body=text)
        try:
            self._ingest_beat(DispatchService().event_for(
                dispatch_request,
                turn_id=turn_id,
                request_id=request_id,
                session_id=session_id,
                status="accepted",
            ))
        except Exception:
            logger.error("dispatch event persist failed", exc_info=True)
        if self.bus is None:
            logger.error("dispatch: no bus configured (channel=%s)", target)
            try:
                self._ingest_beat(DispatchService().event_for(
                    dispatch_request,
                    turn_id=turn_id,
                    request_id=request_id,
                    session_id=session_id,
                    status="failed",
                    attempts=0,
                    last_error="no bus configured",
                ))
            except Exception:
                logger.error("dispatch no-bus event persist failed", exc_info=True)
            return False
        ok = self.bus.publish_dispatch(target, payload)
        try:
            self._ingest_beat(DispatchService().event_for(
                dispatch_request,
                turn_id=turn_id,
                request_id=request_id,
                session_id=session_id,
                status="published" if ok else "failed",
                attempts=1,
                last_error="" if ok else "bus rejected payload",
            ))
        except Exception:
            logger.error("dispatch status event persist failed", exc_info=True)
        if not ok:
            logger.warning("dispatch: bus rejected payload (channel=%s)", target)
        return ok
