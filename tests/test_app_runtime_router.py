from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("dashboard_server", SCRIPTS / "dashboard_server.py")
assert spec and spec.loader
dashboard_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_server)
from fiam.config import FiamConfig  # noqa: E402
from fiam.store.beat import Beat, append_beat, read_beats  # noqa: E402
from fiam.store.objects import ObjectStore  # noqa: E402
from fiam.runtime.prompt import load_transcript_messages  # noqa: E402
from fiam.turn import TurnTraceRow, TurnTraceStore  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


class AppRuntimeRouterTest(unittest.TestCase):
    def test_daily_chat_uses_api(self) -> None:
        self.assertEqual(dashboard_server._select_favilla_chat_runtime("今天晚点提醒我喝水"), "api")

    def test_code_chat_uses_cc(self) -> None:
        self.assertEqual(dashboard_server._select_favilla_chat_runtime("帮我看一下 pytest 报错"), "cc")

    def test_attachments_use_cc(self) -> None:
        self.assertEqual(
            dashboard_server._select_favilla_chat_runtime("帮我看这个文件", [{"path": "/tmp/a.txt"}]),
            "cc",
        )

    def test_upload_returns_object_hash_attachment(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            try:
                result = dashboard_server._favilla_upload({
                    "files": [{"name": "pic.jpg", "mime": "image/jpeg", "data": "aGVsbG8="}],
                })
                attachment = result["files"][0]
                safe = dashboard_server._validate_app_attachments([attachment])
                path_only = dashboard_server._validate_app_attachments([{"path": attachment["path"], "mime": "image/jpeg"}])
                self.assertEqual(len(attachment["object_hash"]), 64)
                self.assertTrue(Path(attachment["path"]).exists())
                self.assertEqual(safe[0]["object_hash"], attachment["object_hash"])
                self.assertEqual(path_only, [])
            finally:
                dashboard_server._CONFIG = original_config

    def test_cot_segments_preserve_order(self) -> None:
        reply, thoughts, locked, segments = dashboard_server._parse_cot(
            "<cot>checking mood</cot>Visible reply.<cot>second pass</cot>Tail."
        )
        self.assertFalse(locked)
        self.assertEqual(reply, "Visible reply.\n\nTail.")
        self.assertEqual([segment["type"] for segment in segments], ["thought", "text", "thought", "text"])
        self.assertEqual(len(thoughts), 2)

    def test_locked_cot_hides_raw_text(self) -> None:
        _reply, thoughts, locked, segments = dashboard_server._parse_cot(
            "<lock/><cot>secret plan detail</cot>Visible."
        )
        self.assertTrue(locked)
        self.assertNotIn("secret plan detail", str(thoughts))
        self.assertNotIn("secret plan detail", str(segments))

    def test_route_marker_is_private_control(self) -> None:
        reply, queued_todos, hold_reason, route = dashboard_server._apply_app_control_markers(
            'visible <route family="gemini" reason="needs math" />',
            channel="chat",
            runtime="api",
            user_text="check this",
            attachments=[],
        )

        self.assertEqual(reply, "visible")
        self.assertEqual(queued_todos, 0)
        self.assertEqual(hold_reason, "")
        self.assertEqual(route, {"family": "gemini", "reason": "needs math"})

    def test_cc_action_events_merge_tool_use_and_result(self) -> None:
        actions = dashboard_server._combine_cc_action_events([
            {"kind": "tool_use", "tool_use_id": "u1", "tool_name": "Bash", "summary": "Show status"},
            {"kind": "tool_result", "tool_use_id": "u1", "tool_name": "Bash", "is_error": False, "summary": "clean"},
        ])

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["kind"], "tool_action")
        self.assertEqual(actions[0]["tool_name"], "Bash")
        self.assertEqual(actions[0]["input_summary"], "Show status")
        self.assertEqual(actions[0]["result_summary"], "clean")
        self.assertEqual(actions[0]["status"], "ok")

    def test_cc_tool_history_enters_transcript_with_object_ref(self) -> None:
        original_config = dashboard_server._CONFIG
        original_pool = dashboard_server._POOL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = FiamConfig(home_path=root / "home", code_path=root / "code", memory_mode="manual")
                config.ensure_dirs()
                dashboard_server._CONFIG = config
                dashboard_server._POOL = object()
                large_result = "tool output " * 600

                dashboard_server._record_cc_app_turn(
                    "run tool",
                    "finished",
                    "chat",
                    surface="favilla",
                    action_events=[{
                        "kind": "tool_action",
                        "tool_use_id": "tool_1",
                        "tool_name": "Bash",
                        "input_summary": "echo lots",
                        "result_summary": "large output",
                        "result_full": large_result,
                        "is_error": False,
                    }],
                    turn_id="turn_cc_tool_history",
                    request_id="req_cc_tool_history",
                )

                messages = load_transcript_messages(config, "chat", max_messages=20)
                tool_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "tool_result"]

                self.assertEqual([message["role"] for message in messages], ["user", "assistant", "tool", "assistant"])
                self.assertEqual(messages[1]["tool_calls"][0]["id"], "tool_1")
                self.assertIn("object_ref hash=", messages[2]["content"])
                self.assertNotEqual(messages[2]["content"], large_result)
                object_hash = str((tool_beats[0].meta or {}).get("object_hash") or "")
                self.assertEqual(len(object_hash), 64)
                self.assertTrue((config.object_dir / object_hash[:2] / f"{object_hash}.txt").exists())
        finally:
            dashboard_server._CONFIG = original_config
            dashboard_server._POOL = original_pool

    def test_stream_persists_transcript_before_done(self) -> None:
        original_config = dashboard_server._CONFIG
        original_iter = dashboard_server._iter_cc_favilla_chat_events
        original_persist = dashboard_server._persist_favilla_ai_transcript
        original_rollover = dashboard_server._check_and_run_session_rollover
        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")

            def fake_iter(**_kwargs):
                yield {"event": "start", "data": {"runtime": "cc"}}
                yield {"event": "text_delta", "data": {"index": 0, "text": "hello"}}
                yield {"event": "done", "data": {"ok": True, "runtime": "cc", "reply": "hello", "metrics": {"runtime": "cc"}}}

            def fake_persist(*args, **kwargs):
                order.append("persist")
                return original_persist(*args, **kwargs)

            dashboard_server._iter_cc_favilla_chat_events = fake_iter
            dashboard_server._persist_favilla_ai_transcript = fake_persist
            dashboard_server._check_and_run_session_rollover = lambda _channel: None

            events = []
            for ev in dashboard_server._favilla_chat_send_stream({
                "text": "hi",
                "runtime": "cc",
                "request_id": "test-stream",
                "client_sent_at": 1.0,
            }):
                if ev.get("event") == "commit":
                    order.append("commit")
                if ev.get("event") == "done":
                    order.append("done")
                events.append(ev)
            history = dashboard_server._favilla_transcript_load("chat")["messages"]
            trace_rows = [json.loads(line) for line in (dashboard_server._CONFIG.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]

        dashboard_server._CONFIG = original_config
        dashboard_server._iter_cc_favilla_chat_events = original_iter
        dashboard_server._persist_favilla_ai_transcript = original_persist
        dashboard_server._check_and_run_session_rollover = original_rollover

        self.assertEqual(order, ["persist", "commit", "done"])
        self.assertEqual(history[-1]["role"], "ai")
        self.assertEqual(history[-1]["text"], "hello")
        self.assertEqual(events[-1]["data"]["transcript_id"], history[-1]["id"])
        self.assertEqual(events[-2]["event"], "commit")
        self.assertEqual(events[-2]["data"]["transcript_id"], history[-1]["id"])
        self.assertEqual(history[-1]["meta"]["trace"]["request_id"], "test-stream")
        self.assertEqual(history[-1]["meta"]["trace"]["trace_file"], "store/turn_traces.jsonl")
        phases = [row["phase"] for row in trace_rows]
        self.assertIn("dashboard.receive", phases)
        self.assertIn("dashboard.runtime", phases)
        self.assertIn("dashboard.persist", phases)

    def test_non_stream_send_writes_receive_trace(self) -> None:
        original_config = dashboard_server._CONFIG
        original_run_api = dashboard_server._run_api_favilla_chat
        original_rollover = dashboard_server._check_and_run_session_rollover
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._run_api_favilla_chat = lambda **_kwargs: {"ok": True, "reply": "hello", "raw_reply": "hello", "metrics": {}}
            dashboard_server._check_and_run_session_rollover = lambda _channel: None

            result = dashboard_server._favilla_chat_send({
                "text": "hi",
                "runtime": "api",
                "turn_id": "turn-non-stream",
                "request_id": "req-non-stream",
                "client_sent_at": 1.0,
            })
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]

        dashboard_server._CONFIG = original_config
        dashboard_server._run_api_favilla_chat = original_run_api
        dashboard_server._check_and_run_session_rollover = original_rollover

        self.assertTrue(result["ok"])
        receive_rows = [row for row in trace_rows if row["phase"] == "dashboard.receive"]
        self.assertEqual(len(receive_rows), 1)
        self.assertEqual(receive_rows[0]["turn_id"], "turn-non-stream")
        self.assertEqual(receive_rows[0]["request_id"], "req-non-stream")
        self.assertEqual(receive_rows[0]["refs"]["runtime"], "api")
        self.assertEqual(receive_rows[0]["refs"]["client_sent_at"], "1.0")

    def test_cc_stream_text_delta_buffers_control_markers(self) -> None:
        original_config = dashboard_server._CONFIG
        original_record_turn = dashboard_server._record_cc_app_turn
        original_debug_context = dashboard_server._record_debug_context
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._record_cc_app_turn = lambda *args, **kwargs: None
            dashboard_server._record_debug_context = lambda *args, **kwargs: None

            class FakeProc:
                def __init__(self) -> None:
                    self.stdout = iter([
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "visible <hold>secret</hold> tail"}]}}) + "\n",
                        json.dumps({"type": "result", "result": "visible <hold>secret</hold> tail", "session_id": "sess_stream", "is_error": False}) + "\n",
                    ])
                    self.stderr = self
                    self.returncode = 0

                def wait(self, timeout=None):
                    return 0

                def read(self):
                    return ""

                def kill(self):
                    self.returncode = -9

            with patch.dict(os.environ, {"FIAM_CC_WARM_DISABLED": "1"}), patch("subprocess.Popen", return_value=FakeProc()):
                events = list(dashboard_server._iter_cc_favilla_chat_events(
                    text="hi",
                    channel="chat",
                    surface="favilla",
                    turn_id="turn_stream_marker",
                    request_id="req_stream_marker",
                ))

        dashboard_server._CONFIG = original_config
        dashboard_server._record_cc_app_turn = original_record_turn
        dashboard_server._record_debug_context = original_debug_context

        live_events = [event for event in events if event["event"] != "done"]
        self.assertIn("text_delta", [event["event"] for event in live_events])
        self.assertNotIn("text", [event["event"] for event in live_events])
        self.assertNotIn("<hold>", json.dumps(live_events, ensure_ascii=False))

    def test_cc_stream_text_delta_preserves_spacing(self) -> None:
        original_config = dashboard_server._CONFIG
        original_record_turn = dashboard_server._record_cc_app_turn
        original_debug_context = dashboard_server._record_debug_context
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._record_cc_app_turn = lambda *args, **kwargs: None
            dashboard_server._record_debug_context = lambda *args, **kwargs: None

            class FakeProc:
                def __init__(self) -> None:
                    self.stdout = iter([
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello "}]}}) + "\n",
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}}) + "\n",
                        json.dumps({"type": "result", "result": "hello world", "session_id": "sess_stream", "is_error": False}) + "\n",
                    ])
                    self.stderr = self
                    self.returncode = 0

                def wait(self, timeout=None):
                    return 0

                def read(self):
                    return ""

                def kill(self):
                    self.returncode = -9

            with patch.dict(os.environ, {"FIAM_CC_WARM_DISABLED": "1"}), patch("subprocess.Popen", return_value=FakeProc()):
                events = list(dashboard_server._iter_cc_favilla_chat_events(
                    text="hi",
                    channel="chat",
                    surface="favilla",
                    turn_id="turn_stream_space",
                    request_id="req_stream_space",
                ))

        dashboard_server._CONFIG = original_config
        dashboard_server._record_cc_app_turn = original_record_turn
        dashboard_server._record_debug_context = original_debug_context

        deltas = [event["data"]["text"] for event in events if event["event"] == "text_delta"]
        self.assertEqual("".join(deltas), "hello world")

    def test_cc_warm_prompt_does_not_inject_runtime_context(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            config.constitution_md_path.write_text("stable constitution", encoding="utf-8")
            config.manual_md_path.write_text("stable manual", encoding="utf-8")
            (config.self_dir / "identity.md").write_text("stable identity", encoding="utf-8")
            dashboard_server._CONFIG = config

            cold_system, cold_user = dashboard_server._build_cc_favilla_prompt_parts(
                "hello",
                channel="chat",
                recall_context=None,
                warm=False,
            )
            warm_system, warm_user = dashboard_server._build_cc_favilla_prompt_parts(
                "hello",
                channel="chat",
                recall_context=None,
                warm=True,
            )

        dashboard_server._CONFIG = original_config

        self.assertIn("stable constitution", warm_system)
        self.assertIn("stable manual", warm_system)
        self.assertIn("stable identity", warm_system)
        self.assertNotIn("[server_time]", warm_system)
        self.assertNotIn("[context]", warm_system)
        self.assertNotIn("[server_time]", warm_user)
        self.assertTrue(warm_user.endswith("hello"))
        self.assertIn("[server_time]", cold_system)

    def test_cc_hook_scrub_removes_hook_attachment_rows(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            session_id = "sess_scrub"
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "claude")}, clear=False):
                path = dashboard_server._cc_project_transcript_path(session_id)
                assert path is not None
                path.parent.mkdir(parents=True, exist_ok=True)
                user_row = {
                    "type": "user",
                    "uuid": "user_1",
                    "message": {
                        "role": "user",
                        "content": "before hook after",
                    },
                }
                hook_row = {
                    "type": "attachment",
                    "parentUuid": "user_1",
                    "attachment": {
                        "type": "hook_additional_context",
                        "content": ["[recall]\nsecret hook recall\n\n[external]\nsecret hook external"],
                    },
                }
                assistant_row = {"type": "assistant", "message": {"role": "assistant", "content": "visible"}}
                path.write_text(
                    "\n".join(json.dumps(row, ensure_ascii=False) for row in (user_row, hook_row, assistant_row)) + "\n",
                    encoding="utf-8",
                )

                changed = dashboard_server._cc_scrub_hook_transcript({"session_id": session_id})
                lines = path.read_text(encoding="utf-8").splitlines()
                scrubbed = [json.loads(line) for line in lines]

        dashboard_server._CONFIG = original_config

        self.assertTrue(changed)
        self.assertEqual([row["type"] for row in scrubbed], ["user", "assistant"])
        self.assertIn("before hook after", scrubbed[0]["message"]["content"])
        self.assertNotIn("secret hook", "\n".join(lines))

    def test_object_token_extraction_and_download(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            object_hash = ObjectStore(config.object_dir).put_bytes(b"hello file", suffix="")
            manifest = config.home_path / "uploads" / "manifest.jsonl"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "uploaded_at": "2026-05-13T09:00:00+00:00",
                "object_hash": object_hash,
                "name": "hello.txt",
                "mime": "text/plain",
                "size": 10,
                "direction": "outbound",
            }) + "\n", encoding="utf-8")

            attachments = dashboard_server._extract_object_attachments(f"made obj:{object_hash[:12]}")
            body, name, mime = dashboard_server._favilla_object_download(f"obj:{object_hash[:12]}")

        dashboard_server._CONFIG = original_config

        self.assertEqual(attachments[0]["object_hash"], object_hash)
        self.assertEqual(attachments[0]["name"], "hello.txt")
        self.assertEqual(body, b"hello file")
        self.assertEqual(name, "hello.txt")
        self.assertEqual(mime, "text/plain")

    def test_official_thoughts_use_summary_helper(self) -> None:
        original_summary = dashboard_server.summarize_cot_steps
        try:
            dashboard_server.summarize_cot_steps = lambda steps, locked, config: [
                {"index": 0, "summary": "checking tool path", "icon": "Search"}
            ]
            thoughts, segments = dashboard_server._official_thought_payloads([
                {"text": "The user wants me to inspect the runtime path before replying."}
            ])
        finally:
            dashboard_server.summarize_cot_steps = original_summary

        self.assertEqual(thoughts[0]["summary"], "checking tool path")
        self.assertEqual(thoughts[0]["source"], "official")
        self.assertEqual(segments[0]["type"], "thought")
        self.assertEqual(segments[0]["icon"], "Search")

    def test_memory_worker_helper_writes_timeline(self) -> None:
        original_config = dashboard_server._CONFIG
        original_get_embedder = dashboard_server._get_embedder

        class FakeEmbedder:
            def embed(self, _text: str):
                import numpy as np

                vec = np.array([1.0, 0.5, 0.25], dtype=np.float32)
                return vec / np.linalg.norm(vec)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code", embedding_backend="local", embedding_dim=3)
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._get_embedder = lambda: FakeEmbedder()
            event_id = append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 16, 0, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="memory worker dashboard helper",
                meta={"turn_id": "turn_worker"},
            ))

            processed = dashboard_server._run_memory_worker_once(limit=10)
            daily = config.timeline_dir / "2026-05-13.md"

            self.assertEqual(processed, 1)
            self.assertTrue(daily.exists())
            self.assertIn(f"event:{event_id}", daily.read_text(encoding="utf-8"))

        dashboard_server._CONFIG = original_config
        dashboard_server._get_embedder = original_get_embedder

    def test_debug_trace_api_filters_rows(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            store = TurnTraceStore(config.store_dir / "turn_traces.jsonl")
            store.append_many((
                TurnTraceRow(turn_id="turn_a", request_id="req_a", phase="dashboard.receive", status="ok"),
                TurnTraceRow(turn_id="turn_b", request_id="req_b", phase="dashboard.runtime", status="error", error="provider failed", duration_ms=42),
            ))

            result = dashboard_server._api_debug_trace({"turn_id": "turn_b", "limit": "10"})

        dashboard_server._CONFIG = original_config

        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["rows"][0]["request_id"], "req_b")
        self.assertEqual(result["rows"][0]["phase"], "dashboard.runtime")
        self.assertEqual(result["filters"], {"turn_id": "turn_b"})
        self.assertEqual(result["summary"]["by_status"]["error"], 1)
        self.assertEqual(result["summary"]["slowest"][0]["duration_ms"], 42)

    def test_timeline_api_queries_markdown_timeline(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            (config.timeline_dir / "2026-05-13.md").write_text(
                "# 2026-05-13\n\n### 14:08 turn_timeline\n- user@chat message: DATA-020 query test\n- refs: event:ev_timeline turn:turn_timeline\n",
                encoding="utf-8",
            )

            result = dashboard_server._api_timeline({"q": "query", "limit": "5"})

        dashboard_server._CONFIG = original_config

        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["records"][0]["path"], "2026-05-13.md")
        self.assertIn("event:ev_timeline", result["records"][0]["refs"])

    def test_objects_api_searches_catalog_and_resolves_token(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            digest = "d" * 64
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="attachment",
                content="attachment: diagram.png",
                meta={
                    "object_hash": digest,
                    "object_name": "diagram.png",
                    "object_mime": "image/png",
                    "object_size": 42,
                    "direction": "inbound",
                },
            ))

            result = dashboard_server._api_objects({"q": "diagram", "token": f"obj:{digest[:12]}"})

        dashboard_server._CONFIG = original_config

        self.assertEqual(result["object_hash"], digest)
        self.assertEqual(result["records"][0]["object_hash"], digest)
        self.assertEqual(result["records"][0]["direction"], "inbound")
        self.assertNotIn("path", json.dumps(result, ensure_ascii=False))

    def test_flow_api_normalizes_legacy_favilla_app_channels(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 18, 0, tzinfo=timezone.utc),
                actor="user",
                channel="favilla",
                kind="message",
                content="old favilla",
            ))
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 18, 1, tzinfo=timezone.utc),
                actor="user",
                channel="app",
                kind="message",
                content="old app",
            ))

            result = dashboard_server._api_flow(0, 10)

        dashboard_server._CONFIG = original_config

        scenes = [(row["channel"], row.get("surface")) for row in result["beats"]]
        self.assertEqual(scenes, [("chat", "favilla"), ("chat", "favilla")])

    def test_api_runtime_wrapper_writes_phase_trace_rows(self) -> None:
        from types import SimpleNamespace
        import fiam.runtime.api as api_module

        original_config = dashboard_server._CONFIG
        original_pool = dashboard_server._POOL
        original_api_runtime = api_module.ApiRuntime
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._POOL = object()

            class FakeApiRuntime:
                @classmethod
                def from_config(cls, _config):
                    return cls()

                def ask(self, *_args, **_kwargs):
                    return SimpleNamespace(
                        reply="hello",
                        model="fake-model",
                        usage={},
                        timings={},
                        tool_calls=[],
                        recall_fragments=[],
                        dispatched=[],
                        transcript_messages=[],
                    )

            api_module.ApiRuntime = FakeApiRuntime

            dashboard_server._run_api_favilla_chat(
                text="hi",
                channel="chat",
                record_turn=False,
                turn_id="turn_api_trace",
                request_id="req_api_trace",
            )
            trace = dashboard_server._api_debug_trace({"turn_id": "turn_api_trace", "limit": "10"})

        api_module.ApiRuntime = original_api_runtime
        dashboard_server._CONFIG = original_config
        dashboard_server._POOL = original_pool

        phases = [row["phase"] for row in trace["rows"]]
        self.assertEqual(phases, ["dashboard.prompt", "dashboard.runtime", "dashboard.marker"])

    def test_cc_runtime_wrapper_writes_phase_trace_rows(self) -> None:
        from types import SimpleNamespace

        original_config = dashboard_server._CONFIG
        original_pool = dashboard_server._POOL
        original_record_turn = dashboard_server._record_cc_app_turn
        original_debug_context = dashboard_server._record_debug_context
        original_save_session = dashboard_server._save_app_active_session
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._POOL = object()
            dashboard_server._record_cc_app_turn = lambda *args, **kwargs: None
            dashboard_server._record_debug_context = lambda *args, **kwargs: None
            dashboard_server._save_app_active_session = lambda _session_id: None
            stdout = json.dumps({
                "type": "result",
                "result": "hello",
                "session_id": "sess_cc",
                "model": "claude-fake",
                "duration_ms": 12,
            }) + "\n"

            with patch.dict(os.environ, {"FIAM_CC_WARM_DISABLED": "1"}), patch("subprocess.run", return_value=SimpleNamespace(stdout=stdout, stderr="", returncode=0)):
                dashboard_server._run_cc_favilla_chat(
                    text="hi",
                    channel="chat",
                    turn_id="turn_cc_trace",
                    request_id="req_cc_trace",
                )
            trace = dashboard_server._api_debug_trace({"turn_id": "turn_cc_trace", "limit": "10"})

        dashboard_server._CONFIG = original_config
        dashboard_server._POOL = original_pool
        dashboard_server._record_cc_app_turn = original_record_turn
        dashboard_server._record_debug_context = original_debug_context
        dashboard_server._save_app_active_session = original_save_session

        phases = [row["phase"] for row in trace["rows"]]
        self.assertEqual(phases, ["dashboard.prompt", "dashboard.runtime", "dashboard.marker"])

    def test_cc_nonstream_uses_warm_runner_by_default(self) -> None:
        from types import SimpleNamespace

        original_config = dashboard_server._CONFIG
        original_pool = dashboard_server._POOL
        original_record_turn = dashboard_server._record_cc_app_turn
        original_debug_context = dashboard_server._record_debug_context
        original_save_session = dashboard_server._save_app_active_session
        original_warm_turn = dashboard_server._run_cc_warm_turn_result_locked
        captured: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._POOL = object()
            dashboard_server._record_cc_app_turn = lambda *args, **kwargs: None
            dashboard_server._record_debug_context = lambda *args, **kwargs: None
            dashboard_server._save_app_active_session = lambda _session_id: None

            def fake_warm_turn(user_prompt: str, system_context: str):
                captured["user_prompt"] = user_prompt
                captured["system_context"] = system_context
                stdout = json.dumps({
                    "type": "result",
                    "result": "hello",
                    "session_id": "sess_warm_nonstream",
                    "model": "claude-fake",
                    "duration_ms": 8,
                }) + "\n"
                return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

            dashboard_server._run_cc_warm_turn_result_locked = fake_warm_turn
            with patch("subprocess.run") as cold_run:
                result = dashboard_server._run_cc_favilla_chat(
                    text="hi",
                    channel="chat",
                    turn_id="turn_cc_warm_nonstream",
                    request_id="req_cc_warm_nonstream",
                )

        dashboard_server._CONFIG = original_config
        dashboard_server._POOL = original_pool
        dashboard_server._record_cc_app_turn = original_record_turn
        dashboard_server._record_debug_context = original_debug_context
        dashboard_server._save_app_active_session = original_save_session
        dashboard_server._run_cc_warm_turn_result_locked = original_warm_turn

        self.assertEqual(result["session_id"], "sess_warm_nonstream")
        self.assertFalse(cold_run.called)
        self.assertNotIn("[server_time]", captured["user_prompt"])
        self.assertNotIn("[server_time]", captured["system_context"])

    def test_cc_studio_uses_warm_runner_by_default(self) -> None:
        from types import SimpleNamespace

        original_config = dashboard_server._CONFIG
        original_warm_turn = dashboard_server._run_cc_warm_turn_result_locked
        captured: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config

            def fake_warm_turn(user_prompt: str, system_context: str):
                captured["user_prompt"] = user_prompt
                captured["system_context"] = system_context
                edit_payload = json.dumps({"summary": "append text", "edits": [{"op": "append", "text": "x"}]})
                stdout = json.dumps({
                    "type": "result",
                    "result": edit_payload,
                    "session_id": "sess_warm_studio",
                    "model": "claude-fake",
                }) + "\n"
                return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

            dashboard_server._run_cc_warm_turn_result_locked = fake_warm_turn
            with patch("subprocess.run") as cold_run:
                result = dashboard_server._run_cc_studio_edit("[studio_edit_contract]\nReturn only JSON.")

        dashboard_server._CONFIG = original_config
        dashboard_server._run_cc_warm_turn_result_locked = original_warm_turn

        self.assertEqual(result["session_id"], "sess_warm_studio")
        self.assertEqual(result["edits"][0]["op"], "append")
        self.assertFalse(cold_run.called)
        self.assertNotIn("[server_time]", captured["user_prompt"])
        self.assertNotIn("[server_time]", captured["system_context"])

    def test_stroll_send_injects_context_and_keeps_source_history_separate(self) -> None:
        original_config = dashboard_server._CONFIG
        original_api = dashboard_server._run_api_favilla_chat
        captured: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")

            def fake_api(**kwargs):
                captured["text"] = kwargs["text"]
                captured["channel"] = kwargs["channel"]
                return {"ok": True, "runtime": "api", "reply": "I can see the corner"}

            dashboard_server._run_api_favilla_chat = fake_api
            result = dashboard_server._favilla_chat_send({
                "text": "where am I",
                "runtime": "api",
                "channel": "stroll",
                "stroll_context": {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "road"},
            })
            stroll_history = dashboard_server._favilla_transcript_load("stroll")["messages"]
            chat_history = dashboard_server._favilla_transcript_load("chat")["messages"]

        dashboard_server._CONFIG = original_config
        dashboard_server._run_api_favilla_chat = original_api
        self.assertTrue(result["ok"])
        self.assertEqual(captured["channel"], "stroll")
        self.assertIn("[stroll_context]", captured["text"])
        self.assertIn("where am I", captured["text"])
        self.assertNotIn("[stroll_context]", stroll_history[0]["text"])
        self.assertEqual(stroll_history[0]["role"], "user")
        self.assertEqual(stroll_history[0]["text"], "where am I")
        self.assertEqual(stroll_history[1]["text"], "I can see the corner")
        self.assertEqual(chat_history, [])

    def test_stroll_send_applies_hidden_ai_map_marker(self) -> None:
        original_config = dashboard_server._CONFIG
        original_api = dashboard_server._run_api_favilla_chat
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")

            def fake_api(**_kwargs):
                return {"ok": True, "runtime": "api", "reply": 'marked it <stroll_record kind="marker" text="quiet corner" />'}

            dashboard_server._run_api_favilla_chat = fake_api
            result = dashboard_server._favilla_chat_send({
                "text": "mark this place",
                "runtime": "api",
                "channel": "stroll",
                "stroll_context": {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "green"},
            })
            history = dashboard_server._favilla_transcript_load("stroll")["messages"]
            nearby = dashboard_server._favilla_stroll_nearby({"lng": 121.5, "lat": 31.2})

        dashboard_server._CONFIG = original_config
        dashboard_server._run_api_favilla_chat = original_api
        self.assertEqual(result["reply"], "marked it")
        self.assertEqual(len(result["stroll_records"]), 1)
        self.assertEqual(result["stroll_records"][0]["text"], "quiet corner")
        self.assertEqual(result["stroll_records"][0]["placeKind"], "green")
        self.assertNotIn("stroll_record", history[1]["text"])
        self.assertEqual(nearby["records"][0]["text"], "quiet corner")

    def test_stroll_send_queues_hidden_client_actions(self) -> None:
        original_config = dashboard_server._CONFIG
        original_api = dashboard_server._run_api_favilla_chat
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")

            def fake_api(**_kwargs):
                return {"ok": True, "runtime": "api", "reply": 'checking <stroll_action type="view_camera" reason="look first" /><stroll_action type="capture_photo" reason="confirm sign" /><stroll_action type="set_limen_screen" text="wait" emoji="spark" />'}

            dashboard_server._run_api_favilla_chat = fake_api
            result = dashboard_server._favilla_chat_send({
                "text": "look",
                "runtime": "api",
                "channel": "stroll",
                "stroll_context": {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "road"},
            })
            history = dashboard_server._favilla_transcript_load("stroll")["messages"]

        dashboard_server._CONFIG = original_config
        dashboard_server._run_api_favilla_chat = original_api
        self.assertEqual(result["reply"], "checking")
        self.assertEqual([action["type"] for action in result["stroll_actions"]], ["view_camera", "capture_photo", "set_limen_screen"])
        self.assertNotIn("stroll_action", history[1]["text"])

    def test_studio_edit_parser_accepts_command_json(self) -> None:
        parsed = dashboard_server._parse_studio_edit_response(
            '{"summary":"changed AAA","edits":[{"op":"replace","target":"AAA","text":"AABA"},{"op":"append","text":"<p data-author=\\"AI\\">tail</p>"}]}'
        )

        self.assertEqual(parsed["summary"], "changed AAA")
        self.assertEqual([edit["op"] for edit in parsed["edits"]], ["replace", "append"])
        self.assertEqual(parsed["edits"][0]["target"], "AAA")

    def test_studio_edit_records_history_but_not_flow(self) -> None:
        original_config = dashboard_server._CONFIG
        original_model = dashboard_server._run_studio_edit_model
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")

            def fake_model(_prompt: str, runtime: str):
                return {"summary": "replace one token", "author": "AI", "edits": [{"op": "replace", "target": "AAA", "text": "AABA"}], "runtime": runtime}

            dashboard_server._run_studio_edit_model = fake_model
            result = dashboard_server._favilla_studio_edit({"instruction": "change AAA", "content": "<p>AAA</p>", "runtime": "api"})
            history = dashboard_server._favilla_transcript_load("studio")["messages"]
            flow_exists = dashboard_server._CONFIG.flow_path.exists()

        dashboard_server._CONFIG = original_config
        dashboard_server._run_studio_edit_model = original_model
        self.assertTrue(result["ok"])
        self.assertEqual(result["edits"][0]["op"], "replace")
        self.assertEqual([row["role"] for row in history], ["user", "ai"])
        self.assertFalse(flow_exists)

    def test_dashboard_includes_studio_and_location_buckets(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=root / "code")
            from fiam_lib.stroll_store import add_spatial_record

            add_spatial_record(dashboard_server._CONFIG, {"kind": "marker", "origin": "ai", "lng": 121.5, "lat": 31.2, "text": "quiet corner", "emoji": "✨", "placeKind": "green"})
            dashboard_server._favilla_studio_save({"state": {
                "files": [],
                "activeFileId": "file2",
                "activeNoteContent": "<p>AAA</p>",
                "timeline": [
                    {"id": "u1", "title": "asked AI to edit", "type": "user", "iconName": "Edit3", "at": 1_700_000_000_000, "units": 4, "location": {"lng": 121.5, "lat": 31.2, "placeKind": "studio"}},
                    {"id": "a1", "title": "applied AI edit", "type": "ai", "iconName": "Sparkles", "at": 1_700_000_010_000, "units": 6, "location": {"lng": 121.5, "lat": 31.2, "placeKind": "studio"}},
                ],
            }})
            summary = dashboard_server._favilla_dashboard()

        dashboard_server._CONFIG = original_config
        self.assertGreaterEqual(summary["studio"]["turns"], 2)
        self.assertGreater(summary["studio"]["ai_words"], 0)
        self.assertTrue(summary["locations"])
        self.assertIn("percent", summary["locations"][0])


if __name__ == "__main__":
    unittest.main()
