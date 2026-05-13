from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.store.beat import Beat, read_beats
from fiam.store.pool import Pool
from fiam.turn import DispatchRequest, InboundQueue, MarkerInterpreter, SummaryRuntimeConfig, TriggerPolicy, TurnCommit, TurnRequest


class FakeEmbedder:
    def embed(self, text: str):
        import numpy as np

        vec = np.array([1.0, 0.5, 0.25], dtype=np.float32)
        return vec / np.linalg.norm(vec)


class FakeBus:
    def __init__(self) -> None:
        self.payloads = []

    def publish_dispatch(self, target: str, payload: dict) -> bool:
        self.payloads.append((target, payload))
        return True


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

            drained = InboundQueue(path).drain()
            self.assertEqual(len(drained), 1)
            self.assertEqual(drained[0].turn_id, "turn_q")
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_commit_turn_writes_all_read_models_and_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual")

            commit = conductor.commit_turn(TurnCommit(
                turn_id="turn_commit",
                request_id="req_commit",
                session_id="sess_commit",
                events=(Beat(
                    t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                    actor="ai",
                    channel="favilla",
                    kind="message",
                    content="visible",
                    runtime="api",
                ),),
                transcript_messages=({"role": "assistant", "content": "visible"},),
                ui_history_rows=({"role": "ai", "text": "visible", "meta": {"turn_id": "turn_commit"}},),
                dispatch_requests=(DispatchRequest(channel="email", recipient="Zephyr", body="hello"),),
                todo_changes=MarkerInterpreter().interpret('<todo at="2099-05-12 20:00">later</todo>').todo_changes,
                state_change=MarkerInterpreter().interpret('<state value="mute" reason="focus" />').state_change,
                trace={"received": "2026-05-12T00:00:00+00:00"},
            ), channel="favilla")

            self.assertTrue(commit.trace["commit_done"].startswith("20"))
            beats = read_beats(config.flow_path)
            self.assertTrue(any(beat.kind == "message" and beat.content == "visible" for beat in beats))
            self.assertTrue(any(beat.kind == "dispatch" and (beat.meta or {}).get("dispatch_recipient") == "Zephyr" for beat in beats))
            self.assertTrue((config.home_path / "transcript" / "favilla.jsonl").exists())
            self.assertTrue((config.store_dir / "transcripts" / "favilla.jsonl").exists())
            self.assertIn("turn_commit", config.todo_path.read_text(encoding="utf-8"))
            state = json.loads(config.ai_state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "mute")
            self.assertTrue((config.store_dir / "turn_traces.jsonl").exists())

    def test_dispatch_status_events_share_dispatch_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            bus = FakeBus()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual", bus=bus)

            conductor.commit_turn(TurnCommit(
                turn_id="turn_dispatch",
                request_id="req_dispatch",
                dispatch_requests=(DispatchRequest(channel="email", recipient="Zephyr", body="hello", marker_index=2),),
            ), channel="favilla")

            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            statuses = [(beat.meta or {}).get("dispatch_status") for beat in dispatch_beats]
            ids = {(beat.meta or {}).get("dispatch_id") for beat in dispatch_beats}
            self.assertEqual(statuses, ["accepted", "published"])
            self.assertEqual(len(ids), 1)
            self.assertTrue(str(next(iter(ids))).startswith("disp_"))
            self.assertEqual(bus.payloads[0][0], "email")

    def test_trigger_policy_separates_record_and_wake(self) -> None:
        policy = TriggerPolicy()

        self.assertEqual(policy.decide("limen"), "record_only")
        self.assertEqual(policy.decide("chat", ai_state="mute"), "lazy")
        self.assertEqual(policy.decide("chat", interactive=True), "batch")
        self.assertEqual(policy.decide("chat"), "instant")

    def test_summary_runtime_config_reads_env_names_only(self) -> None:
        old = {key: os.environ.get(key) for key in ("FIAM_SUMMARY_PROVIDER", "FIAM_SUMMARY_MODEL", "FIAM_SUMMARY_API_KEY", "FIAM_SUMMARY_BASE_URL")}
        try:
            os.environ["FIAM_SUMMARY_PROVIDER"] = "mimo"
            os.environ["FIAM_SUMMARY_MODEL"] = "summary-test"
            os.environ["FIAM_SUMMARY_API_KEY"] = "secret-value-not-returned"
            os.environ["FIAM_SUMMARY_BASE_URL"] = "https://summary.example/v1"

            config = SummaryRuntimeConfig.from_env()

            self.assertEqual(config.provider, "mimo")
            self.assertEqual(config.model, "summary-test")
            self.assertEqual(config.api_key_env, "FIAM_SUMMARY_API_KEY")
            self.assertEqual(config.base_url, "https://summary.example/v1")
            self.assertNotEqual(config.api_key_env, "secret-value-not-returned")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
