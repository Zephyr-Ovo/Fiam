from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.append(path)
if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from fiam.browser_bridge import build_browser_control_text, build_browser_runtime_text, extract_browser_actions, extract_browser_done, format_browser_snapshot, normalize_browser_snapshot
from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.store.beat import read_beats
from fiam.store.pool import Pool
from fiam.runtime import api as api_module

spec = importlib.util.spec_from_file_location("dashboard_server", SCRIPTS / "dashboard_server.py")
assert spec and spec.loader
dashboard_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_server)


class FakeEmbedder:
    def embed(self, text: str):
        import numpy as np

        vec = np.array([1.0, float(len(text) % 5 + 1), 0.5], dtype=np.float32)
        return vec / np.linalg.norm(vec)


class FakeBus:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    def publish_receive(self, source: str, payload: dict) -> bool:
        self.messages.append((source, payload))
        return True


SNAPSHOT = {
    "snapshot": {
        "url": "https://example.test/report",
        "title": "论文格式检测报告",
        "browser": "edge",
        "selection": "结论段落",
        "headings": ["检测报告", "格式问题"],
        "textBlocks": ["这里是正文检测结果。", "参考文献格式需要复核。"],
        "nodes": [
            {"id": "node_1", "role": "button", "name": "提交", "selector": "button.submit", "actions": ["click"], "viewport": "visible"},
            {"id": "node_2", "role": "textbox", "name": "论文标题", "selector": "#title", "actions": ["set_text"]},
        ],
        "media": {
            "imageCount": 3,
            "backgroundImageCount": 2,
            "canvasCount": 1,
            "videoCount": 1,
            "iframeCount": 0,
            "samples": [{"kind": "image", "label": "报告截图", "viewport": "visible"}],
        },
    }
}


