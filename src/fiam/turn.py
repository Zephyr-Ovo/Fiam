"""Turn-level contracts for the Fiam data pipeline.

This module is intentionally small and dependency-light: transport adapters,
runtime adapters, marker interpretation, persistence, dispatch, and memory work
can share these structures without importing the dashboard server.
"""

from __future__ import annotations

import uuid
import json
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fiam.channels import channel_spec, normalize_channel
from fiam.markers import (
    parse_cot_markers,
    parse_hold_markers,
    parse_outbound_markers,
    parse_route_markers,
    parse_sleep_markers,
    parse_state_markers,
    parse_todo_markers,
    parse_wake_markers,
    strip_xml_markers,
)
from fiam.store.beat import Beat


DeliveryPolicy = Literal["record_only", "lazy", "instant", "batch", "state_only"]
FrameKind = Literal[
    "start",
    "tool_use",
    "tool_result",
    "thought",
    "text_delta",
    "progress",
    "commit",
    "done",
    "error",
]


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    object_hash: str = ""
    name: str = ""
    mime: str = ""
    size: int = 0
    path: str = ""


@dataclass(frozen=True, slots=True)
class TurnRequest:
    channel: str
    actor: str
    text: str
    turn_id: str = field(default_factory=lambda: f"turn_{uuid.uuid4().hex}")
    request_id: str = ""
    session_id: str = ""
    attachments: tuple[AttachmentRef, ...] = ()
    structured_payload: dict[str, Any] = field(default_factory=dict)
    source_meta: dict[str, Any] = field(default_factory=dict)
    delivery_policy: DeliveryPolicy = "instant"
    trace: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def canonical(self) -> "TurnRequest":
        return TurnRequest(
            channel=normalize_channel(self.channel),
            actor=self.actor,
            text=self.text,
            turn_id=self.turn_id,
            request_id=self.request_id,
            session_id=self.session_id,
            attachments=self.attachments,
            structured_payload=self.structured_payload,
            source_meta=self.source_meta,
            delivery_policy=self.delivery_policy,
            trace=self.trace,
            received_at=self.received_at,
        )


