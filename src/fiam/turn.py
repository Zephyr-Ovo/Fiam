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
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

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
TraceStatus = Literal["ok", "error", "skipped"]
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

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"object_hash": self.object_hash}
        if self.name:
            payload["name"] = self.name
        if self.mime:
            payload["mime"] = self.mime
        if self.size:
            payload["size"] = self.size
        return payload


@dataclass(frozen=True, slots=True)
class TurnRequest:
    channel: str
    actor: str
    text: str
    surface: str = ""
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
            surface=(self.surface or "").strip().lower(),
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
    attachments: tuple[AttachmentRef, ...] = ()
    attachment_errors: tuple[str, ...] = ()


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
class HoldRequest:
    status: str
    reason: str = ""
    raw_text: str = ""
    summary: str = ""
    attempt_index: int = 0
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
    hold_status: str = ""
    hold_request: HoldRequest | None = None

    @property
    def held(self) -> bool:
        return self.hold_request is not None


@dataclass(frozen=True, slots=True)
class TurnCommit:
    turn_id: str
    surface: str = ""
    request_id: str = ""
    session_id: str = ""
    events: tuple[Beat, ...] = ()
    transcript_messages: tuple[dict[str, Any], ...] = ()
    ui_history_rows: tuple[dict[str, Any], ...] = ()
    dispatch_requests: tuple[DispatchRequest, ...] = ()
    todo_changes: tuple[TodoChange, ...] = ()
    state_change: StateChange | None = None
    hold_request: HoldRequest | None = None
    trace: dict[str, Any] = field(default_factory=dict)


