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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fiam.bus import Bus, DISPATCH_PREFIX  # noqa: E402
from fiam.plugins import is_dispatch_enabled, is_receive_enabled  # noqa: E402
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
    if not text:
        return
    config = _on_dispatch.config  # type: ignore[attr-defined]
    if not is_dispatch_enabled(config, "email"):
        logger.info("email dispatch skipped — plugin disabled")
        return
    from_addr = config.email_from
    to_addr = (
        recipient if "@" in recipient
        else _resolve_contact(recipient, config) or config.email_to
    )
    password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
    if not from_addr or not to_addr or not config.email_smtp_host:
        logger.warning("email not configured — skipping dispatch")
        return
    subject = text.split("\n", 1)[0][:80] or f"From {config.ai_name}"
    ok = _email_send(
        config.email_smtp_host, config.email_smtp_port,
        from_addr, to_addr, subject, text,
        password=password,
    )
    logger.info("dispatched email to %s (ok=%s)", to_addr, ok)


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
