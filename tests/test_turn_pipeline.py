from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.runtime.prompt import append_transcript_messages, build_api_messages, load_timeline_snippets, load_transcript_messages, transcript_path
from fiam.store.beat import Beat, append_beat, read_beats
from fiam.store.events import EventStore
from fiam.store.object_catalog import ObjectCatalog
from fiam.store.pool import Pool
from fiam.turn import AttachmentRef, DispatchRequest, HoldRequest, InboundQueue, MarkerInterpreter, MemoryTimelineStore, MemoryWorker, SummaryRuntimeConfig, TriggerPolicy, TurnCommit, TurnRequest


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


class FakeSummaryRuntime:
    def summarize(self, text: str, *, purpose: str = "event") -> dict:
        return {
            "summary": f"{purpose}: {' '.join(str(text).split())[:80]}",
            "tags": ["memory", "test"],
        }


def write_email_plugin(root: Path, *, capabilities: list[str] | None = None, enabled: bool = True) -> None:
    plugin_dir = root / "plugins" / "email"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    caps = capabilities if capabilities is not None else ["receive", "dispatch", "dispatch_attachment"]
    plugin_dir.joinpath("plugin.toml").write_text(
        "\n".join([
            'id = "email"',
            f"enabled = {'true' if enabled else 'false'}",
            'dispatch_targets = ["email"]',
            "capabilities = [" + ", ".join(json.dumps(item) for item in caps) + "]",
            "",
        ]),
        encoding="utf-8",
    )


