"""Conductor — stateless information hub.

Every message enters through ``receive()`` / ``receive_cc()`` or a
``TurnCommit``, gets written to SQLite events, embedded, segmented by Gorge,
and stored in Pool. Outbound messages leave through TurnCommit dispatch
requests.

Conductor has **no heartbeat and no scheduling**.  It is driven entirely
by external callers (daemon, channels, dashboard).

Recall is the one exception that does NOT flow through Conductor.
Drift detection fires an ``on_drift`` callback so the owner (daemon)
can build a one-shot recall context independently.
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
from fiam.store.pool import Pool
from fiam.turn import DispatchRequest, DispatchService, TurnCommit, TurnRequest, TurnTraceRow, TurnTraceStore, UiHistoryStore

if TYPE_CHECKING:
    from fiam.retriever.embedder import Embedder
    from fiam.store.beat import AiStatus, UserStatus

logger = logging.getLogger(__name__)


class Conductor:
    """Stateless information hub: receive → flow + embed + segment + store.

    Two entry points:
      - ``receive()``    — external messages (email, chat, browser, ...)
      - ``receive_cc()`` — Claude Code JSONL delta

        One exit:
            - ``TurnCommit.dispatch_requests`` — send outbound messages via bridges

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
    ) -> None:
        self.pool = pool
        self.embedder = embedder
        self.config = config
        self.flow_path = flow_path
        self.bus = bus  # fiam.bus.Bus | None — optional, for dispatch publishing
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
            surface=req.surface,
            meta={
                **(req.source_meta or {}),
                "surface": req.surface,
                "turn_id": req.turn_id,
                "request_id": req.request_id,
                "session_id": req.session_id,
            },
        )
        event_id = self._ingest_beat(beat)
        if event_id:
            beat = replace(beat, meta={**(beat.meta or {}), "event_id": event_id})
        committed_events = [beat]
        for attachment_beat in self._inbound_attachment_beats(req, t):
            attachment_event_id = self._ingest_beat(attachment_beat)
            if attachment_event_id:
                attachment_beat = replace(attachment_beat, meta={**(attachment_beat.meta or {}), "event_id": attachment_event_id})
            committed_events.append(attachment_beat)
        return TurnCommit(
            turn_id=req.turn_id,
            request_id=req.request_id,
            session_id=req.session_id,
            events=tuple(committed_events),
            trace={
                "turn_id": req.turn_id,
                "request_id": req.request_id,
                "session_id": req.session_id,
                "received_at": req.received_at.isoformat(),
            },
        )

    def _inbound_attachment_beats(self, request: TurnRequest, t: datetime) -> tuple[Beat, ...]:
        beats: list[Beat] = []
        seen: set[str] = set()
        for index, attachment in enumerate(request.attachments):
            object_hash = "".join(ch for ch in str(attachment.object_hash or "").lower() if ch in "0123456789abcdef")
            if len(object_hash) != 64 or object_hash in seen:
                continue
            seen.add(object_hash)
            name = attachment.name or object_hash[:12] or "attachment"
            meta = {
                **(request.source_meta or {}),
                "surface": request.surface,
                "turn_id": request.turn_id,
                "request_id": request.request_id,
                "session_id": request.session_id,
                "direction": "inbound",
                "source": "turn_request",
                "attachment_index": index,
                "object_hash": object_hash,
                "object_name": name,
                "object_mime": attachment.mime,
                "object_size": attachment.size,
            }
            beats.append(Beat(
                t=t,
                actor=request.actor,
                channel=request.channel,
                kind="attachment",
                content=f"attachment: {name}",
                surface=request.surface,
                meta=meta,
            ))
        return tuple(beats)

    def commit_turn(self, commit: TurnCommit, *, channel: str = "") -> TurnCommit:
        """Commit events, clean transcript, UI read-model rows, side effects, and trace.

        This is the single turn commit boundary used by transports and runtime
        wrappers. Individual storage adapters remain small, but callers no
        longer own separate write paths for one turn.
        """
        trace_rows: list[TurnTraceRow] = []
        trace_context = {
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "session_id": commit.session_id,
            "channel": channel,
            "surface": commit.surface,
        }

        def trace_phase(phase: str, *, status: str = "ok", started_at: datetime | None = None, error: str = "", refs: dict | None = None) -> None:
            ended_at = datetime.now(timezone.utc)
            started = started_at or ended_at
            duration_ms = max(0, int((ended_at - started).total_seconds() * 1000))
            trace_rows.append(TurnTraceRow(
                **trace_context,
                phase=phase,
                status=status if status in {"ok", "error", "skipped"} else "error",
                started_at=started.isoformat(),
                ended_at=ended_at.isoformat(),
                duration_ms=duration_ms,
                error=error,
                refs=refs or {},
            ))

        def flush_trace_rows() -> None:
            if self.config is not None and trace_rows:
                TurnTraceStore(self.config.store_dir / "turn_traces.jsonl").append_many(trace_rows)

        trace_phase("commit.start")

        committed_events: list[Beat] = []
        events_started = datetime.now(timezone.utc)
        try:
            for beat in commit.events:
                meta = {
                    **(beat.meta or {}),
                    "surface": beat.surface or commit.surface,
                    "turn_id": commit.turn_id,
                    "request_id": commit.request_id,
                    "session_id": commit.session_id,
                }
                event_id = self._ingest_beat(replace(beat, meta=meta, surface=beat.surface or commit.surface))
                if event_id:
                    committed_events.append(replace(beat, meta={**meta, "event_id": event_id}, surface=beat.surface or commit.surface))
                else:
                    committed_events.append(replace(beat, meta=meta, surface=beat.surface or commit.surface))
        except Exception as exc:
            trace_phase("commit.events", started_at=events_started, status="error", error=str(exc)[:1000])
            flush_trace_rows()
            raise
        trace_phase("commit.events", started_at=events_started, status="ok" if commit.events else "skipped", refs={"event_ids": [str((beat.meta or {}).get("event_id") or "") for beat in committed_events]})

        if commit.transcript_messages and self.config is not None:
            transcript_started = datetime.now(timezone.utc)
            ok, error = self._commit_runtime_transcript(channel, commit.transcript_messages)
            trace_phase("commit.transcript", started_at=transcript_started, status="ok" if ok else "error", error=error, refs={"message_count": len(commit.transcript_messages)})
        else:
            trace_phase("commit.transcript", status="skipped")

        if commit.ui_history_rows and self.config is not None:
            ui_started = datetime.now(timezone.utc)
            try:
                UiHistoryStore(self.config.home_path).append_rows(channel, commit.ui_history_rows)
            except Exception as exc:
                trace_phase("commit.ui", started_at=ui_started, status="error", error=str(exc)[:1000], refs={"row_count": len(commit.ui_history_rows)})
                flush_trace_rows()
                raise
            else:
                trace_phase("commit.ui", started_at=ui_started, refs={"row_count": len(commit.ui_history_rows)})
        else:
            trace_phase("commit.ui", status="skipped")

        todo_started = datetime.now(timezone.utc)
        try:
            for change in commit.todo_changes:
                self._commit_todo_change(change, commit, channel=channel)
        except Exception as exc:
            trace_phase("commit.todo", started_at=todo_started, status="error", error=str(exc)[:1000], refs={"change_count": len(commit.todo_changes)})
            flush_trace_rows()
            raise
        trace_phase("commit.todo", started_at=todo_started, status="ok" if commit.todo_changes else "skipped", refs={"change_count": len(commit.todo_changes)})

        if commit.state_change is not None:
            state_started = datetime.now(timezone.utc)
            try:
                self._commit_state_change(commit.state_change, commit, channel=channel)
            except Exception as exc:
                trace_phase("commit.state", started_at=state_started, status="error", error=str(exc)[:1000], refs={"state": commit.state_change.state})
                flush_trace_rows()
                raise
            else:
                trace_phase("commit.state", started_at=state_started, refs={"state": commit.state_change.state})
        else:
            trace_phase("commit.state", status="skipped")

        if commit.hold_request is not None:
            hold_started = datetime.now(timezone.utc)
            try:
                self._commit_hold_request(commit.hold_request, commit, channel=channel)
            except Exception as exc:
                trace_phase("commit.hold", started_at=hold_started, status="error", error=str(exc)[:1000], refs={"status": commit.hold_request.status})
                flush_trace_rows()
                raise
            else:
                trace_phase("commit.hold", started_at=hold_started, refs={"status": commit.hold_request.status})
        else:
            trace_phase("commit.hold", status="skipped")

        dispatch_started = datetime.now(timezone.utc)
        try:
            for request in commit.dispatch_requests:
                self._commit_dispatch_request(request, commit)
        except Exception as exc:
            trace_phase("commit.dispatch", started_at=dispatch_started, status="error", error=str(exc)[:1000], refs={"dispatch_count": len(commit.dispatch_requests)})
            flush_trace_rows()
            raise
        trace_phase("commit.dispatch", started_at=dispatch_started, status="ok" if commit.dispatch_requests else "skipped", refs={"dispatch_count": len(commit.dispatch_requests)})

        trace_phase(
            "commit.input_trace",
            status="ok" if commit.trace else "skipped",
            refs={"keys": sorted(str(key) for key in (commit.trace or {}).keys())},
        )
        trace = {
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "session_id": commit.session_id,
            "trace_file": "store/turn_traces.jsonl",
            "commit_done": datetime.now(timezone.utc).isoformat(),
        }
        if self.config is not None:
            trace_phase("commit.trace", refs={"row_count": len(trace_rows) + 1, "trace_file": trace["trace_file"]})
            trace_phase("commit.done")
            flush_trace_rows()

        return replace(commit, events=tuple(committed_events), trace=trace)

    def _commit_runtime_transcript(self, channel: str, messages: tuple[dict, ...]) -> tuple[bool, str]:
        try:
            from fiam.runtime.prompt import append_transcript_messages, trim_transcript_messages

            append_transcript_messages(self.config, channel, list(messages))
            trim_transcript_messages(self.config, channel)
            return True, ""
        except Exception as exc:
            logger.error("runtime transcript commit failed", exc_info=True)
            return False, str(exc)[:1000]

    def _commit_todo_change(self, change, commit: TurnCommit, *, channel: str) -> None:
        if self.config is None or not change.at:
            return
        try:
            at = datetime.fromisoformat(change.at.replace("Z", "+00:00"))
            at = self.config.ensure_timezone(at)
        except ValueError:
            return
        if at <= datetime.now(timezone.utc).astimezone(at.tzinfo):
            return
        idempotency_key = f"{commit.turn_id}:{change.marker_index}:{change.kind}"
        row = {
            "at": at.isoformat(),
            "kind": change.kind,
            "reason": change.reason,
            "created": datetime.now(timezone.utc).isoformat(),
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "session_id": commit.session_id,
            "surface": commit.surface,
            "marker_index": change.marker_index,
            "idempotency_key": idempotency_key,
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
        self._ingest_beat(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel or (commit.events[0].channel if commit.events else "schedule"),
            kind="schedule",
            content=change.reason or change.kind,
            meta={
                **row,
                "schedule_kind": change.kind,
                "schedule_at": at.isoformat(),
                "fact_kind": "schedule",
            },
            surface=commit.surface,
        ))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _commit_state_change(self, change, commit: TurnCommit, *, channel: str = "") -> None:
        if self.config is None:
            return
        state = str(change.state or "").strip().lower()
        if state not in {"notify", "mute", "block", "sleep", "busy", "together", "online"}:
            return
        idempotency_key = f"{commit.turn_id}:{change.marker_index}:state"
        record = {
            "state": state,
            "since": self.config.now_local().isoformat(),
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "session_id": commit.session_id,
            "surface": commit.surface,
            "marker_index": change.marker_index,
            "idempotency_key": idempotency_key,
        }
        if change.until:
            record["until"] = change.until
        if change.reason:
            record["reason"] = change.reason
        self._ingest_beat(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel or (commit.events[0].channel if commit.events else "state"),
            kind="state",
            content=state,
            meta={**record, "fact_kind": "state"},
            surface=commit.surface,
        ))
        self.config.ai_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.ai_state_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if state == "sleep":
            self.config.active_session_path.unlink(missing_ok=True)

    def _commit_hold_request(self, request, commit: TurnCommit, *, channel: str) -> None:
        if self.config is None:
            return
        from fiam.store.objects import ObjectStore

        status = request.status if request.status in {"reroll", "held"} else "held"
        raw_text = str(request.raw_text or "")
        object_hash = ObjectStore(self.config.object_dir).put_text(raw_text, suffix=".txt") if raw_text else ""
        summary = (request.summary or request.reason or status).strip()[:240]
        idempotency_key = f"{commit.turn_id}:{request.marker_index}:hold:{request.attempt_index}:{status}"
        record = {
            "hold_status": status,
            "reason": request.reason,
            "summary": summary,
            "object_hash": object_hash,
            "attempt_index": request.attempt_index,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "turn_id": commit.turn_id,
            "request_id": commit.request_id,
            "session_id": commit.session_id,
            "surface": commit.surface,
            "marker_index": request.marker_index,
            "idempotency_key": idempotency_key,
        }
        self._ingest_beat(Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=channel or (commit.events[0].channel if commit.events else "chat"),
            kind="hold",
            content=summary,
            meta={**record, "fact_kind": "hold", "private": True},
            surface=commit.surface,
        ))
        if status != "held":
            return
        path = self.config.held_path
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
        if idempotency_key in existing:
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**record, "status": "open"}, ensure_ascii=False, sort_keys=True) + "\n")

    def _commit_dispatch_request(self, request: DispatchRequest, commit: TurnCommit) -> None:
        from fiam.plugins import dispatch_supports_capability, resolve_dispatch_target

        if request.attachments and not dispatch_supports_capability(self.config, request.channel, "dispatch_attachment"):
            failed = DispatchService().event_for(
                request,
                turn_id=commit.turn_id,
                request_id=commit.request_id,
                session_id=commit.session_id,
                status="failed",
                attempts=0,
                last_error=f"dispatch target {request.channel} does not support attachments",
            )
            self._ingest_beat(failed)
            self._append_dispatch_trace(
                "dispatch.failed",
                request,
                commit,
                error=f"dispatch target {request.channel} does not support attachments",
                refs={"reason": "missing_dispatch_attachment_capability"},
            )
            return
        target = resolve_dispatch_target(self.config, request.channel) if self.config is not None else request.channel
        if target is None:
            logger.info("dispatch skipped: plugin disabled (channel=%s)", request.channel)
            failed = DispatchService().event_for(
                request,
                turn_id=commit.turn_id,
                request_id=commit.request_id,
                session_id=commit.session_id,
                status="failed",
                attempts=0,
                last_error="plugin disabled",
            )
            self._ingest_beat(failed)
            self._append_dispatch_trace("dispatch.failed", request, commit, error="plugin disabled", refs={"reason": "plugin_disabled"})
            return
        request = DispatchRequest(
            channel=target,
            recipient=request.recipient,
            body=request.body,
            marker_index=request.marker_index,
            status=request.status,
            dispatch_id=request.dispatch_id,
            attachments=request.attachments,
            attachment_errors=request.attachment_errors,
        )
        if request.attachment_errors:
            failed = DispatchService().event_for(
                request,
                turn_id=commit.turn_id,
                request_id=commit.request_id,
                session_id=commit.session_id,
                status="failed",
                attempts=0,
                last_error="; ".join(request.attachment_errors)[:1000],
            )
            self._ingest_beat(failed)
            self._append_dispatch_trace("dispatch.failed", request, commit, error="; ".join(request.attachment_errors)[:1000], refs={"reason": "attachment_errors"})
            return
        for attachment_event in DispatchService().attachment_events_for(
            request,
            turn_id=commit.turn_id,
            request_id=commit.request_id,
            session_id=commit.session_id,
        ):
            self._ingest_beat(attachment_event)
        accepted = DispatchService().event_for(
            request,
            turn_id=commit.turn_id,
            request_id=commit.request_id,
            session_id=commit.session_id,
            status="accepted",
        )
        self._ingest_beat(accepted)
        self._append_dispatch_trace("dispatch.accepted", request, commit)
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
            self._append_dispatch_trace("dispatch.failed", request, commit, error="no bus configured", refs={"reason": "no_bus"})
            return
        ok = DispatchService(self.bus).publish(
            request,
            turn_id=commit.turn_id,
            request_id=commit.request_id,
            session_id=commit.session_id,
        )
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
        self._append_dispatch_trace("dispatch.published" if ok else "dispatch.failed", request, commit, error="" if ok else "bus rejected payload", refs={"attempts": 1})

    def _append_dispatch_trace(self, phase: str, request: DispatchRequest, commit: TurnCommit, *, error: str = "", refs: dict | None = None) -> None:
        if self.config is None:
            return
        dispatch_id = request.dispatch_id or DispatchService().dispatch_id_for(
            turn_id=commit.turn_id,
            marker_index=request.marker_index,
            target=request.channel,
            recipient=request.recipient,
        )
        row_refs = {
            "dispatch_id": dispatch_id,
            "target": request.channel,
            "recipient": request.recipient,
            "attachment_hashes": [attachment.object_hash for attachment in request.attachments],
        }
        row_refs.update(refs or {})
        now = datetime.now(timezone.utc)
        try:
            TurnTraceStore(self.config.store_dir / "turn_traces.jsonl").append(TurnTraceRow(
                turn_id=commit.turn_id,
                request_id=commit.request_id,
                session_id=commit.session_id,
                channel=request.channel,
                surface=commit.surface,
                phase=phase,
                status="error" if error else "ok",
                started_at=now.isoformat(),
                ended_at=now.isoformat(),
                error=error,
                refs=row_refs,
            ))
        except Exception:
            logger.debug("dispatch trace append failed", exc_info=True)

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
            self.pool.ingest_event(
                event_id,
                t,
                body,
                fp,
                channel=consumed_beats[0].channel if consumed_beats else "",
                surface=consumed_beats[0].surface if consumed_beats else "",
                source_event_ids=[str((beat.meta or {}).get("event_id")) for beat in consumed_beats if (beat.meta or {}).get("event_id")],
                object_refs=[str(ref) for beat in consumed_beats for ref in ((beat.meta or {}).get("object_refs") or ())],
            )
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
        self.pool.ingest_event(
            event_id,
            t,
            body,
            fp,
            channel=beats[0].channel if beats else "",
            surface=beats[0].surface if beats else "",
            source_event_ids=[str((beat.meta or {}).get("event_id")) for beat in beats if (beat.meta or {}).get("event_id")],
            object_refs=[str(ref) for beat in beats for ref in ((beat.meta or {}).get("object_refs") or ())],
        )
        self._post_ingest([event_id])
        return [event_id]

    # ==================================================================
    # Post-ingest: edge generation + DS naming
    # ==================================================================

    def _post_ingest(self, event_ids: list[str]) -> None:
        """Enqueue graph build jobs after new pool events are created."""
        if self.config is None:
            return  # test environment — skip edge generation
        if self.memory_mode == "manual":
            return
        try:
            from fiam.store.events import EventStore

            event_store = EventStore(self.config.event_db_path, object_dir=self.config.object_dir)
            for event_id in event_ids:
                event_store.enqueue_memory_job(event_id, kind="pool_graph")
        except Exception:
            logger.error("post_ingest graph job enqueue failed for %s", event_ids, exc_info=True)
