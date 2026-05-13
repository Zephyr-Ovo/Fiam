"""MQTT message bus — thin wrapper around paho-mqtt.

Conductor / daemon / bridges all talk through this module.
The bus replaces per-channel polling with publish/subscribe.

Topic layout (locked 2026-04-24):
    fiam/receive/<channel[/path]> ← inbound canonical domain (chat, email, desktop/result, ...)
    fiam/dispatch/<target>    ← outbound from conductor (email, cc, dashboard, ...)

Reliability: QoS 1 + persistent sessions. The broker (Mosquitto) is
expected to be reachable on 127.0.0.1:1883 with no auth.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Callable

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "paho-mqtt is required for the message bus.\n"
        "  pip install paho-mqtt"
    ) from e

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Topic constants
# ---------------------------------------------------------------------
RECEIVE_PREFIX = "fiam/receive"
DISPATCH_PREFIX = "fiam/dispatch"
RECEIVE_ALL = "fiam/receive/#"
DISPATCH_ALL = "fiam/dispatch/+"


def receive_topic(channel: str) -> str:
    return f"{RECEIVE_PREFIX}/{channel}"


def dispatch_topic(target: str) -> str:
    return f"{DISPATCH_PREFIX}/{target}"


# ---------------------------------------------------------------------
# Bus client
# ---------------------------------------------------------------------

# Handler signature: (source_or_target: str, payload: dict) -> None
Handler = Callable[[str, dict], None]


class Bus:
    """Persistent MQTT client with topic-prefix routing.

    Usage:
        bus = Bus(client_id="fiam-daemon")
        bus.subscribe("fiam/receive/#", on_inbound)
        bus.connect("127.0.0.1", 1883)
        bus.loop_start()
        bus.publish_receive("chat", {"text": "...", "surface": "favilla", "from_name": "..."})
        ...
        bus.loop_stop()
    """

    def __init__(self, client_id: str, *, qos: int = 1, manual_ack: bool = True) -> None:
        self.client_id = client_id
        self.qos = qos
        self.manual_ack = manual_ack
        # clean_session=False enables persistent sessions; broker queues
        # QoS 1 messages while we're disconnected.
        self._client = mqtt.Client(
            client_id=client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=manual_ack,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # topic-pattern → handler map
        self._handlers: dict[str, Handler] = {}
        self._lock = threading.Lock()

        # Auto-resubscribe state (after reconnect)
        self._subscriptions: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self, host: str = "127.0.0.1", port: int = 1883,
                keepalive: int = 60) -> None:
        self._client.connect(host, port, keepalive)

    def loop_start(self) -> None:
        """Start the network loop in a background thread (non-blocking)."""
        self._client.loop_start()

    def loop_stop(self) -> None:
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def loop_forever(self) -> None:
        """Run the network loop in the foreground (blocks)."""
        self._client.loop_forever()

    # ------------------------------------------------------------------
    # Pub / Sub
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to a topic pattern; payloads are auto-decoded as JSON.

        ``topic`` may use MQTT wildcards (``+`` for one level, ``#`` for many).
        ``handler(leaf, payload_dict)`` receives the trailing topic segment
        and the parsed JSON body.
        """
        with self._lock:
            self._handlers[topic] = handler
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)
        # If already connected, subscribe immediately. Otherwise on_connect
        # will replay the list.
        try:
            self._client.subscribe(topic, qos=self.qos)
        except Exception:
            pass

    def publish(self, topic: str, payload: dict, *, retain: bool = False) -> bool:
        """Publish a JSON payload. Returns True if accepted by client buffer."""
        body = json.dumps(payload, ensure_ascii=False, default=_json_default)
        info = self._client.publish(topic, body, qos=self.qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_receive(self, channel: str, payload: dict) -> bool:
        return self.publish(receive_topic(channel), payload)

    def publish_dispatch(self, target: str, payload: dict) -> bool:
        return self.publish(dispatch_topic(target), payload)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("bus[%s] connected", self.client_id)
            with self._lock:
                subs = list(self._subscriptions)
            for topic in subs:
                client.subscribe(topic, qos=self.qos)
        else:
            logger.error("bus[%s] connect failed rc=%s", self.client_id, rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("bus[%s] disconnected rc=%s (will retry)",
                           self.client_id, rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.error("bus[%s] bad payload on %s", self.client_id, msg.topic)
            self._ack_message(msg)
            return

        # Find handler whose pattern matches this topic
        with self._lock:
            handlers = list(self._handlers.items())
        for pattern, handler in handlers:
            if mqtt.topic_matches_sub(pattern, msg.topic):
                leaf = self._topic_leaf(pattern, msg.topic)
                try:
                    handler(leaf, payload)
                except Exception:
                    logger.error("bus[%s] handler %s raised on %s",
                                 self.client_id, pattern, msg.topic,
                                 exc_info=True)
                    return
                self._ack_message(msg)
                # First match wins (one handler per pattern by design)
                return
        self._ack_message(msg)

    def _ack_message(self, msg) -> None:
        if not self.manual_ack:
            return
        qos = int(getattr(msg, "qos", 0) or 0)
        mid = int(getattr(msg, "mid", 0) or 0)
        if qos <= 0 or mid <= 0:
            return
        try:
            self._client.ack(mid, qos)
        except Exception:
            logger.error("bus[%s] ack failed mid=%s qos=%s", self.client_id, mid, qos, exc_info=True)

    @staticmethod
    def _topic_leaf(pattern: str, topic: str) -> str:
        if pattern.endswith("/#"):
            prefix = pattern[:-2]
            if topic == prefix:
                return ""
            if topic.startswith(prefix + "/"):
                return topic[len(prefix) + 1:]
        return topic.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _json_default(obj):
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