@dataclass(frozen=True, slots=True)
class ToolEvent:
    kind: Literal["tool_use", "tool_result", "tool_action"]
    tool_name: str = ""
    tool_id: str = ""
    input_summary: str = ""
    result_summary: str = ""
    result: str = ""
    is_error: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamFrame:
    kind: FrameKind
    data: dict[str, Any] = field(default_factory=dict)
    turn_id: str = ""
    request_id: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class TurnResult:
    raw_text: str = ""
    visible_text: str = ""
    runtime: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    tool_events: tuple[ToolEvent, ...] = ()
    stream_frames: tuple[StreamFrame, ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    channel: str
    recipient: str
    body: str
    marker_index: int = 0
    status: str = "accepted"
    dispatch_id: str = ""


@dataclass(frozen=True, slots=True)
class TodoChange:
    at: str
    kind: str
    reason: str = ""
    marker_index: int = 0


@dataclass(frozen=True, slots=True)
class StateChange:
    state: str
    until: str = ""
    reason: str = ""
    marker_index: int = 0


@dataclass(frozen=True, slots=True)
class MarkerInterpretation:
    visible_reply: str
    private_thoughts: tuple[str, ...] = ()
    dispatch_requests: tuple[DispatchRequest, ...] = ()
    todo_changes: tuple[TodoChange, ...] = ()
    state_change: StateChange | None = None
    route_hint: dict[str, str] | None = None
    hold_reason: str = ""

    @property
    def held(self) -> bool:
        return bool(self.hold_reason)


@dataclass(frozen=True, slots=True)
class TurnCommit:
    turn_id: str
    request_id: str = ""
    session_id: str = ""
    events: tuple[Beat, ...] = ()
    transcript_messages: tuple[dict[str, Any], ...] = ()
    ui_history_rows: tuple[dict[str, Any], ...] = ()
    dispatch_requests: tuple[DispatchRequest, ...] = ()
    todo_changes: tuple[TodoChange, ...] = ()
    state_change: StateChange | None = None
    trace: dict[str, Any] = field(default_factory=dict)


class MarkerInterpreter:
    """Single parser for model-authored XML markers."""

    CONTROL_NAMES = {"send", "cot", "hold", "todo", "wake", "sleep", "state", "route", "lock"}

    def interpret(self, text: str) -> MarkerInterpretation:
        dispatches = tuple(
            DispatchRequest(
                channel=marker.channel,
                recipient=marker.recipient,
                body=marker.body,
                marker_index=i,
            )
            for i, marker in enumerate(parse_outbound_markers(text))
        )
        holds = parse_hold_markers(text)
        hold_reason = ""
        if holds:
            hold_reason = holds[-1].reason or "held reply"
        route_markers = parse_route_markers(text)
        route_hint = None
        if route_markers:
            marker = route_markers[-1]
            route_hint = {"family": marker.family, "reason": marker.reason}
        todo_changes: list[TodoChange] = []
        marker_index = 0
        for marker in parse_todo_markers(text):
            todo_changes.append(TodoChange(at=marker.at, kind="todo", reason=marker.text, marker_index=marker_index))
            marker_index += 1
        for marker in parse_wake_markers(text):
            todo_changes.append(TodoChange(at=marker.at, kind="wake", reason=marker.reason, marker_index=marker_index))
            marker_index += 1
        for marker in parse_sleep_markers(text):
            todo_changes.append(TodoChange(at=marker.at, kind="sleep", reason=marker.reason, marker_index=marker_index))
            marker_index += 1
        state_markers = parse_state_markers(text)
        state_change = None
        if state_markers:
            marker = state_markers[-1]
            state_change = StateChange(state=marker.state, until=marker.until, reason=marker.reason, marker_index=len(state_markers) - 1)
        visible = strip_xml_markers(text or "", self.CONTROL_NAMES).strip()
        if hold_reason:
            visible = ""
        return MarkerInterpretation(
            visible_reply=visible,
            private_thoughts=tuple(parse_cot_markers(text)),
            dispatch_requests=dispatches,
            todo_changes=tuple(todo_changes),
            state_change=state_change,
            route_hint=route_hint,
            hold_reason=hold_reason,
        )


class DispatchService:
    """Create dispatch facts before publishing to a bus."""

    def __init__(self, bus: object | None = None) -> None:
        self.bus = bus

    def event_for(
        self,
        request: DispatchRequest,
        *,
        turn_id: str = "",
        request_id: str = "",
        session_id: str = "",
        status: str | None = None,
        attempts: int = 0,
        last_error: str = "",
    ) -> Beat:
        status_value = status or request.status
        dispatch_id = request.dispatch_id or self.dispatch_id_for(
            turn_id=turn_id,
            marker_index=request.marker_index,
            target=request.channel,
            recipient=request.recipient,
        )
        return Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=request.channel,
            kind="dispatch",
            content=request.body,
            meta={
                "turn_id": turn_id,
                "request_id": request_id,
                "session_id": session_id,
                "name": "dispatch",
                "dispatch_id": dispatch_id,
                "dispatch_target": request.channel,
                "dispatch_recipient": request.recipient,
                "dispatch_status": status_value,
                "dispatch_attempts": attempts,
                "dispatch_last_error": last_error,
            },
        )

    @staticmethod
    def dispatch_id_for(*, turn_id: str, marker_index: int, target: str, recipient: str) -> str:
        seed = f"{turn_id}:{marker_index}:{target}:{recipient}"
        return "disp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    def publish(self, request: DispatchRequest) -> bool:
        if self.bus is None:
            return False
        publish = getattr(self.bus, "publish_dispatch", None)
        if publish is None:
            return False
        return bool(publish(request.channel, {"recipient": request.recipient, "text": request.body}))


class TriggerPolicy:
    """Central trigger policy for record vs wake decisions."""

    def decide(
        self,
        channel: str,
        *,
        ai_state: str = "notify",
        delivery: DeliveryPolicy | None = None,
        interactive: bool = False,
    ) -> DeliveryPolicy:
        if delivery:
            return delivery
        spec = channel_spec(channel)
        if not spec.responds:
            return "record_only"
        if ai_state in {"block"}:
            return "record_only"
        if ai_state in {"sleep", "mute"}:
            return "lazy"
        if interactive:
            return "batch"
        return "instant"