class BrowserBridgeTest(unittest.TestCase):
    def test_snapshot_format_keeps_actionable_context(self) -> None:
        compact = normalize_browser_snapshot(SNAPSHOT)
        self.assertEqual(compact["url"], "https://example.test/report")
        self.assertEqual(len(compact["nodes"]), 2)
        self.assertEqual(compact["actionMap"]["node_1"]["selector"], "button.submit")

        text = format_browser_snapshot(SNAPSHOT)
        self.assertIn("[browser_snapshot]", text)
        self.assertIn("论文格式检测报告", text)
        self.assertIn("node_1 role=button", text)
        self.assertNotIn("selector=button.submit", text)
        self.assertIn("media_digest", text)
        self.assertIn("images=3 videos=1", text)
        self.assertIn("background_images=2", text)
        self.assertIn("visible_text", text)

    def test_runtime_text_includes_question_after_snapshot(self) -> None:
        text = build_browser_runtime_text("帮我看哪里要改", SNAPSHOT)
        self.assertIn("[browser_snapshot]", text)
        self.assertIn("[browser_action_protocol]", text)
        self.assertIn("[user_request]", text)
        self.assertTrue(text.rstrip().endswith("帮我看哪里要改"))

    def test_browser_control_prompt_allows_direct_operation(self) -> None:
        text = build_browser_control_text({**SNAPSHOT, "reason": "content_ready"})
        self.assertIn("[browser_control]", text)
        self.assertIn("operate directly", text)
        self.assertIn("Prefer taking exactly one low-risk", text)
        self.assertIn("<browser_action", text)

    def test_browser_control_prompt_includes_recent_actions(self) -> None:
        text = build_browser_control_text({
            **SNAPSHOT,
            "reason": "after_action",
            "controlTrail": [{"action": "click", "nodeId": "node_7", "name": "Your boards", "result": "ok"}],
        })
        self.assertIn("[recent_browser_actions]", text)
        self.assertIn("Your boards", text)

    def test_browser_action_marker_resolves_hidden_selector(self) -> None:
        cleaned, actions = extract_browser_actions("好的 <browser_action node=\"node_1\" action=\"click\" />", SNAPSHOT)
        self.assertEqual(cleaned, "好的")
        self.assertEqual(actions[0]["nodeId"], "node_1")
        self.assertEqual(actions[0]["selector"], "button.submit")
        self.assertEqual(actions[0]["action"], "click")

    def test_browser_done_marker_is_hidden_and_structured(self) -> None:
        cleaned, done = extract_browser_done("结束 <browser_done reason=\"no useful action\" />")
        self.assertEqual(cleaned, "结束")
        self.assertEqual(done, {"reason": "no useful action"})

    def test_dashboard_browser_tick_extracts_single_action(self) -> None:
        with patch.object(dashboard_server, "_recent_browser_action_trail", return_value=[]), \
             patch.object(dashboard_server, "_favilla_chat_send", return_value={"reply": "ok <browser_action node=\"node_1\" action=\"click\" />"}) as send:
            result = dashboard_server._browser_control_tick({**SNAPSHOT, "runtime": "api", "reason": "content_ready"})
        self.assertEqual(result["mode"], "autonomous")
        self.assertEqual(result["browser_actions"][0]["nodeId"], "node_1")
        self.assertFalse(send.call_args.args[0]["record_turn"])

    def test_dashboard_browser_tick_records_ai_decision_and_done(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            with patch.object(dashboard_server, "_favilla_chat_send", return_value={"reply": "停在这里 <browser_done reason=\"enough context\" />"}):
                result = dashboard_server._browser_control_tick({**SNAPSHOT, "runtime": "api", "reason": "content_ready"})
            beats = read_beats(config.flow_path)
        dashboard_server._CONFIG = original_config
        self.assertEqual(result["browser_done"], {"reason": "enough context"})
        self.assertEqual(result["browser_actions"], [])
        self.assertEqual(beats[0].scene, "ai@browser")
        self.assertIn("browser_control_done", beats[0].text)

    def test_dashboard_browser_tick_strips_invalid_action_marker_from_segments(self) -> None:
        with patch.object(dashboard_server, "_favilla_chat_send", return_value={
            "reply": "ok <browser_action node=\"missing\" action=\"click\" />",
            "segments": [{"type": "text", "text": "ok <browser_action node=\"missing\" action=\"click\" />"}],
        }):
            result = dashboard_server._browser_control_tick({**SNAPSHOT, "runtime": "api", "reason": "content_ready"})
        self.assertEqual(result["browser_actions"], [])
        self.assertEqual(result["segments"][0]["text"], "ok")

    def test_dashboard_browser_tick_saves_screenshot_attachment(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            with patch.object(dashboard_server, "_favilla_chat_send", return_value={"reply": "ok"}) as send:
                result = dashboard_server._browser_control_tick({
                    **SNAPSHOT,
                    "runtime": "api",
                    "reason": "content_ready",
                    "screenshot": {"dataUrl": "data:image/jpeg;base64,aGVsbG8=", "reason": "content_ready"},
                })
            attachments = send.call_args.args[0]["attachments"]
            self.assertTrue(Path(attachments[0]["path"]).exists())
        dashboard_server._CONFIG = original_config
        self.assertTrue(result["screenshot_attached"])
        self.assertTrue(result["screenshot_attempted"])
        self.assertEqual(attachments[0]["mime"], "image/jpeg")

    def test_dashboard_browser_tick_retries_without_screenshot_on_failure(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            with patch.object(dashboard_server, "_favilla_chat_send", side_effect=[RuntimeError("vision down"), {"reply": "ok"}]) as send:
                result = dashboard_server._browser_control_tick({
                    **SNAPSHOT,
                    "runtime": "api",
                    "reason": "content_ready",
                    "screenshot": {"dataUrl": "data:image/jpeg;base64,aGVsbG8=", "reason": "content_ready"},
                })
        dashboard_server._CONFIG = original_config
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[0]["attachments"], [])
        self.assertTrue(result["screenshot_attempted"])
        self.assertFalse(result["screenshot_attached"])
        self.assertIn("vision down", result["screenshot_fallback_error"])

    def test_dashboard_browser_action_result_records_flow(self) -> None:
        original_config = dashboard_server._CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            result = dashboard_server._append_browser_action_flow({
                "action": {"nodeId": "node_1", "action": "click", "name": "提交"},
                "result": {"ok": True, "label": "提交"},
                "snapshot": SNAPSHOT["snapshot"],
            })
            beats = read_beats(config.flow_path)

        dashboard_server._CONFIG = original_config
        self.assertTrue(result["ok"])
        self.assertEqual(beats[0].scene, "ai@action")
        self.assertEqual(beats[0].runtime, "browser")
        self.assertEqual(beats[1].scene, "user@browser")

    def test_pinterest_profile_keeps_common_controls_and_suppresses_pins(self) -> None:
        snapshot = {
            "snapshot": {
                "url": "https://www.pinterest.com/",
                "title": "Pinterest",
                "nodes": [
                    {"id": "node_1", "role": "searchbox", "name": "Search", "selector": "#search-input", "actions": ["set_text"]},
                    {"id": "node_2", "role": "link", "name": "a blue room pin page", "selector": ".pin-a", "actions": ["click"]},
                    {"id": "node_3", "role": "button", "name": "Create", "selector": "button.create", "actions": ["click"]},
                    {"id": "node_4", "role": "button", "name": "div > div.KnPdox.gEQpi5", "selector": ".overlay", "actions": ["click"]},
                ],
            }
        }
        compact = normalize_browser_snapshot(snapshot)
        labels = [node["name"] for node in compact["nodes"]]
        self.assertEqual(labels, ["Search", "Create"])
        self.assertEqual(compact["profile"]["id"], "pinterest")
        self.assertEqual(compact["profile"]["suppressed"][0]["group"], "anonymous_overlays")
        self.assertEqual(compact["actionMap"]["node_1"]["selector"], "#search-input")
        text = format_browser_snapshot(snapshot)
        self.assertIn("profile: pinterest", text)
        self.assertIn("suppressed 1 similar feed pin links", text)
        self.assertNotIn("pin page", text)

    def test_extension_profile_rules_can_keep_and_hide_controls(self) -> None:
        snapshot = {
            "snapshot": {
                "url": "https://example.local/app",
                "title": "Example App",
                "profileRules": {
                    "id": "user:example.local",
                    "hosts": ["example.local"],
                    "strictKeep": True,
                    "keep": [{"role": "button", "labelContains": "Primary", "alias": "Primary action"}],
                    "suppress": [{"selectorContains": ".decorative", "group": "manual_hidden"}],
                    "groups": {"manual_hidden": "manually hidden elements"},
                },
                "nodes": [
                    {"id": "node_1", "role": "button", "name": "Primary", "selector": "button.primary", "actions": ["click"]},
                    {"id": "node_2", "role": "button", "name": "", "selector": "div.decorative > button", "actions": ["click"]},
                    {"id": "node_3", "role": "button", "name": "Secondary", "selector": "button.secondary", "actions": ["click"]},
                ],
            }
        }
        compact = normalize_browser_snapshot(snapshot)
        self.assertEqual([node["name"] for node in compact["nodes"]], ["Primary action"])
        self.assertEqual(compact["profile"]["id"], "user:example.local")
        self.assertTrue(compact["profile"]["strictKeep"])
        self.assertEqual(compact["profile"]["suppressed"][0]["label"], "non-selected controls")
        self.assertEqual(compact["profile"]["suppressed"][0]["count"], 2)
        self.assertEqual(compact["actionMap"]["node_1"]["selector"], "button.primary")

    def test_strict_keep_falls_back_when_current_state_has_no_kept_nodes(self) -> None:
        snapshot = {
            "snapshot": {
                "url": "https://example.local/app",
                "title": "Example App",
                "profileRules": {
                    "id": "user:example.local",
                    "hosts": ["example.local"],
                    "strictKeep": True,
                    "strictKeepContextFallback": True,
                    "keep": [{"role": "button", "labelContains": "Missing"}],
                    "suppress": [{"selectorContains": ".decorative", "group": "manual_hidden"}],
                },
                "nodes": [
                    {"id": "node_1", "role": "button", "name": "Secondary", "selector": "button.secondary", "actions": ["click"]},
                    {"id": "node_2", "role": "button", "name": "Decorative", "selector": "div.decorative > button", "actions": ["click"]},
                ],
            }
        }
        compact = normalize_browser_snapshot(snapshot)
        self.assertEqual([node["name"] for node in compact["nodes"]], ["Secondary"])
        self.assertEqual(compact["actionMap"]["node_1"]["selector"], "button.secondary")

    def test_strict_keep_contextual_fallback_adds_page_content_controls(self) -> None:
        snapshot = {
            "snapshot": {
                "url": "https://example.local/profile",
                "title": "Example Profile",
                "profileRules": {
                    "id": "user:example.local",
                    "hosts": ["example.local"],
                    "strictKeep": True,
                    "strictKeepContextFallback": True,
                    "keep": [{"role": "link", "labelContains": "Home"}],
                    "suppress": [{"labelContains": "pin page", "group": "feed_pins"}],
                },
                "nodes": [
                    {"id": "node_1", "role": "link", "name": "Home", "selector": "a.home", "actions": ["click"]},
                    {"id": "node_2", "role": "link", "name": "Art inspiration", "selector": "a.board-art", "actions": ["click"]},
                    {"id": "node_3", "role": "link", "name": "Cat pin page", "selector": "a.pin", "actions": ["click"]},
                ],
            }
        }
        compact = normalize_browser_snapshot(snapshot)
        self.assertEqual([node["name"] for node in compact["nodes"]], ["Home", "Art inspiration"])
        self.assertEqual(compact["actionMap"]["node_2"]["selector"], "a.board-art")

    def test_builtin_contextual_fallback_survives_user_strict_keep_override(self) -> None:
        snapshot = {
            "snapshot": {
                "url": "https://www.pinterest.com/irisz2340/",
                "title": "Pinterest",
                "profileRules": {
                    "id": "user:pinterest.com",
                    "hosts": ["pinterest.com"],
                    "strictKeep": True,
                    "keep": [{"role": "link", "labelContains": "Home"}],
                },
                "nodes": [
                    {"id": "node_1", "role": "link", "name": "Home", "selector": "a.home", "actions": ["click"]},
                    {"id": "node_2", "role": "link", "name": "Art inspiration", "selector": "a.board-art", "actions": ["click"]},
                ],
            }
        }
        compact = normalize_browser_snapshot(snapshot)
        self.assertTrue(compact["profile"]["strictKeepContextFallback"])
        self.assertEqual([node["name"] for node in compact["nodes"]], ["Home", "Art inspiration"])

    def test_dashboard_suppresses_repeated_recent_browser_action(self) -> None:
        actions = [{"action": "click", "nodeId": "node_6", "name": "Explore"}]
        trail = [{"action": "click", "nodeId": "node_6", "name": "Explore", "result": "ok"}]
        self.assertEqual(dashboard_server._suppress_repeated_browser_actions(actions, trail), [])

    def test_browser_scene_normalizes_to_user_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code", embedding_dim=3, memory_mode="manual")
            config.ensure_dirs()
            conductor = Conductor(
                pool=Pool(config.pool_dir, dim=3),
                embedder=FakeEmbedder(),
                config=config,
                flow_path=config.flow_path,
                memory_mode="manual",
            )
            conductor.receive("浏览器页面更新", "browser")
            beats = read_beats(config.flow_path)
            self.assertEqual(beats[0].scene, "user@browser")

    def test_dashboard_browser_snapshot_publishes_receive_source(self) -> None:
        original_config = dashboard_server._CONFIG
        original_get_bus = dashboard_server._get_bus
        fake_bus = FakeBus()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard_server._CONFIG = FiamConfig(home_path=root / "home", code_path=ROOT)
            dashboard_server._get_bus = lambda: fake_bus
            result = dashboard_server._browser_snapshot(SNAPSHOT)

        dashboard_server._CONFIG = original_config
        dashboard_server._get_bus = original_get_bus
        self.assertTrue(result["ok"])
        self.assertEqual(fake_bus.messages[0][0], "browser")
        self.assertEqual(fake_bus.messages[0][1]["source"], "browser")
        self.assertIn("[browser_snapshot]", fake_bus.messages[0][1]["text"])

    def test_dashboard_browser_snapshot_records_when_bus_unavailable(self) -> None:
        original_config = dashboard_server._CONFIG
        original_get_bus = dashboard_server._get_bus
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._get_bus = lambda: None
            result = dashboard_server._browser_snapshot(SNAPSHOT)
            beats = read_beats(config.flow_path)

        dashboard_server._CONFIG = original_config
        dashboard_server._get_bus = original_get_bus
        self.assertTrue(result["ok"])
        self.assertFalse(result["queued"])
        self.assertTrue(result["recorded"])
        self.assertEqual(beats[0].scene, "user@browser")

    def test_dashboard_browser_ask_uses_selected_runtime_and_source(self) -> None:
        original_chat = dashboard_server._favilla_chat_send
        captured: dict = {}

        def fake_chat(payload: dict) -> dict:
            captured.update(payload)
            return {"ok": True, "runtime": payload["runtime"], "reply": "ok"}

        dashboard_server._favilla_chat_send = fake_chat
        try:
            result = dashboard_server._browser_ask({**SNAPSHOT, "question": "总结", "runtime": "cc"})
        finally:
            dashboard_server._favilla_chat_send = original_chat

        self.assertTrue(result["ok"])
        self.assertEqual(captured["source"], "browser")
        self.assertEqual(captured["runtime"], "cc")
        self.assertIn("[browser_snapshot]", captured["text"])

    def test_browser_api_ask_light_record_skips_local_embedder(self) -> None:
        original_config = dashboard_server._CONFIG
        original_pool = dashboard_server._POOL
        captured: dict = {}

        class FakeRuntime:
            def ask(self, text: str, **kwargs):
                captured["text"] = text
                captured.update(kwargs)
                return SimpleNamespace(
                    reply="页面里有一个提交按钮。",
                    model="fake-model",
                    usage={},
                    recall_fragments=0,
                    dispatched=0,
                    tool_calls=[],
                )

        def fake_from_config(cls, config, **kwargs):
            captured["conductor"] = kwargs.get("conductor")
            captured["recall_refresher"] = kwargs.get("recall_refresher")
            return FakeRuntime()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code", embedding_backend="local")
            config.ensure_dirs()
            dashboard_server._CONFIG = config
            dashboard_server._POOL = Pool(config.pool_dir, dim=config.embedding_dim)
            dashboard_server._append_transcript("browser", {"role": "user", "raw_text": "OLD_BROWSER_CONTEXT", "runtime": "api"})
            with patch.object(dashboard_server, "_get_embedder", side_effect=AssertionError("embedder should not load")):
                with patch.object(api_module.ApiRuntime, "from_config", classmethod(fake_from_config)):
                    result = dashboard_server._run_api_favilla_chat(text="看这个页面", source="browser")
            beats = read_beats(config.flow_path)

        dashboard_server._CONFIG = original_config
        dashboard_server._POOL = original_pool
        self.assertTrue(result["ok"])
        self.assertFalse(captured["record"])
        self.assertNotIn("OLD_BROWSER_CONTEXT", captured["extra_context"])
        self.assertIsNone(captured["conductor"])
        self.assertIsNone(captured["recall_refresher"])
        self.assertEqual([beat.scene for beat in beats], ["user@browser", "ai@browser"])


if __name__ == "__main__":
    unittest.main()