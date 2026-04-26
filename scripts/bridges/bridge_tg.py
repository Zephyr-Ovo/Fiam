"""bridge_tg — Telegram ↔ MQTT bridge (independent process).

Inbound:  poll TG getUpdates every ``tg_poll_interval`` seconds → publish
          each message to ``fiam/receive/tg``.
Outbound: subscribe ``fiam/dispatch/tg`` → call postman send helpers.

This process is the ONLY one that talks to api.telegram.org. The daemon
no longer knows about TG at all; it only sees MQTT topics.

Run as a systemd unit. Crash-safe — restart fully reinitialises state
from the broker's persistent session (TG offset is kept in-memory and
re-derived from the next getUpdates call).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Allow running as ``python scripts/bridges/bridge_tg.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fiam.bus import Bus, DISPATCH_PREFIX  # noqa: E402
from fiam.plugins import is_dispatch_enabled, is_receive_enabled  # noqa: E402
from fiam_lib.core import _build_config, _project_root, _toml_path  # noqa: E402
from fiam_lib.postman import (  # noqa: E402
    fetch_tg_inbox,
    _tg_send_segmented,
    _tg_send_sticker,
    _extract_stickers,
)

logger = logging.getLogger("fiam.bridge.tg")
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
    """Handle outbound TG message published by daemon."""
    text = (payload.get("text") or "").strip()
    if not text:
        return
    config = _on_dispatch.config  # type: ignore[attr-defined]
    if not is_dispatch_enabled(config, "tg"):
        logger.info("TG dispatch skipped — plugin disabled")
        return
    token = os.environ.get(config.tg_bot_token_env, "")
    chat_id = config.tg_chat_id
    if not token or not chat_id:
        logger.warning("TG not configured — skipping dispatch")
        return
    body, stickers = _extract_stickers(text, config)
    if body:
        _tg_send_segmented(token, chat_id, body)
    for stk in stickers:
        if stk["type"] == "file_id":
            _tg_send_sticker(token, chat_id, stk["value"])
    logger.info("dispatched TG message (%d chars, %d stickers)", len(body), len(stickers))


def main() -> None:
    parser = argparse.ArgumentParser(description="TG ↔ MQTT bridge")
    parser.add_argument("--toml", type=Path, default=None, help="path to fiam.toml")
    args = parser.parse_args()

    code_path = _project_root()
    _load_env(code_path)

    # Build config (reuse daemon's loader)
    fake = argparse.Namespace(toml=args.toml)
    config = _build_config(fake)
    _on_dispatch.config = config  # type: ignore[attr-defined]

    if not is_receive_enabled(config, "tg") and not is_dispatch_enabled(config, "tg"):
        logger.info("bridge_tg disabled by plugin manifest; exiting")
        return

    bus = Bus(client_id="fiam-bridge-tg")
    bus.subscribe(f"{DISPATCH_PREFIX}/tg", _on_dispatch)
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

    interval = max(10, int(config.tg_poll_interval))
    logger.info("bridge_tg up — poll every %ds, broker %s:%d",
                interval, config.mqtt_host, config.mqtt_port)

    next_poll = 0.0
    while running:
        now = time.time()
        if now < next_poll:
            time.sleep(min(1.0, next_poll - now))
            continue
        next_poll = now + interval
        try:
            msgs = fetch_tg_inbox(config)
        except Exception:
            logger.exception("fetch_tg_inbox failed")
            continue
        for m in msgs:
            if not is_receive_enabled(config, "tg"):
                logger.info("TG receive skipped — plugin disabled")
                break
            bus.publish_receive("tg", {
                "text": m.get("text", ""),
                "from_name": m.get("from_name", ""),
                "source": "tg",
                "t": m.get("t"),
            })
        if msgs:
            logger.info("published %d TG message(s)", len(msgs))

    bus.loop_stop()
    logger.info("bridge_tg stopped")


if __name__ == "__main__":
    main()
