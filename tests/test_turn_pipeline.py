from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.store.beat import read_beats
from fiam.store.pool import Pool
from fiam.turn import InboundQueue, MarkerInterpreter, TriggerPolicy, TurnRequest


class FakeEmbedder:
    def embed(self, text: str):
        import numpy as np

        vec = np.array([1.0, 0.5, 0.25], dtype=np.float32)
        return vec / np.linalg.norm(vec)


class TurnPipelineTest(unittest.TestCase):
    def test_marker_interpreter_returns_structured_commit_parts(self) -> None:
        parsed = MarkerInterpreter().interpret(
            'visible <cot>private</cot> <todo at="2026-05-12 20:00">write</todo> '
            '<state value="mute" reason="focus" /> <send to="email:Zephyr">hello</send>'
        )

        self.assertEqual(parsed.visible_reply, "visible")
        self.assertEqual(parsed.private_thoughts, ("private",))
        self.assertEqual(parsed.todo_changes[0].kind, "todo")
        self.assertEqual(parsed.todo_changes[0].reason, "write")
        self.assertEqual(parsed.state_change.state if parsed.state_change else "", "mute")
        self.assertEqual(parsed.dispatch_requests[0].recipient, "Zephyr")

    def test_conductor_receive_turn_writes_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            flow = root / "store" / "flow.jsonl"
            conductor = Conductor(pool=Pool(root / "pool", dim=3), embedder=FakeEmbedder(), config=config, flow_path=flow, memory_mode="manual")

            commit = conductor.receive_turn(
                TurnRequest(
                    channel="chat",
                    actor="user",
                    text="hello",
                    turn_id="turn_test",
                    request_id="req_test",
                    session_id="sess_test",
                    received_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
                )
            )

            self.assertEqual(commit.turn_id, "turn_test")
            beats = read_beats(flow)
            self.assertEqual(beats[0].meta["turn_id"], "turn_test")
            self.assertEqual(beats[0].meta["request_id"], "req_test")
            self.assertEqual(beats[0].meta["session_id"], "sess_test")

    def test_inbound_queue_serializes_turn_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue" / "inbound.jsonl"
            InboundQueue(path).enqueue(TurnRequest(channel="chat", actor="user", text="hello", turn_id="turn_q"))

            row = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(row["turn_id"], "turn_q")
            self.assertEqual(row["channel"], "favilla")

    def test_trigger_policy_separates_record_and_wake(self) -> None:
        policy = TriggerPolicy()

        self.assertEqual(policy.decide("limen"), "record_only")
        self.assertEqual(policy.decide("chat", ai_state="mute"), "lazy")
        self.assertEqual(policy.decide("chat", interactive=True), "batch")
        self.assertEqual(policy.decide("chat"), "instant")


if __name__ == "__main__":
    unittest.main()