class TurnPipelineTest(unittest.TestCase):
    def test_marker_interpreter_returns_structured_commit_parts(self) -> None:
        digest = "a" * 64
        parsed = MarkerInterpreter().interpret(
            'visible <cot>private</cot> <todo at="2026-05-12 20:00">write</todo> '
            f'<state value="mute" reason="focus" /> <send to="email:Zephyr" attach="obj:{digest}">hello</send>'
        )

        self.assertEqual(parsed.visible_reply, "visible")
        self.assertEqual(parsed.private_thoughts, ("private",))
        self.assertEqual(parsed.todo_changes[0].kind, "todo")
        self.assertEqual(parsed.todo_changes[0].reason, "write")
        self.assertEqual(parsed.state_change.state if parsed.state_change else "", "mute")
        self.assertEqual(parsed.dispatch_requests[0].recipient, "Zephyr")
        self.assertEqual(parsed.dispatch_requests[0].attachments[0].object_hash, digest)

    def test_conductor_receive_turn_writes_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_email_plugin(root)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            flow = root / "store" / "flow.jsonl"
            conductor = Conductor(pool=Pool(root / "pool", dim=3), embedder=FakeEmbedder(), config=config, flow_path=flow, memory_mode="manual")

            commit = conductor.receive_turn(
                TurnRequest(
                    channel="chat",
                    actor="user",
                    text="hello",
                    surface="favilla",
                    turn_id="turn_test",
                    request_id="req_test",
                    session_id="sess_test",
                    attachments=(AttachmentRef(object_hash="a" * 64, name="photo.jpg", mime="image/jpeg", size=123),),
                    received_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
                )
            )

            self.assertEqual(commit.turn_id, "turn_test")
            beats = read_beats(flow)
            self.assertEqual(beats[0].meta["turn_id"], "turn_test")
            self.assertEqual(beats[0].meta["request_id"], "req_test")
            self.assertEqual(beats[0].meta["session_id"], "sess_test")
            self.assertEqual(beats[0].surface, "favilla")
            self.assertEqual(len(commit.events), 2)
            self.assertEqual(beats[1].kind, "attachment")
            self.assertEqual(beats[1].meta["direction"], "inbound")
            self.assertEqual(beats[1].meta["object_hash"], "a" * 64)
            self.assertEqual(beats[1].meta["object_name"], "photo.jpg")
            records = ObjectCatalog.from_config(config).search("photo")
            self.assertEqual(records[0].object_hash, "a" * 64)
            self.assertEqual(records[0].direction, "inbound")

    def test_inbound_queue_serializes_turn_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue" / "inbound.jsonl"
            queue_id = InboundQueue(path).enqueue(TurnRequest(channel="chat", actor="user", text="hello", surface="favilla", turn_id="turn_q"))

            row = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertTrue(queue_id.startswith("iq_"))
            self.assertEqual(row["queue_id"], queue_id)
            self.assertEqual(row["turn_id"], "turn_q")
            self.assertEqual(row["channel"], "chat")
            self.assertEqual(row["surface"], "favilla")
            self.assertNotIn("path", json.dumps(row.get("attachments") or []))

            claimed = InboundQueue(path).claim(worker_id="test", lease_seconds=60)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].turn_id, "turn_q")
            self.assertTrue(InboundQueue(path).ack(claimed[0].source_meta["queue_id"]))
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_inbound_queue_claim_ack_retry_dead_letter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue" / "inbound.jsonl"
            queue = InboundQueue(path)
            queue.enqueue(TurnRequest(channel="chat", actor="user", text="hello", surface="favilla", turn_id="turn_q"))

            claimed = queue.claim(worker_id="w1", lease_seconds=60)
            self.assertEqual(len(claimed), 1)
            queue_id = claimed[0].source_meta["queue_id"]
            self.assertEqual(queue.claim(worker_id="w2"), [])

            self.assertTrue(queue.fail(queue_id, error="boom", max_attempts=2, backoff_seconds=0))
            retried = queue.claim(worker_id="w2")
            self.assertEqual(retried[0].turn_id, "turn_q")
            self.assertTrue(queue.fail(queue_id, error="boom again", max_attempts=2, backoff_seconds=0))
            self.assertEqual(queue.claim(worker_id="w3"), [])

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["attempts"], 2)
            self.assertIn("dead_lettered_at", rows[0])

            self.assertTrue(queue.ack(queue_id))
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
                    channel="chat",
                    kind="message",
                    content="visible",
                    runtime="api",
                    surface="favilla",
                ),),
                transcript_messages=({"role": "assistant", "content": "visible"},),
                ui_history_rows=({"role": "ai", "text": "visible", "meta": {"turn_id": "turn_commit"}},),
                dispatch_requests=(DispatchRequest(channel="email", recipient="Zephyr", body="hello"),),
                todo_changes=MarkerInterpreter().interpret('<todo at="2099-05-12 20:00">later</todo>').todo_changes,
                state_change=MarkerInterpreter().interpret('<state value="mute" reason="focus" />').state_change,
                trace={"received": "2026-05-12T00:00:00+00:00"},
            ), channel="chat")

            self.assertTrue(commit.trace["commit_done"].startswith("20"))
            self.assertEqual(commit.trace["trace_file"], "store/turn_traces.jsonl")
            self.assertNotIn("received", commit.trace)
            beats = read_beats(config.flow_path)
            self.assertTrue(any(beat.kind == "message" and beat.content == "visible" for beat in beats))
            self.assertTrue(any(beat.kind == "schedule" and (beat.meta or {}).get("fact_kind") == "schedule" for beat in beats))
            self.assertTrue(any(beat.kind == "state" and (beat.meta or {}).get("fact_kind") == "state" for beat in beats))
            self.assertTrue(any(beat.kind == "dispatch" and (beat.meta or {}).get("dispatch_recipient") == "Zephyr" for beat in beats))
            self.assertTrue((config.home_path / "transcript" / "chat.jsonl").exists())
            self.assertTrue((config.store_dir / "transcripts" / "chat.jsonl").exists())
            self.assertIn("turn_commit", config.todo_path.read_text(encoding="utf-8"))
            state = json.loads(config.ai_state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "mute")
            trace_path = config.store_dir / "turn_traces.jsonl"
            self.assertTrue(trace_path.exists())
            trace_rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            phases = [row["phase"] for row in trace_rows]
            self.assertIn("commit.start", phases)
            self.assertIn("commit.events", phases)
            self.assertIn("commit.dispatch", phases)
            self.assertIn("commit.hold", phases)
            self.assertIn("commit.input_trace", phases)
            self.assertIn("commit.done", phases)
            self.assertTrue(all("trace" not in row for row in trace_rows))

    def test_commit_turn_writes_held_fact_object_and_read_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual")

            conductor.commit_turn(TurnCommit(
                turn_id="turn_held",
                request_id="req_held",
                session_id="sess_held",
                surface="favilla",
                hold_request=HoldRequest(
                    status="held",
                    reason="needs more thought",
                    raw_text="draft answer <held>needs more thought</held>",
                    summary="needs more thought",
                ),
            ), channel="chat")

            beats = read_beats(config.flow_path)
            hold_beats = [beat for beat in beats if beat.kind == "hold"]
            self.assertEqual(len(hold_beats), 1)
            self.assertEqual((hold_beats[0].meta or {}).get("hold_status"), "held")
            object_hash = str((hold_beats[0].meta or {}).get("object_hash") or "")
            self.assertTrue(object_hash)
            self.assertIn("draft answer", (config.object_dir / object_hash[:2] / f"{object_hash}.txt").read_text(encoding="utf-8"))
            held_rows = [json.loads(line) for line in config.held_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(held_rows[0]["status"], "open")
            self.assertEqual(held_rows[0]["hold_status"], "held")

    def test_memory_worker_writes_markdown_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            flow = config.flow_path
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            event_id = append_beat(flow, Beat(
                t=datetime(2026, 5, 13, 14, 8, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="discussed DATA-020 trace and memory timeline",
                surface="favilla",
                meta={"turn_id": "turn_timeline", "request_id": "req_timeline"},
            ))

            processed = MemoryWorker(event_store, embedder=FakeEmbedder(), model_id="fake").process_once()

            self.assertEqual(processed, 1)
            daily = config.timeline_dir / "2026-05-13.md"
            self.assertTrue(daily.exists())
            text = daily.read_text(encoding="utf-8")
            self.assertIn("### 14:08 turn_timeline", text)
            self.assertIn(f"event:{event_id}", text)
            self.assertIn("chat/favilla", text)
            self.assertIn("2026-05-13.md", (config.timeline_dir / "index.md").read_text(encoding="utf-8"))
            self.assertIn("## 2026-05-13", (config.timeline_dir / "2026-05.md").read_text(encoding="utf-8"))
            self.assertIn("## 2026-05", (config.timeline_dir / "2026.md").read_text(encoding="utf-8"))
            queried = MemoryTimelineStore(config.timeline_dir).query("DATA-020")
            self.assertEqual(queried[0]["path"], "2026-05-13.md")
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("memory.done", [row["phase"] for row in trace_rows])

    def test_conductor_enqueues_pool_graph_job_after_pool_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="auto")
            config.ensure_dirs()
            pool = Pool(config.pool_dir, dim=3)
            conductor = Conductor(pool=pool, embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="auto")

            conductor._post_ingest(["pool_event_1"])

            jobs = EventStore(config.event_db_path, object_dir=config.object_dir).read_memory_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["event_id"], "pool_event_1")
            self.assertEqual(jobs[0]["kind"], "pool_graph")
            self.assertEqual(jobs[0]["status"], "pending")

    def test_memory_worker_processes_pool_graph_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            pool = Pool(config.pool_dir, dim=3)
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            v2 = np.array([0.95, 0.05, 0.0], dtype=np.float32)
            v2 = v2 / np.linalg.norm(v2)
            pool.ingest_event("pool_a", datetime(2026, 5, 13, 14, 8, tzinfo=timezone.utc), "first memory", v1)
            pool.ingest_event("pool_b", datetime(2026, 5, 13, 14, 9, tzinfo=timezone.utc), "second memory", v2)
            event_store.enqueue_memory_job("pool_b", kind="pool_graph")

            processed = MemoryWorker(
                event_store,
                embedder=FakeEmbedder(),
                pool=pool,
                config=config,
                model_id="fake",
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            jobs = event_store.read_memory_jobs()
            self.assertEqual(jobs[0]["status"], "done")
            edge_index, _edge_attr = pool.load_edges()
            self.assertGreater(edge_index.shape[1], 0)
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            graph_rows = [row for row in trace_rows if row.get("refs", {}).get("job_kind") == "pool_graph"]
            self.assertEqual(graph_rows[-1]["refs"]["pool_graph"], "done")

    def test_memory_worker_enqueues_derived_jobs_after_event_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            pool = Pool(config.pool_dir, dim=3)
            event_id = append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 14, 8, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="derived memory jobs should be queued",
            ))

            processed = MemoryWorker(
                event_store,
                embedder=FakeEmbedder(),
                pool=pool,
                config=config,
                model_id="fake",
                summary_runtime=FakeSummaryRuntime(),
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            jobs = event_store.read_memory_jobs()
            jobs_by_key = {(row["event_id"], row["kind"]): row for row in jobs}
            self.assertEqual(jobs_by_key[(event_id, "event")]["status"], "done")
            self.assertIn((event_id, "summary"), jobs_by_key)
            self.assertIn((event_id, "recall_warmup"), jobs_by_key)
            self.assertIn(("transcript:chat", "transcript_compaction"), jobs_by_key)

    def test_memory_worker_processes_summary_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            object_hash = "d" * 64
            event_id = append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 14, 8, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="attachment",
                content="quarterly report attachment with relevant details",
                meta={"object_hash": object_hash, "object_name": "report.txt", "object_mime": "text/plain"},
            ))
            event_store.enqueue_memory_job(event_id, kind="summary")

            processed = MemoryWorker(
                event_store,
                config=config,
                summary_runtime=FakeSummaryRuntime(),
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            event = event_store.read_event(event_id)
            self.assertIsNotNone(event)
            meta = event.meta or {}
            self.assertIn("attachment:", meta["summary"])
            self.assertEqual(meta["object_summary"], meta["summary"])
            self.assertTrue(meta["summary_ref"])
            self.assertEqual(meta["tags"], ["memory", "test"])
            self.assertEqual(event_store.read_memory_jobs()[0]["status"], "done")

    def test_memory_worker_compacts_transcript_and_loader_keeps_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            append_transcript_messages(
                config,
                "chat",
                [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"} for i in range(125)],
            )
            event_store.enqueue_memory_job("transcript:chat", kind="transcript_compaction")

            processed = MemoryWorker(
                event_store,
                config=config,
                summary_runtime=FakeSummaryRuntime(),
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            lines = transcript_path(config, "chat").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 121)
            first = json.loads(lines[0])
            self.assertEqual(first["role"], "system")
            self.assertTrue(first["content"].startswith("[transcript_compaction]"))
            loaded = load_transcript_messages(config, "chat")
            self.assertEqual(len(loaded), 80)
            self.assertEqual(loaded[0]["role"], "system")
            self.assertIn("[transcript_compaction]", loaded[0]["content"])
            self.assertEqual(event_store.read_memory_jobs()[0]["status"], "done")

    def test_memory_worker_processes_recall_warmup_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual", recall_top_k=2)
            config.ensure_dirs()
            pool = Pool(config.pool_dir, dim=3)
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            v2 = np.array([0.95, 0.05, 0.0], dtype=np.float32)
            v2 = v2 / np.linalg.norm(v2)
            pool.ingest_event("pool_a", datetime(2026, 5, 12, 14, 8, tzinfo=timezone.utc), "first warmup memory", v1)
            pool.ingest_event("pool_b", datetime(2026, 5, 12, 14, 9, tzinfo=timezone.utc), "second warmup memory", v2)
            event_store.enqueue_memory_job("pool_b", kind="recall_warmup")

            processed = MemoryWorker(
                event_store,
                pool=pool,
                config=config,
                summary_runtime=FakeSummaryRuntime(),
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            path = config.store_dir / "recall_warmup.jsonl"
            self.assertTrue(path.exists())
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["seed_event_id"], "pool_b")
            self.assertGreaterEqual(row["fragment_count"], 1)
            self.assertEqual(event_store.read_memory_jobs()[0]["status"], "done")

    def test_memory_worker_dead_letters_failed_job_without_blocking_later_events(self) -> None:
        class SometimesFailEmbedder(FakeEmbedder):
            def embed(self, text: str):
                if "bad memory" in text:
                    raise RuntimeError("embed failed")
                return super().embed(text)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            flow = config.flow_path
            event_store = EventStore(config.event_db_path, object_dir=config.object_dir)
            bad_id = append_beat(flow, Beat(
                t=datetime(2026, 5, 13, 14, 8, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="bad memory",
            ))
            good_id = append_beat(flow, Beat(
                t=datetime(2026, 5, 13, 14, 9, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="good memory",
            ))

            processed = MemoryWorker(
                event_store,
                embedder=SometimesFailEmbedder(),
                model_id="fake",
                max_attempts=1,
                backoff_seconds=0,
            ).process_once(limit=10)

            self.assertEqual(processed, 1)
            jobs = event_store.read_memory_jobs()
            jobs_by_event = {row["event_id"]: row for row in jobs if row["kind"] == "event"}
            self.assertEqual(jobs_by_event[bad_id]["status"], "dead_letter")
            self.assertEqual(jobs_by_event[bad_id]["attempts"], 1)
            self.assertEqual(jobs_by_event[good_id]["status"], "done")
            remaining = event_store.read_unembedded(limit=10)
            self.assertEqual([beat.meta["event_id"] for beat in remaining], [bad_id])
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            phases = [row["phase"] for row in trace_rows]
            self.assertIn("memory.failed", phases)
            self.assertIn("memory.done", phases)

    def test_prompt_loads_only_selected_timeline_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            (config.timeline_dir / "2026-05-13.md").write_text("# day\n\n### old\n- should not auto inject\n", encoding="utf-8")
            (config.timeline_dir / "context.md").write_text("- selected DATA-020 timeline snippet", encoding="utf-8")

            self.assertEqual(load_timeline_snippets(config), "- selected DATA-020 timeline snippet")
            messages = build_api_messages(config, "hello", include_recall=False)
            joined = json.dumps(messages, ensure_ascii=False)
            self.assertIn("selected DATA-020 timeline snippet", joined)
            self.assertNotIn("should not auto inject", joined)

    def test_dispatch_status_events_share_dispatch_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_email_plugin(root)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            bus = FakeBus()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual", bus=bus)

            digest = "b" * 64
            conductor.commit_turn(TurnCommit(
                turn_id="turn_dispatch",
                request_id="req_dispatch",
                dispatch_requests=(DispatchRequest(
                    channel="email",
                    recipient="Zephyr",
                    body="hello",
                    marker_index=2,
                    attachments=(AttachmentRef(object_hash=digest, name="note.txt", mime="text/plain", size=12),),
                ),),
            ), channel="chat")

            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            attachment_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "attachment"]
            statuses = [(beat.meta or {}).get("dispatch_status") for beat in dispatch_beats]
            ids = {(beat.meta or {}).get("dispatch_id") for beat in dispatch_beats}
            self.assertEqual(statuses, ["accepted", "published"])
            self.assertEqual(len(ids), 1)
            self.assertTrue(str(next(iter(ids))).startswith("disp_"))
            self.assertEqual(bus.payloads[0][0], "email")
            self.assertEqual(bus.payloads[0][1]["attachments"][0]["object_hash"], digest)
            self.assertEqual((dispatch_beats[0].meta or {}).get("attachment_hashes"), [digest])
            self.assertEqual((attachment_beats[0].meta or {}).get("object_hash"), digest)
            self.assertEqual((attachment_beats[0].meta or {}).get("object_mime"), "text/plain")
            self.assertEqual(bus.payloads[0][1]["dispatch_id"], next(iter(ids)))
            self.assertEqual(bus.payloads[0][1]["turn_id"], "turn_dispatch")
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            phases = [row["phase"] for row in trace_rows]
            self.assertIn("dispatch.accepted", phases)
            self.assertIn("dispatch.published", phases)

    def test_attachment_dispatch_without_capability_fails_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_email_plugin(root, capabilities=["dispatch"])
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            bus = FakeBus()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual", bus=bus)

            conductor.commit_turn(TurnCommit(
                turn_id="turn_no_attach_cap",
                request_id="req_no_attach_cap",
                dispatch_requests=(DispatchRequest(
                    channel="email",
                    recipient="Zephyr",
                    body="hello",
                    attachments=(AttachmentRef(object_hash="c" * 64, name="note.txt"),),
                ),),
            ), channel="chat")

            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual(bus.payloads, [])
            self.assertEqual([(beat.meta or {}).get("dispatch_status") for beat in dispatch_beats], ["failed"])
            self.assertIn("does not support attachments", (dispatch_beats[0].meta or {}).get("dispatch_last_error"))
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("dispatch.failed", [row["phase"] for row in trace_rows])

    def test_disabled_dispatch_target_writes_failed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_email_plugin(root, enabled=False)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            bus = FakeBus()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual", bus=bus)

            conductor.commit_turn(TurnCommit(
                turn_id="turn_plugin_disabled",
                request_id="req_plugin_disabled",
                dispatch_requests=(DispatchRequest(
                    channel="email",
                    recipient="Zephyr",
                    body="hello",
                    marker_index=3,
                ),),
            ), channel="chat")

            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual(bus.payloads, [])
            self.assertEqual([(beat.meta or {}).get("dispatch_status") for beat in dispatch_beats], ["failed"])
            self.assertIn("plugin disabled", (dispatch_beats[0].meta or {}).get("dispatch_last_error"))
            self.assertTrue(str((dispatch_beats[0].meta or {}).get("dispatch_id") or "").startswith("disp_"))
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("dispatch.failed", [row["phase"] for row in trace_rows])

    def test_invalid_dispatch_attachment_writes_failed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            bus = FakeBus()
            conductor = Conductor(pool=Pool(config.pool_dir, dim=3), embedder=FakeEmbedder(), config=config, flow_path=config.flow_path, memory_mode="manual", bus=bus)

            conductor.commit_turn(TurnCommit(
                turn_id="turn_bad_attach",
                request_id="req_bad_attach",
                dispatch_requests=(DispatchRequest(
                    channel="email",
                    recipient="Zephyr",
                    body="hello",
                    marker_index=1,
                    attachment_errors=("unresolved object token: obj:badtoken",),
                ),),
            ), channel="chat")

            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual(bus.payloads, [])
            self.assertEqual([(beat.meta or {}).get("dispatch_status") for beat in dispatch_beats], ["failed"])
            self.assertIn("unresolved object token", (dispatch_beats[0].meta or {}).get("dispatch_last_error"))
            self.assertEqual((dispatch_beats[0].meta or {}).get("attachment_errors"), ["unresolved object token: obj:badtoken"])

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