class InboundQueue:
    """Durable JSONL queue for incoming turn requests."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def enqueue(self, request: TurnRequest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "turn_id": request.turn_id,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "channel": normalize_channel(request.channel),
            "actor": request.actor,
            "text": request.text,
            "source_meta": request.source_meta,
            "delivery_policy": request.delivery_policy,
            "trace": request.trace,
            "received_at": request.received_at.isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def drain(self, *, limit: int = 100) -> list[TurnRequest]:
        """Pop up to ``limit`` queued requests in FIFO order."""
        if not self.path.exists():
            return []
        rows = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = rows[: max(0, int(limit))]
        remaining = rows[len(selected):]
        requests: list[TurnRequest] = []
        for line in selected:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            received_at = datetime.now(timezone.utc)
            raw_received = str(payload.get("received_at") or "")
            if raw_received:
                try:
                    received_at = datetime.fromisoformat(raw_received.replace("Z", "+00:00"))
                except ValueError:
                    pass
            requests.append(TurnRequest(
                channel=str(payload.get("channel") or "favilla"),
                actor=str(payload.get("actor") or "user"),
                text=str(payload.get("text") or ""),
                turn_id=str(payload.get("turn_id") or f"turn_{uuid.uuid4().hex}"),
                request_id=str(payload.get("request_id") or ""),
                session_id=str(payload.get("session_id") or ""),
                source_meta=payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {},
                delivery_policy=payload.get("delivery_policy") if payload.get("delivery_policy") in {"record_only", "lazy", "instant", "batch", "state_only"} else "instant",
                trace=payload.get("trace") if isinstance(payload.get("trace"), dict) else {},
                received_at=received_at,
            ))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(("\n".join(remaining) + "\n") if remaining else "", encoding="utf-8")
        return requests


class UiHistoryStore:
    """Append-only UI read model generated from TurnCommit."""

    def __init__(self, home_path: Path) -> None:
        self.home_path = home_path

    def append_rows(self, channel: str, rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        if not rows:
            return []
        source = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in (channel or "chat").strip().lower()).strip("_") or "chat"
        path = self.home_path / "transcript" / f"{source}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        written: list[dict[str, Any]] = []
        now_min = int(datetime.now(timezone.utc).timestamp() // 60)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                record = {
                    "id": str(row.get("id") or f"turn-{uuid.uuid4().hex}"),
                    "role": str(row.get("role") or "ai"),
                    "t": int(row.get("t") or now_min),
                    **{k: v for k, v in row.items() if v not in (None, [], "") and k not in {"id", "role", "t"}},
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written.append(record)
        return written


class TurnTraceStore:
    """Durable turn-phase trace rows for observability."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, turn_id: str, trace: dict[str, Any]) -> None:
        if not turn_id or not trace:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "turn_id": turn_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "trace": trace,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass(frozen=True, slots=True)
class SummaryRuntimeConfig:
    provider: str = ""
    model: str = ""
    api_key_env: str = "FIAM_SUMMARY_API_KEY"
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "SummaryRuntimeConfig":
        return cls(
            provider=os.environ.get("FIAM_SUMMARY_PROVIDER", "mimo").strip(),
            model=os.environ.get("FIAM_SUMMARY_MODEL", "").strip(),
            api_key_env="FIAM_SUMMARY_API_KEY",
            base_url=os.environ.get("FIAM_SUMMARY_BASE_URL", "").strip(),
        )


class MemoryWorker:
    """Idempotent worker boundary for async memory processing."""

    def __init__(self, event_store: object | None = None, *, embedder: object | None = None, feature_store: object | None = None, model_id: str = "", summary_config: SummaryRuntimeConfig | None = None) -> None:
        self.event_store = event_store
        self.embedder = embedder
        self.feature_store = feature_store
        self.model_id = model_id
        self.summary_config = summary_config or SummaryRuntimeConfig.from_env()

    def pending_query(self) -> str:
        return "SELECT id FROM events WHERE embedded_at = '' ORDER BY t ASC"

    def process_once(self, *, limit: int = 100) -> int:
        if self.event_store is None or self.embedder is None:
            return 0
        read_unembedded = getattr(self.event_store, "read_unembedded", None)
        mark_embedded = getattr(self.event_store, "mark_embedded", None)
        if read_unembedded is None or mark_embedded is None:
            return 0
        processed = 0
        for beat in read_unembedded(limit=limit):
            event_id = str((beat.meta or {}).get("event_id") or "")
            if not event_id:
                continue
            vec = self.embedder.embed(beat.content)
            if self.feature_store is not None:
                self.feature_store.append_beat_vector(beat, vec, model_id=self.model_id)
            mark_embedded(event_id, model_id=self.model_id, embedded_at=datetime.now(timezone.utc))
            processed += 1
        return processed