class MarkerInterpreter:
    """Single parser for model-authored XML markers."""

    CONTROL_NAMES = {"send", "cot", "hold", "held", "todo", "wake", "sleep", "state", "route", "lock", "voice"}

    def __init__(self, object_resolver: Callable[[str], str] | None = None) -> None:
        self.object_resolver = object_resolver

    def interpret(self, text: str) -> MarkerInterpretation:
        dispatches: list[DispatchRequest] = []
        for i, marker in enumerate(parse_outbound_markers(text)):
            attachments, attachment_errors = self._attachment_refs(marker.attachments)
            dispatches.append(DispatchRequest(
                channel=marker.channel,
                recipient=marker.recipient,
                body=marker.body,
                marker_index=i,
                attachments=attachments,
                attachment_errors=tuple([*marker.attachment_errors, *attachment_errors]),
            ))
        holds = parse_hold_markers(text)
        hold_reason = ""
        hold_status = ""
        hold_request = None
        if holds:
            marker = holds[-1]
            hold_reason = marker.reason or "held reply"
            hold_status = marker.status
            hold_request = HoldRequest(
                status=hold_status,
                reason=hold_reason,
                raw_text=text or "",
                summary=hold_reason[:240],
                marker_index=len(holds) - 1,
            )
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
            todo_changes.append(TodoChange(at=marker.at, kind="wake", marker_index=marker_index))
            marker_index += 1
        for marker in parse_sleep_markers(text):
            todo_changes.append(TodoChange(at=marker.at, kind="sleep", marker_index=marker_index))
            marker_index += 1
        state_markers = parse_state_markers(text)
        state_change = None
        if state_markers:
            marker = state_markers[-1]
            state_change = StateChange(state=marker.state, until=marker.until, reason=marker.reason, marker_index=len(state_markers) - 1)
        visible = strip_xml_markers(text or "", self.CONTROL_NAMES).strip()
        if hold_request is not None:
            visible = ""
            dispatches = []
            todo_changes = []
            state_change = None
            route_hint = None
        return MarkerInterpretation(
            visible_reply=visible,
            private_thoughts=tuple(()) if hold_request is not None else tuple(parse_cot_markers(text)),
            dispatch_requests=tuple(dispatches),
            todo_changes=tuple(todo_changes),
            state_change=state_change,
            route_hint=route_hint,
            hold_reason=hold_reason,
            hold_status=hold_status,
            hold_request=hold_request,
        )

    def _attachment_refs(self, tokens: tuple[str, ...]) -> tuple[tuple[AttachmentRef, ...], tuple[str, ...]]:
        refs: list[AttachmentRef] = []
        errors: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            digest = "".join(ch for ch in str(token or "").lower() if ch in "0123456789abcdef")
            if len(digest) != 64 and self.object_resolver is not None:
                resolved = self.object_resolver(f"obj:{digest}")
                if resolved:
                    digest = resolved
            if len(digest) != 64 or digest in seen:
                if len(digest) != 64:
                    errors.append(f"unresolved object token: obj:{str(token)[:80]}")
                continue
            seen.add(digest)
            refs.append(AttachmentRef(object_hash=digest))
        return tuple(refs), tuple(errors)


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
        attachment_payloads = self.attachment_payloads(request.attachments)
        meta: dict[str, Any] = {
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
        }
        if attachment_payloads:
            meta["attachments"] = attachment_payloads
            meta["attachment_hashes"] = [item["object_hash"] for item in attachment_payloads]
            meta["object_hash"] = attachment_payloads[0]["object_hash"]
        if request.attachment_errors:
            meta["attachment_errors"] = list(request.attachment_errors)
        return Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel=request.channel,
            kind="dispatch",
            content=request.body,
            meta=meta,
        )

    def attachment_events_for(
        self,
        request: DispatchRequest,
        *,
        turn_id: str = "",
        request_id: str = "",
        session_id: str = "",
    ) -> tuple[Beat, ...]:
        dispatch_id = request.dispatch_id or self.dispatch_id_for(
            turn_id=turn_id,
            marker_index=request.marker_index,
            target=request.channel,
            recipient=request.recipient,
        )
        beats: list[Beat] = []
        for index, attachment in enumerate(self.attachment_payloads(request.attachments)):
            object_hash = str(attachment.get("object_hash") or "")
            name = str(attachment.get("name") or object_hash[:12] or "attachment")
            mime = str(attachment.get("mime") or "")
            size = int(attachment.get("size") or 0)
            beats.append(Beat(
                t=datetime.now(timezone.utc),
                actor="ai",
                channel=request.channel,
                kind="attachment",
                content=f"attachment: {name}",
                meta={
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "session_id": session_id,
                    "name": "attachment",
                    "object_hash": object_hash,
                    "object_mime": mime,
                    "object_name": name,
                    "object_size": size,
                    "dispatch_id": dispatch_id,
                    "dispatch_target": request.channel,
                    "dispatch_recipient": request.recipient,
                    "attachment_index": index,
                },
            ))
        return tuple(beats)

    @staticmethod
    def dispatch_id_for(*, turn_id: str, marker_index: int, target: str, recipient: str) -> str:
        seed = f"{turn_id}:{marker_index}:{target}:{recipient}"
        return "disp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def attachment_payloads(attachments: tuple[AttachmentRef, ...]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        seen: set[str] = set()
        for attachment in attachments:
            object_hash = "".join(ch for ch in str(attachment.object_hash or "").lower() if ch in "0123456789abcdef")
            if len(object_hash) != 64 or object_hash in seen:
                continue
            seen.add(object_hash)
            payloads.append(AttachmentRef(
                object_hash=object_hash,
                name=attachment.name,
                mime=attachment.mime,
                size=attachment.size,
            ).to_payload())
        return payloads

    def publish(
        self,
        request: DispatchRequest,
        *,
        turn_id: str = "",
        request_id: str = "",
        session_id: str = "",
    ) -> bool:
        if self.bus is None:
            return False
        publish = getattr(self.bus, "publish_dispatch", None)
        if publish is None:
            return False
        dispatch_id = request.dispatch_id or self.dispatch_id_for(
            turn_id=turn_id,
            marker_index=request.marker_index,
            target=request.channel,
            recipient=request.recipient,
        )
        payload = {
            "recipient": request.recipient,
            "text": request.body,
            "dispatch_id": dispatch_id,
            "turn_id": turn_id,
            "request_id": request_id,
            "session_id": session_id,
        }
        attachments = self.attachment_payloads(request.attachments)
        if attachments:
            payload["attachments"] = attachments
        return bool(publish(request.channel, payload))


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

    def enqueue(self, request: TurnRequest) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        queue_id = f"iq_{uuid.uuid4().hex}"
        payload = {
            "queue_id": queue_id,
            "turn_id": request.turn_id,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "channel": normalize_channel(request.channel),
            "surface": (request.surface or "").strip().lower(),
            "actor": request.actor,
            "text": request.text,
            "attachments": [
                {
                    "object_hash": item.object_hash,
                    "name": item.name,
                    "mime": item.mime,
                    "size": item.size,
                }
                for item in request.attachments
            ],
            "structured_payload": request.structured_payload,
            "source_meta": request.source_meta,
            "delivery_policy": request.delivery_policy,
            "trace": request.trace,
            "received_at": request.received_at.isoformat(),
            "attempts": 0,
            "available_at": "",
            "lease_until": "",
            "leased_by": "",
            "last_error": "",
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return queue_id

    def claim(self, *, limit: int = 1, worker_id: str = "", lease_seconds: int = 300) -> list[TurnRequest]:
        """Claim visible queue items without deleting them."""
        if not self.path.exists():
            return []
        payloads = self._read_payloads()
        now = datetime.now(timezone.utc)
        selected_ids: set[str] = set()
        requests: list[TurnRequest] = []
        lease_until = now.timestamp() + max(0, int(lease_seconds))
        for payload in payloads:
            if len(requests) >= max(0, int(limit)):
                break
            queue_id = str(payload.get("queue_id") or "")
            if not queue_id:
                continue
            if str(payload.get("dead_lettered_at") or ""):
                continue
            available_at = self._parse_time(payload.get("available_at"))
            if available_at and available_at > now:
                continue
            existing_lease = self._parse_time(payload.get("lease_until"))
            if existing_lease and existing_lease > now:
                continue
            selected_ids.add(queue_id)
            payload["leased_by"] = worker_id or "worker"
            payload["lease_until"] = datetime.fromtimestamp(lease_until, timezone.utc).isoformat()
            requests.append(self._request_from_payload(payload))
        if selected_ids:
            self._write_payloads(payloads)
        return requests

    def ack(self, queue_id: str) -> bool:
        """Delete a committed queue item."""
        queue_id = str(queue_id or "")
        payloads = self._read_payloads()
        kept = [payload for payload in payloads if str(payload.get("queue_id") or "") != queue_id]
        if len(kept) == len(payloads):
            return False
        self._write_payloads(kept)
        return True

    def fail(self, queue_id: str, *, error: str = "", max_attempts: int = 3, backoff_seconds: int = 60) -> bool:
        """Release a claimed item for retry, or dead-letter it after max attempts."""
        queue_id = str(queue_id or "")
        payloads = self._read_payloads()
        now = datetime.now(timezone.utc)
        changed = False
        for payload in payloads:
            if str(payload.get("queue_id") or "") != queue_id:
                continue
            attempts = int(payload.get("attempts") or 0) + 1
            payload["attempts"] = attempts
            payload["last_error"] = str(error or "")[:1000]
            payload["leased_by"] = ""
            payload["lease_until"] = ""
            if attempts >= max(1, int(max_attempts)):
                payload["dead_lettered_at"] = now.isoformat()
            else:
                delay = max(0, int(backoff_seconds)) * attempts
                payload["available_at"] = datetime.fromtimestamp(now.timestamp() + delay, timezone.utc).isoformat()
            changed = True
            break
        if changed:
            self._write_payloads(payloads)
        return changed

    def _read_payloads(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        payloads: list[dict[str, Any]] = []
        for line in rows:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def _write_payloads(self, payloads: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(payload, ensure_ascii=False, sort_keys=True) for payload in payloads)
        self.path.write_text((text + "\n") if text else "", encoding="utf-8")

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        raw = str(value or "")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _request_from_payload(self, payload: dict[str, Any]) -> TurnRequest:
        received_at = datetime.now(timezone.utc)
        raw_received = str(payload.get("received_at") or "")
        if raw_received:
            try:
                received_at = datetime.fromisoformat(raw_received.replace("Z", "+00:00"))
            except ValueError:
                pass
        attachments_raw = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        attachments: list[AttachmentRef] = []
        for item in attachments_raw:
            if isinstance(item, dict):
                attachments.append(AttachmentRef(
                    object_hash=str(item.get("object_hash") or ""),
                    name=str(item.get("name") or ""),
                    mime=str(item.get("mime") or ""),
                    size=int(item.get("size") or 0),
                ))
        source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
        queue_id = str(payload.get("queue_id") or "")
        if queue_id:
            source_meta = {**source_meta, "queue_id": queue_id, "queue_attempts": int(payload.get("attempts") or 0)}
        return TurnRequest(
            channel=str(payload.get("channel") or "chat"),
            actor=str(payload.get("actor") or "user"),
            text=str(payload.get("text") or ""),
            surface=str(payload.get("surface") or ""),
            turn_id=str(payload.get("turn_id") or f"turn_{uuid.uuid4().hex}"),
            request_id=str(payload.get("request_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            attachments=tuple(attachments),
            structured_payload=payload.get("structured_payload") if isinstance(payload.get("structured_payload"), dict) else {},
            source_meta=source_meta,
            delivery_policy=payload.get("delivery_policy") if payload.get("delivery_policy") in {"record_only", "lazy", "instant", "batch", "state_only"} else "instant",
            trace=payload.get("trace") if isinstance(payload.get("trace"), dict) else {},
            received_at=received_at,
        )


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


@dataclass(frozen=True, slots=True)
class TurnTraceRow:
    """One structured turn observability phase row."""

    turn_id: str
    phase: str
    status: TraceStatus = "ok"
    request_id: str = ""
    session_id: str = ""
    channel: str = ""
    surface: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0
    runtime: str = ""
    model: str = ""
    attempt: int = 0
    error: str = ""
    refs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "surface": self.surface,
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": int(self.duration_ms or 0),
            "runtime": self.runtime,
            "model": self.model,
            "attempt": int(self.attempt or 0),
            "error": str(self.error or "")[:1000],
            "refs": self._safe_refs(self.refs),
        }
        return row

    @staticmethod
    def _safe_refs(refs: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in (refs or {}).items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                safe[str(key)] = value
            elif isinstance(value, (list, tuple)):
                safe[str(key)] = [str(item) for item in value[:50]]
            else:
                safe[str(key)] = str(value)[:500]
        return safe


class TurnTraceStore:
    """Durable turn-phase trace rows for observability."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, row: TurnTraceRow) -> None:
        if not row.turn_id or not row.phase:
            return
        self.append_many((row,))

    def append_many(self, rows: tuple[TurnTraceRow, ...] | list[TurnTraceRow]) -> None:
        rows = tuple(row for row in rows if row.turn_id and row.phase)
        if not rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


class MemoryTimelineStore:
    """Derived markdown read model for explicit memory timeline."""

    def __init__(self, timeline_dir: Path) -> None:
        self.timeline_dir = timeline_dir

    def append_beat(self, beat: Beat) -> Path | None:
        if beat.kind in {"think", "trace"}:
            return None
        meta = beat.meta if isinstance(beat.meta, dict) else {}
        event_id = str(meta.get("event_id") or "").strip()
        if not event_id:
            return None
        self.timeline_dir.mkdir(parents=True, exist_ok=True)
        day = beat.t.date().isoformat()
        path = self.timeline_dir / f"{day}.md"
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if f"event:{event_id}" in existing:
            return path
        if not existing.strip():
            path.write_text(f"# {day}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(self._entry_for(beat, event_id))
        self._ensure_index(day)
        self._compact_day(day)
        return path

    def _ensure_index(self, day: str) -> None:
        index = self.timeline_dir / "index.md"
        line = f"- [{day}]({day}.md)"
        existing = index.read_text(encoding="utf-8", errors="replace") if index.exists() else "# Timeline\n\n"
        if line not in existing:
            index.write_text(existing.rstrip() + "\n" + line + "\n", encoding="utf-8")

    def _compact_day(self, day: str) -> None:
        daily = self.timeline_dir / f"{day}.md"
        if not daily.exists() or len(day) < 7:
            return
        text = daily.read_text(encoding="utf-8", errors="replace")
        entries = self._entry_summaries(text)
        if not entries:
            return
        summary = "; ".join(entries[:5])
        if len(entries) > 5:
            summary += f"; +{len(entries) - 5} more"
        refs = self._refs_from_text(text)
        month = day[:7]
        year = day[:4]
        self._upsert_section(
            self.timeline_dir / f"{month}.md",
            title=f"# {month}",
            heading=f"## {day}",
            lines=[
                f"- entries: {len(entries)}",
                f"- summary: {summary}",
                f"- refs: {' '.join(refs[:40])}",
            ],
        )
        self._upsert_section(
            self.timeline_dir / f"{year}.md",
            title=f"# {year}",
            heading=f"## {month}",
            lines=[
                f"- latest: {day}",
                f"- summary: {summary}",
                f"- refs: {' '.join(refs[:40])}",
            ],
        )

    def query(self, query: str = "", *, limit: int = 20) -> list[dict[str, Any]]:
        needle = str(query or "").strip().lower()
        limit = max(1, min(100, int(limit or 20)))
        results: list[dict[str, Any]] = []
        if not self.timeline_dir.exists():
            return []
        files = [path for path in self.timeline_dir.glob("*.md") if path.name not in {"context.md", "index.md"}]
        daily = sorted((path for path in files if len(path.stem) == 10), reverse=True)
        monthly = sorted((path for path in files if len(path.stem) == 7), reverse=True)
        yearly = sorted((path for path in files if len(path.stem) == 4), reverse=True)
        others = sorted((path for path in files if len(path.stem) not in {4, 7, 10}), reverse=True)
        for path in [*daily, *monthly, *yearly, *others]:
            text = path.read_text(encoding="utf-8", errors="replace")
            for heading, body in self._sections(text):
                searchable = f"{heading}\n{body}".lower()
                if needle and needle not in searchable:
                    continue
                results.append({
                    "path": path.name,
                    "heading": heading.lstrip("# ").strip(),
                    "text": body.strip()[:1200],
                    "refs": self._refs_from_text(body),
                })
                if len(results) >= limit:
                    return results
        return results

    @staticmethod
    def _entry_summaries(text: str) -> list[str]:
        summaries: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or stripped.startswith("- refs:"):
                continue
            summaries.append(stripped[2:242])
        return summaries

    @staticmethod
    def _refs_from_text(text: str) -> list[str]:
        refs: list[str] = []
        for raw in str(text or "").replace(",", " ").split():
            token = raw.strip().strip("[](){}.,;")
            if token.startswith(("event:", "turn:", "object:", "dispatch:")) and token not in refs:
                refs.append(token)
        return refs[:100]

    @staticmethod
    def _sections(text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, list[str]]] = []
        current: tuple[str, list[str]] | None = None
        for line in text.splitlines():
            if line.startswith("### ") or line.startswith("## "):
                if current is not None:
                    sections.append(current)
                current = (line.strip(), [])
            elif current is not None:
                current[1].append(line)
        if current is not None:
            sections.append(current)
        return [(heading, "\n".join(body)) for heading, body in sections]

    @staticmethod
    def _upsert_section(path: Path, *, title: str, heading: str, lines: list[str]) -> None:
        section = heading + "\n" + "\n".join(lines).rstrip() + "\n"
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else title.rstrip() + "\n\n"
        raw_lines = existing.splitlines()
        try:
            start = raw_lines.index(heading)
        except ValueError:
            path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
            return
        end = len(raw_lines)
        for index in range(start + 1, len(raw_lines)):
            if raw_lines[index].startswith("## "):
                end = index
                break
        next_lines = raw_lines[:start] + section.rstrip().splitlines() + raw_lines[end:]
        path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")

    def _entry_for(self, beat: Beat, event_id: str) -> str:
        meta = beat.meta if isinstance(beat.meta, dict) else {}
        turn_id = str(meta.get("turn_id") or "").strip()
        object_hash = str(meta.get("object_hash") or "").strip()
        dispatch_id = str(meta.get("dispatch_id") or "").strip()
        surface = str(beat.surface or meta.get("surface") or "").strip()
        scene = beat.channel + (f"/{surface}" if surface else "")
        summary = self._summary(beat.content)
        refs = [f"event:{event_id}"]
        if turn_id:
            refs.append(f"turn:{turn_id}")
        if object_hash:
            refs.append(f"object:{object_hash}")
        if dispatch_id:
            refs.append(f"dispatch:{dispatch_id}")
        return (
            f"### {beat.t.strftime('%H:%M')} {turn_id or event_id}\n"
            f"- {beat.actor}@{scene} {beat.kind}: {summary}\n"
            f"- refs: {' '.join(refs)}\n\n"
        )

    @staticmethod
    def _summary(content: str) -> str:
        text = " ".join(str(content or "").split())
        if len(text) > 240:
            return text[:237].rstrip() + "..."
        return text or "(empty)"


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

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.model and self.base_url and self.api_key())


class SummaryRuntime:
    """Small OpenAI-compatible summary client for MemoryWorker jobs."""

    def __init__(self, config: SummaryRuntimeConfig) -> None:
        self.config = config

    def summarize(self, text: str, *, purpose: str = "event") -> dict[str, Any]:
        clean = " ".join(str(text or "").split())
        if not clean:
            return {"summary": "", "tags": []}
        if not self.config.configured:
            return {"summary": _deterministic_summary(clean), "tags": _deterministic_tags(clean)}
        body = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize FIAM memory material for later recall. Return only JSON "
                        "with keys summary and tags. summary must be concise and factual; "
                        "tags must be short lowercase strings."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"purpose": purpose, "text": clean[:12000]}, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        }
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key()}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        try:
            parsed = _parse_summary_payload(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}
        if not parsed.get("summary"):
            parsed["summary"] = _deterministic_summary(clean)
        parsed["tags"] = [str(tag).strip().lower()[:40] for tag in (parsed.get("tags") or []) if str(tag).strip()][:12]
        return parsed


class MemoryWorker:
    """Idempotent worker boundary for async memory processing."""

    def __init__(self, event_store: object | None = None, *, embedder: object | None = None, feature_store: object | None = None, pool: object | None = None, config: object | None = None, model_id: str = "", summary_config: SummaryRuntimeConfig | None = None, summary_runtime: object | None = None, timeline_store: MemoryTimelineStore | None = None, trace_store: TurnTraceStore | None = None, worker_id: str = "memory-worker", lease_seconds: int = 300, max_attempts: int = 3, backoff_seconds: int = 60) -> None:
        self.event_store = event_store
        self.embedder = embedder
        self.feature_store = feature_store
        self.pool = pool
        self.config = config
        self.model_id = model_id
        self.summary_config = summary_config or SummaryRuntimeConfig.from_env()
        self.summary_runtime = summary_runtime or SummaryRuntime(self.summary_config)
        self.timeline_store = timeline_store or self._default_timeline_store(event_store)
        self.trace_store = trace_store or self._default_trace_store(event_store)
        self.worker_id = worker_id or "memory-worker"
        self.lease_seconds = int(lease_seconds)
        self.max_attempts = int(max_attempts)
        self.backoff_seconds = int(backoff_seconds)

    def pending_query(self) -> str:
        return "SELECT job_id, event_id, kind FROM memory_jobs WHERE status = 'pending' ORDER BY created_at ASC"

    def process_once(self, *, limit: int = 100) -> int:
        if self.event_store is None:
            return 0
        enqueue_jobs = getattr(self.event_store, "enqueue_unembedded_memory_jobs", None)
        claim_jobs = getattr(self.event_store, "claim_memory_jobs", None)
        ack_job = getattr(self.event_store, "ack_memory_job", None)
        fail_job = getattr(self.event_store, "fail_memory_job", None)
        read_event = getattr(self.event_store, "read_event", None)
        if all(callable(item) for item in (enqueue_jobs, claim_jobs, ack_job, fail_job, read_event)):
            if self.embedder is not None:
                enqueue_jobs(limit=limit)
            processed = 0
            for job in claim_jobs(limit=limit, worker_id=self.worker_id, lease_seconds=self.lease_seconds):
                job_id = str(job.get("job_id") or "")
                event_id = str(job.get("event_id") or "")
                job_kind = str(job.get("kind") or "event")
                if job_kind == "pool_graph":
                    started_at = datetime.now(timezone.utc)
                    try:
                        graph_refs = self._process_pool_graph(event_id)
                    except Exception as exc:
                        fail_job(job_id, error=str(exc), max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                        self._trace_memory_phase("memory.failed", None, event_id=event_id, job=job, status="error", error=str(exc), started_at=started_at)
                        continue
                    ack_job(job_id)
                    self._trace_memory_phase("memory.done", None, event_id=event_id, job=job, refs=graph_refs, started_at=started_at)
                    processed += 1
                    continue
                if job_kind == "summary":
                    started_at = datetime.now(timezone.utc)
                    try:
                        refs = self._process_summary(event_id)
                    except Exception as exc:
                        fail_job(job_id, error=str(exc), max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                        self._trace_memory_phase("memory.failed", None, event_id=event_id, job=job, status="error", error=str(exc), started_at=started_at)
                        continue
                    ack_job(job_id)
                    self._trace_memory_phase("memory.done", None, event_id=event_id, job=job, refs=refs, started_at=started_at)
                    processed += 1
                    continue
                if job_kind == "transcript_compaction":
                    started_at = datetime.now(timezone.utc)
                    try:
                        refs = self._process_transcript_compaction(event_id)
                    except Exception as exc:
                        fail_job(job_id, error=str(exc), max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                        self._trace_memory_phase("memory.failed", None, event_id=event_id, job=job, status="error", error=str(exc), started_at=started_at)
                        continue
                    ack_job(job_id)
                    self._trace_memory_phase("memory.done", None, event_id=event_id, job=job, refs=refs, started_at=started_at)
                    processed += 1
                    continue
                if job_kind == "recall_warmup":
                    started_at = datetime.now(timezone.utc)
                    try:
                        refs = self._process_recall_warmup(event_id)
                    except Exception as exc:
                        fail_job(job_id, error=str(exc), max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                        self._trace_memory_phase("memory.failed", None, event_id=event_id, job=job, status="error", error=str(exc), started_at=started_at)
                        continue
                    ack_job(job_id)
                    self._trace_memory_phase("memory.done", None, event_id=event_id, job=job, refs=refs, started_at=started_at)
                    processed += 1
                    continue
                if job_kind != "event":
                    self._trace_memory_phase("memory.skipped", None, event_id=event_id, job=job, status="skipped", error=f"unsupported job kind: {job_kind}")
                    ack_job(job_id)
                    continue
                if self.embedder is None:
                    fail_job(job_id, error="embedder unavailable", max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                    self._trace_memory_phase("memory.failed", None, event_id=event_id, job=job, status="error", error="embedder unavailable")
                    continue
                beat = read_event(event_id)
                if beat is None:
                    self._trace_memory_phase("memory.skipped", None, event_id=event_id, job=job, status="skipped", error="event missing")
                    ack_job(job_id)
                    continue
                started_at = datetime.now(timezone.utc)
                try:
                    self._process_beat(beat, event_id)
                except Exception as exc:
                    fail_job(job_id, error=str(exc), max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
                    self._trace_memory_phase("memory.failed", beat, event_id=event_id, job=job, status="error", error=str(exc), started_at=started_at)
                    continue
                ack_job(job_id)
                enqueue_one = getattr(self.event_store, "enqueue_memory_job", None)
                if callable(enqueue_one):
                    enqueue_one(event_id, kind="summary")
                    if self.pool is not None:
                        enqueue_one(event_id, kind="recall_warmup")
                    if self.config is not None:
                        enqueue_one(f"transcript:{beat.channel}", kind="transcript_compaction")
                self._trace_memory_phase("memory.done", beat, event_id=event_id, job=job, refs={"timeline": bool(self.timeline_store), "feature_store": self.feature_store is not None}, started_at=started_at)
                processed += 1
            return processed

        return 0

    def _trace_memory_phase(self, phase: str, beat: Beat | None, *, event_id: str = "", job: dict[str, Any] | None = None, status: str = "ok", error: str = "", refs: dict[str, Any] | None = None, started_at: datetime | None = None) -> None:
        if self.trace_store is None:
            return
        meta = beat.meta if beat is not None and isinstance(beat.meta, dict) else {}
        ended_at = datetime.now(timezone.utc)
        started = started_at or ended_at
        row_refs = {
            "event_id": event_id,
            "job_id": str((job or {}).get("job_id") or ""),
            "job_kind": str((job or {}).get("kind") or "event"),
            "attempts": int((job or {}).get("attempts") or 0),
        }
        row_refs.update(refs or {})
        try:
            self.trace_store.append(TurnTraceRow(
                turn_id=str(meta.get("turn_id") or event_id or phase),
                request_id=str(meta.get("request_id") or ""),
                session_id=str(meta.get("session_id") or ""),
                channel=beat.channel if beat is not None else "memory",
                surface=beat.surface if beat is not None else "",
                phase=phase,
                status=status if status in {"ok", "error", "skipped"} else "error",
                started_at=started.isoformat(),
                ended_at=ended_at.isoformat(),
                duration_ms=max(0, int((ended_at - started).total_seconds() * 1000)),
                runtime="memory_worker",
                model=self.model_id,
                error=str(error or "")[:1000],
                refs=row_refs,
            ))
        except Exception:
            return

    def _process_beat(self, beat: Beat, event_id: str) -> None:
        mark_embedded = getattr(self.event_store, "mark_embedded", None)
        if mark_embedded is None:
            return
        vec = self.embedder.embed(beat.content)
        if self.feature_store is not None:
            self.feature_store.append_beat_vector(beat, vec, model_id=self.model_id)
        if self.timeline_store is not None:
            self.timeline_store.append_beat(beat)
        mark_embedded(event_id, model_id=self.model_id, embedded_at=datetime.now(timezone.utc))

    def _process_pool_graph(self, event_id: str) -> dict[str, Any]:
        if self.pool is None or self.config is None:
            return {"pool_graph": "skipped", "reason": "missing_pool_or_config"}
        get_event = getattr(self.pool, "get_event", None)
        if callable(get_event) and get_event(event_id) is None:
            return {"pool_graph": "skipped", "reason": "pool_event_missing"}
        from fiam.retriever.graph_builder import build_edges

        summary = build_edges(self.pool, [event_id], self.config, skip_ds=True)
        return {"pool_graph": "done", "graph": summary}

    def _process_summary(self, event_id: str) -> dict[str, Any]:
        read_event = getattr(self.event_store, "read_event", None)
        update_meta = getattr(self.event_store, "update_event_meta", None)
        if not callable(read_event) or not callable(update_meta):
            return {"summary": "skipped", "reason": "event_store_missing_summary_api"}
        beat = read_event(event_id)
        if beat is None:
            return {"summary": "skipped", "reason": "event_missing"}
        meta = beat.meta if isinstance(beat.meta, dict) else {}
        existing = str(meta.get("summary_ref") or "")
        if existing:
            return {"summary": "skipped", "reason": "already_summarized", "summary_ref": existing}
        result = self.summary_runtime.summarize(_summary_source_text(beat), purpose=str(beat.kind or "event"))
        summary = str(result.get("summary") or "").strip()
        tags = [str(tag).strip().lower() for tag in (result.get("tags") or []) if str(tag).strip()][:12]
        if not summary:
            return {"summary": "skipped", "reason": "empty_summary"}
        object_store = getattr(self.event_store, "object_store", None)
        summary_ref = object_store.put_text(summary, suffix=".txt") if object_store is not None else ""
        updates: dict[str, Any] = {
            "summary": summary,
            "object_summary": summary if meta.get("object_hash") else "",
            "summary_ref": summary_ref,
            "summary_provider": self.summary_config.provider or "deterministic",
            "summary_model": self.summary_config.model or "",
        }
        if tags:
            updates["object_tags"] = tags
            updates["tags"] = tags
        update_meta(event_id, updates)
        return {"summary": "done", "summary_ref": summary_ref, "tag_count": len(tags)}

    def _process_transcript_compaction(self, target: str) -> dict[str, Any]:
        if self.config is None:
            return {"transcript_compaction": "skipped", "reason": "missing_config"}
        channel = str(target or "").split(":", 1)[1] if str(target or "").startswith("transcript:") else str(target or "chat")
        from fiam.runtime.prompt import transcript_path

        path = transcript_path(self.config, channel)
        if not path.exists():
            return {"transcript_compaction": "skipped", "reason": "missing_transcript", "channel": channel}
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        max_messages = 120
        if len(lines) <= max_messages:
            return {"transcript_compaction": "skipped", "reason": "below_threshold", "channel": channel, "message_count": len(lines)}
        old_lines = lines[: len(lines) - max_messages]
        old_text = "\n".join(old_lines)
        result = self.summary_runtime.summarize(old_text, purpose=f"transcript:{channel}")
        summary = str(result.get("summary") or "").strip() or _deterministic_summary(old_text)
        object_store = getattr(self.event_store, "object_store", None)
        summary_ref = object_store.put_text(summary, suffix=".txt") if object_store is not None else ""
        compact_message = {
            "role": "system",
            "content": f"[transcript_compaction]\nsummary_ref={summary_ref}\n{summary}",
        }
        kept = [json.dumps(compact_message, ensure_ascii=False), *lines[-max_messages:]]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return {"transcript_compaction": "done", "channel": channel, "compacted": len(old_lines), "summary_ref": summary_ref}

    def _process_recall_warmup(self, event_id: str) -> dict[str, Any]:
        if self.pool is None or self.config is None:
            return {"recall_warmup": "skipped", "reason": "missing_pool_or_config"}
        event = getattr(self.pool, "get_event", lambda _event_id: None)(event_id)
        if event is None:
            return {"recall_warmup": "skipped", "reason": "pool_event_missing"}
        fingerprints = self.pool.load_fingerprints()
        idx = int(getattr(event, "fingerprint_idx", -1))
        if idx < 0 or idx >= len(fingerprints):
            return {"recall_warmup": "skipped", "reason": "missing_fingerprint"}
        from fiam.runtime.recall import build_recall_context

        context = build_recall_context(
            self.config,
            self.pool,
            fingerprints[idx],
            shield_recent=False,
            shield_after=None,
        )
        path = self.config.store_dir / "recall_warmup.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "seed_event_id": event_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fragment_count": context.count,
            "fragments": [
                {
                    "event_id": fragment.event_id,
                    "activation": fragment.activation,
                    "summary": fragment.summary[:240],
                }
                for fragment in context.fragments
                if fragment.event_id != event_id
            ],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return {"recall_warmup": "done", "fragment_count": context.count, "path": "store/recall_warmup.jsonl"}

    @staticmethod
    def _default_timeline_store(event_store: object | None) -> MemoryTimelineStore | None:
        db_path = getattr(event_store, "db_path", None)
        if isinstance(db_path, Path):
            return MemoryTimelineStore(db_path.parent / "timeline")
        return None

    @staticmethod
    def _default_trace_store(event_store: object | None) -> TurnTraceStore | None:
        db_path = getattr(event_store, "db_path", None)
        if isinstance(db_path, Path):
            return TurnTraceStore(db_path.parent / "turn_traces.jsonl")
        return None


def _summary_source_text(beat: Beat) -> str:
    meta = beat.meta if isinstance(beat.meta, dict) else {}
    parts = [
        f"actor={beat.actor}",
        f"channel={beat.channel}",
        f"surface={beat.surface}",
        f"kind={beat.kind}",
    ]
    if meta.get("object_name"):
        parts.append(f"object_name={meta.get('object_name')}")
    if meta.get("object_mime"):
        parts.append(f"object_mime={meta.get('object_mime')}")
    parts.append(str(beat.content or ""))
    return "\n".join(parts)


def _deterministic_summary(text: str, *, limit: int = 320) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _deterministic_tags(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    tags: list[str] = []
    for word in words:
        if word in {"actor", "channel", "surface", "kind", "message", "attachment"}:
            continue
        if word not in tags:
            tags.append(word[:40])
        if len(tags) >= 8:
            break
    return tags


def _parse_summary_payload(content: str) -> dict[str, Any]:
    clean = str(content or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    if not clean.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            clean = match.group(0)
    data = json.loads(clean)
    return data if isinstance(data, dict) else {}
