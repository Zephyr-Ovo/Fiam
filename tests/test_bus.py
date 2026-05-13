from __future__ import annotations

import json
import unittest

from fiam.bus import Bus, RECEIVE_ALL


class FakeClient:
    def __init__(self) -> None:
        self.acks: list[tuple[int, int]] = []
        self.subscriptions: list[tuple[str, int]] = []

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscriptions.append((topic, qos))

    def ack(self, mid: int, qos: int) -> None:
        self.acks.append((mid, qos))


class FakeMessage:
    def __init__(self, topic: str, payload: dict | bytes, *, mid: int = 7, qos: int = 1) -> None:
        self.topic = topic
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.mid = mid
        self.qos = qos


class BusTest(unittest.TestCase):
    def test_manual_ack_after_handler_success(self) -> None:
        bus = Bus(client_id="test-bus")
        fake_client = FakeClient()
        bus._client = fake_client
        seen: list[tuple[str, dict]] = []

        bus.subscribe(RECEIVE_ALL, lambda leaf, payload: seen.append((leaf, payload)))
        bus._on_message(fake_client, None, FakeMessage("fiam/receive/chat", {"text": "hello"}, mid=42, qos=1))

        self.assertEqual(seen, [("chat", {"text": "hello"})])
        self.assertEqual(fake_client.acks, [(42, 1)])

    def test_receive_wildcard_preserves_nested_channel_suffix(self) -> None:
        bus = Bus(client_id="test-bus")
        fake_client = FakeClient()
        bus._client = fake_client
        seen: list[tuple[str, dict]] = []

        bus.subscribe(RECEIVE_ALL, lambda leaf, payload: seen.append((leaf, payload)))
        bus._on_message(fake_client, None, FakeMessage("fiam/receive/desktop/result", {"text": "done"}, mid=45, qos=1))

        self.assertEqual(seen, [("desktop/result", {"text": "done"})])
        self.assertEqual(fake_client.acks, [(45, 1)])

    def test_manual_ack_is_skipped_when_handler_fails(self) -> None:
        bus = Bus(client_id="test-bus")
        fake_client = FakeClient()
        bus._client = fake_client

        def fail(_leaf: str, _payload: dict) -> None:
            raise RuntimeError("queue unavailable")

        bus.subscribe(RECEIVE_ALL, fail)
        with self.assertLogs("fiam.bus", level="ERROR"):
            bus._on_message(fake_client, None, FakeMessage("fiam/receive/chat", {"text": "hello"}, mid=43, qos=1))

        self.assertEqual(fake_client.acks, [])

    def test_bad_payload_is_acked_as_unprocessable(self) -> None:
        bus = Bus(client_id="test-bus")
        fake_client = FakeClient()
        bus._client = fake_client

        with self.assertLogs("fiam.bus", level="ERROR"):
            bus._on_message(fake_client, None, FakeMessage("fiam/receive/chat", b"not-json", mid=44, qos=1))

        self.assertEqual(fake_client.acks, [(44, 1)])


if __name__ == "__main__":
    unittest.main()