from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fiam.markers import (  # noqa: E402
    parse_cot_markers,
    parse_hold_reason,
    parse_outbound_markers,
    parse_state_markers,
    parse_todo_markers,
    parse_wake_markers,
    strip_xml_markers,
)
from fiam.config import FiamConfig  # noqa: E402
from fiam.runtime.turns import assistant_text_beats  # noqa: E402
from fiam_lib.app_markers import apply_hold  # noqa: E402
from fiam_lib.todo import extract_scheduled_items, extract_state_tag  # noqa: E402


class MarkerParsingTest(unittest.TestCase):
    def test_outbound_markers_parse_send_xml(self) -> None:
        markers = parse_outbound_markers(
            'hi\n<send to="email:Zephyr">hello</send>\n<send to="limen:screen">emoji:spark</send>'
        )

        self.assertEqual([(m.channel, m.recipient, m.body) for m in markers], [
            ("email", "Zephyr", "hello"),
            ("limen", "screen", "emoji:spark"),
        ])

    def test_outbound_markers_ignore_markdown_code_examples(self) -> None:
        text = (
            'Do not dispatch the inline example `<send to="email:Zephyr">hello</send>`.\n'
            '```text\n<send to="limen:screen">emoji:spark</send>\n```\n'
            '<send to="email:Zephyr">real body with `<send to="email:Someone">literal</send>`</send>'
        )
        markers = parse_outbound_markers(text)

        self.assertEqual([(m.channel, m.recipient, m.body) for m in markers], [
            ("email", "Zephyr", 'real body with `<send to="email:Someone">literal</send>`'),
        ])

    def test_wake_marker_uses_at_attr(self) -> None:
        markers = parse_wake_markers('<wake at="2026-05-05 20:00"/>', default_tz=timezone.utc)

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].at, "2026-05-05T20:00:00+00:00")

    def test_todo_marker_carries_at_attr_and_body_text(self) -> None:
        markers = parse_todo_markers(
            '<todo at="2026-05-05 20:00">写日报</todo>',
            default_tz=timezone.utc,
        )

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].at, "2026-05-05T20:00:00+00:00")
        self.assertEqual(markers[0].text, "写日报")

    def test_extract_scheduled_items_handles_wake_todo_and_sleep(self) -> None:
        tags = extract_scheduled_items(
            '<wake at="2026-05-06 09:00"/>\n'
            '<todo at="2026-05-05 20:00">写日报</todo>\n'
            '<sleep at="2026-05-05 23:00"/>'
        )

        self.assertEqual([tag["kind"] for tag in tags], ["wake", "todo", "sleep"])
        self.assertEqual([tag["reason"] for tag in tags], ["", "写日报", ""])

    def test_state_markers_parse_and_last_one_wins(self) -> None:
        text = '<state value="mute" until="2026-05-05T22:00:00+08:00" reason="写代码" /><state value="notify" />'
        markers = parse_state_markers(text)
        state = extract_state_tag(text)

        self.assertEqual([m.state for m in markers], ["mute", "notify"])
        self.assertEqual(state, {"state": "notify", "reason": ""})

    def test_sleep_marker_no_longer_in_state_markers(self) -> None:
        # sleep is now scheduling, not a state marker
        state = extract_state_tag('<sleep at="2026-05-05 23:00"/>')
        self.assertIsNone(state)

    def test_hold_reason_detects_body(self) -> None:
        self.assertEqual(parse_hold_reason("正文 <hold>重写一下</hold>"), "重写一下")
        self.assertEqual(parse_hold_reason("正文 <hold/>"), "")
        self.assertEqual(parse_hold_reason("没有 hold"), "")

    def test_apply_hold_text_drops_reply_and_queues_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code", hold_retry_seconds=15)
            cleaned, reason, todos = apply_hold(
                "正文 <hold>重写一下</hold> 位置",
                config,
                channel="chat",
                runtime="api",
            )

            self.assertEqual(reason, "重写一下")
            # cleaned still has the surrounding prose; caller drops the visible reply.
            self.assertIn("正文", cleaned)
            self.assertEqual(len(todos), 1)
            self.assertEqual(todos[0]["action"], "hold_retry")
            self.assertEqual(todos[0]["reason"], "重写一下")

    def test_apply_hold_no_marker_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            cleaned, reason, todos = apply_hold(
                "普通回复",
                config,
                channel="chat",
                runtime="api",
            )

            self.assertEqual(reason, "")
            self.assertEqual(todos, [])
            self.assertEqual(cleaned, "普通回复")

    def test_strip_xml_markers_removes_control_text(self) -> None:
        text = '正文 <todo at="2026-05-05 20:00">写日报</todo> 后文 <state value="mute" reason="专注" />'

        self.assertEqual(strip_xml_markers(text, {"todo", "state"}), "正文  后文")

    def test_assistant_flow_beats_drop_held_reply(self) -> None:
        beats = assistant_text_beats(
            '外层 <hold/> 中间 <hold>重写</hold>',
            t=datetime.now(timezone.utc),
            channel="api",
        )

        self.assertEqual(beats, [])

    def test_parse_cot_markers_extracts_bodies(self) -> None:
        text = "前 <cot>第一段思考</cot> 中 <cot>  第二段  </cot> 后 <cot></cot>"
        cots = parse_cot_markers(text)
        self.assertEqual(cots, ["第一段思考", "第二段"])

    def test_assistant_flow_beats_emit_think_from_cot(self) -> None:
        beats = assistant_text_beats(
            "对外正文 <cot>私下推理 A</cot> 继续 <cot>私下推理 B</cot> 收尾",
            t=datetime.now(timezone.utc),
            channel="favilla",
            runtime="claude",
        )

        think_beats = [b for b in beats if b.kind == "think"]
        dialogue = [b for b in beats if b.kind == "message"]
        self.assertEqual([b.content for b in think_beats], ["私下推理 A", "私下推理 B"])
        self.assertTrue(all(b.channel == "favilla" for b in think_beats))
        self.assertEqual(len(dialogue), 1)
        self.assertNotIn("<cot", dialogue[0].content)
        self.assertIn("对外正文", dialogue[0].content)
        self.assertIn("收尾", dialogue[0].content)

    def test_strip_xml_markers_strips_cot(self) -> None:
        self.assertEqual(strip_xml_markers("a <cot>x</cot> b", {"cot"}), "a  b")


if __name__ == "__main__":
    unittest.main()
