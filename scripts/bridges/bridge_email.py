"""bridge_email — Email (IMAP+SMTP) ↔ MQTT bridge (independent process).

Inbound:  poll IMAP every ``email_poll_interval`` seconds → publish each
          message to ``fiam/receive/email``.
Outbound: subscribe ``fiam/dispatch/email`` → call postman SMTP send.

The daemon never touches IMAP/SMTP directly.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# src/ must stay before scripts/ because scripts/fiam.py shadows the fiam package.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from fiam.bus import Bus, DISPATCH_PREFIX  # noqa: E402
from fiam.plugins import is_dispatch_enabled, is_receive_enabled  # noqa: E402
from fiam.store.beat import append_beat  # noqa: E402
from fiam.store.objects import ObjectStore  # noqa: E402
from fiam.turn import AttachmentRef, DispatchRequest, DispatchService, TurnTraceRow, TurnTraceStore  # noqa: E402
from fiam_lib.core import _build_config, _project_root  # noqa: E402
from fiam_lib.postman import fetch_inbox, _email_send, _resolve_contact  # noqa: E402

logger = logging.getLogger("fiam.bridge.email")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


def _load_env(code_path: Path) -> None:
    env_file = code_path / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("\"'")
        if k and k not in os.environ:
            os.environ[k] = v


def _on_dispatch(_leaf: str, payload: dict) -> None:
    """Handle outbound email message published by daemon."""
    text = (payload.get("text") or "").strip()
    recipient = (payload.get("recipient") or "").strip()
    config = _on_dispatch.config  # type: ignore[attr-defined]
    try:
        attachments = _dispatch_attachments(payload, config)
    except ValueError as exc:
        logger.warning("email dispatch attachment failure: %s", exc)
        _record_dispatch_status(config, payload, status="failed", last_error=str(exc))
        return
    if not text and not attachments:
        _record_dispatch_status(config, payload, status="failed", last_error="empty dispatch payload")
        return
    if not is_dispatch_enabled(config, "email"):
        logger.info("email dispatch skipped — plugin disabled")
        _record_dispatch_status(config, payload, status="failed", last_error="email plugin disabled")
        return
    from_addr = config.email_from
    to_addr = (
        recipient if "@" in recipient
        else _resolve_contact(recipient, config) or config.email_to
    )
    password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
    if not from_addr or not to_addr or not config.email_smtp_host:
        logger.warning("email not configured — skipping dispatch")
        _record_dispatch_status(config, payload, status="failed", last_error="email not configured")
        return
    body = text or "(see attached file)"
    subject = body.split("\n", 1)[0][:80] or "From ai"
    ok = _email_send(
        config.email_smtp_host, config.email_smtp_port,
        from_addr, to_addr, subject, body,
        password=password,
        attachments=attachments,
    )
    _record_dispatch_status(config, payload, status="delivered" if ok else "failed", last_error="" if ok else "SMTP send failed")
    logger.info("dispatched email to %s (ok=%s)", to_addr, ok)


def _dispatch_attachments(payload: dict, config) -> list[dict]:
    raw_attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    if not raw_attachments:
        return []
    object_store = ObjectStore(config.object_dir)
    out: list[dict] = []
    seen: set[str] = set()
    for att in raw_attachments:
        if not isinstance(att, dict):
            raise ValueError("attachment payload must be an object")
        object_hash = "".join(ch for ch in str(att.get("object_hash") or "").lower() if ch in "0123456789abcdef")
        if len(object_hash) != 64:
            raise ValueError("attachment missing object_hash")
        if object_hash in seen:
            continue
        seen.add(object_hash)
        try:
            path = object_store.path_for_hash(object_hash, suffix="")
        except ValueError as exc:
            raise ValueError(f"invalid attachment object_hash: {object_hash[:12]}") from exc
        if not path.is_file():
            raise ValueError(f"attachment object not found: {object_hash[:12]}")
        out.append({
            "object_hash": object_hash,
            "path": str(path),
            "name": Path(str(att.get("name") or path.name)).name,
            "mime": str(att.get("mime") or "application/octet-stream"),
            "size": int(att.get("size") or path.stat().st_size),
        })
    return out


def _record_dispatch_status(config, payload: dict, *, status: str, last_error: str = "") -> None:
    raw_attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    attachments: list[AttachmentRef] = []
    for att in raw_attachments:
        if not isinstance(att, dict):
            continue
        object_hash = "".join(ch for ch in str(att.get("object_hash") or "").lower() if ch in "0123456789abcdef")
        if len(object_hash) != 64:
            continue
        attachments.append(AttachmentRef(
            object_hash=object_hash,
            name=Path(str(att.get("name") or object_hash[:12])).name,
            mime=str(att.get("mime") or ""),
            size=int(att.get("size") or 0),
        ))
    request = DispatchRequest(
        channel="email",
        recipient=str(payload.get("recipient") or ""),
        body=str(payload.get("text") or ""),
        dispatch_id=str(payload.get("dispatch_id") or ""),
        attachments=tuple(attachments),
    )
    try:
        event = DispatchService().event_for(
            request,
            turn_id=str(payload.get("turn_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            status=status,
            attempts=1,
            last_error=last_error,
        )
        append_beat(config.flow_path, event)
        _record_dispatch_trace(config, payload, status=status, last_error=last_error, attachments=attachments)
    except Exception:
        logger.exception("email dispatch status record failed")


def _record_dispatch_trace(config, payload: dict, *, status: str, last_error: str = "", attachments: list[AttachmentRef] | None = None) -> None:
    turn_id = str(payload.get("turn_id") or payload.get("dispatch_id") or "email_dispatch")
    dispatch_id = str(payload.get("dispatch_id") or "")
    now = datetime.now(timezone.utc)
    try:
        TurnTraceStore(config.store_dir / "turn_traces.jsonl").append(TurnTraceRow(
            turn_id=turn_id,
            request_id=str(payload.get("request_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            channel="email",
            surface="email.bridge",
            phase="dispatch.delivered" if status == "delivered" else "dispatch.failed",
            status="ok" if status == "delivered" else "error",
            started_at=now.isoformat(),
            ended_at=now.isoformat(),
            error=last_error,
            refs={
                "dispatch_id": dispatch_id,
                "recipient": str(payload.get("recipient") or ""),
                "attachment_hashes": [attachment.object_hash for attachment in (attachments or [])],
            },
        ))
    except Exception:
        logger.debug("email dispatch trace append failed", exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Email ↔ MQTT bridge")
    parser.add_argument("--toml", type=Path, default=None, help="path to fiam.toml")
    args = parser.parse_args()

    code_path = _project_root()
    _load_env(code_path)

    fake = argparse.Namespace(toml=args.toml)
    config = _build_config(fake)
    _on_dispatch.config = config  # type: ignore[attr-defined]

    if not is_receive_enabled(config, "email") and not is_dispatch_enabled(config, "email"):
        logger.info("bridge_email disabled by plugin manifest; exiting")
        return

    bus = Bus(client_id="fiam-bridge-email")
    bus.subscribe(f"{DISPATCH_PREFIX}/email", _on_dispatch)
    bus.connect(config.mqtt_host, config.mqtt_port, config.mqtt_keepalive)
    bus.loop_start()

    running = True

    def _stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _stop)

    interval = max(30, int(config.email_poll_interval))
    logger.info("bridge_email up — poll every %ds, broker %s:%d",
                interval, config.mqtt_host, config.mqtt_port)

    next_poll = 0.0
    while running:
        now = time.time()
        if now < next_poll:
            time.sleep(min(1.0, next_poll - now))
            continue
        next_poll = now + interval
        try:
            msgs = fetch_inbox(config)
        except Exception:
            logger.exception("fetch_inbox failed")
            continue
        for m in msgs:
            if not is_receive_enabled(config, "email"):
                logger.info("email receive skipped — plugin disabled")
                break
            bus.publish_receive("email", {
                "text": m.get("text", ""),
                "from_name": m.get("from_name", ""),
                "source": "email",
                "t": m.get("t"),
            })
        if msgs:
            logger.info("published %d email(s)", len(msgs))

    bus.loop_stop()
    logger.info("bridge_email stopped")


if __name__ == "__main__":
    main()
