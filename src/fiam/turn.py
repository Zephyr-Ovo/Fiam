"""Turn-level contracts for the Fiam data pipeline.

This module is intentionally small and dependency-light: transport adapters,
runtime adapters, marker interpretation, persistence, dispatch, and memory work
can share these structures without importing the dashboard server.
"""

from __future__ import annotations

import uuid
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

    def event_for(self, request: DispatchRequest, *, turn_id: str = "", request_id: str = "") -> Beat:
        return Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=request.channel,
            kind="message",
            content=request.body,
            meta={
                "turn_id": turn_id,
                "request_id": request_id,
                "dispatch_target": request.channel,
                "dispatch_recipient": request.recipient,
                "dispatch_status": request.status,
                "dispatch_attempts": 0,
            },
        )

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
        import json

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


class MemoryWorker:
    """Idempotent worker boundary for async memory processing."""

    def __init__(self, event_store: object | None = None) -> None:
        self.event_store = event_store

    def pending_query(self) -> str:
        return "SELECT id FROM events WHERE embedded_at = '' ORDER BY t ASC"

    def process_once(self) -> int:
        return 0
