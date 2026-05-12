from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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
                yield {"event": "text", "data": {"index": 0, "text": "hello"}}
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
                if ev.get("event") == "done":
                    order.append("done")
                events.append(ev)
            history = dashboard_server._favilla_transcript_load("chat")["messages"]

        dashboard_server._CONFIG = original_config
        dashboard_server._iter_cc_favilla_chat_events = original_iter
        dashboard_server._persist_favilla_ai_transcript = original_persist
        dashboard_server._check_and_run_session_rollover = original_rollover

        self.assertEqual(order, ["persist", "done"])
        self.assertEqual(history[-1]["role"], "ai")
        self.assertEqual(history[-1]["text"], "hello")
        self.assertEqual(events[-1]["data"]["transcript_id"], history[-1]["id"])
        self.assertEqual(history[-1]["meta"]["trace"]["request_id"], "test-stream")

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
