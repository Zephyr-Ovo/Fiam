from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


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
from fiam.runtime.turns import assistant_text_beats  # noqa: E402
from fiam.turn import MarkerInterpreter  # noqa: E402


class MarkerParsingTest(unittest.TestCase):
    def test_outbound_markers_parse_send_xml(self) -> None:
        markers = parse_outbound_markers(
            'hi\n<send to="email:Zephyr">hello</send>\n<send to="limen:screen">emoji:spark</send>'
        )

        self.assertEqual([(m.channel, m.recipient, m.body) for m in markers], [
            ("email", "Zephyr", "hello"),
            ("limen", "screen", "emoji:spark"),
        ])

    def test_outbound_markers_parse_object_attachments(self) -> None:
        first = "a" * 64
        second = "b" * 64
        markers = parse_outbound_markers(
            f'<send to="email:Zephyr" attach="obj:{first} obj:{second},obj:{first}"></send>'
        )

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].body, "")
        self.assertEqual(markers[0].attachments, (first, second))

    def test_marker_interpreter_resolves_short_object_tokens(self) -> None:
        full = "c" * 64
        parsed = MarkerInterpreter(object_resolver=lambda token: full if token == "obj:cccccccc" else "").interpret(
            '<send to="email:Zephyr" attach="obj:cccccccc">short</send>'
        )

        self.assertEqual(parsed.dispatch_requests[0].attachments[0].object_hash, full)

    def test_marker_interpreter_reports_bad_attachment_tokens(self) -> None:
        parsed = MarkerInterpreter().interpret(
            '<send to="email:Zephyr" attach="C:/tmp/photo.jpg obj:cccccccc">short</send>'
        )

        self.assertEqual(parsed.dispatch_requests[0].attachments, ())
        self.assertEqual(len(parsed.dispatch_requests[0].attachment_errors), 2)

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

    def test_state_markers_parse_and_last_one_wins(self) -> None:
        text = '<state value="mute" until="2026-05-05T22:00:00+08:00" reason="写代码" /><state value="notify" />'
        markers = parse_state_markers(text)

        self.assertEqual([m.state for m in markers], ["mute", "notify"])
        self.assertEqual(markers[-1].state, "notify")

    def test_sleep_marker_no_longer_in_state_markers(self) -> None:
        self.assertEqual(parse_state_markers('<sleep at="2026-05-05 23:00"/>'), [])

    def test_hold_reason_detects_body(self) -> None:
        self.assertEqual(parse_hold_reason("正文 <hold>重写一下</hold>"), "重写一下")
        self.assertEqual(parse_hold_reason("正文 <held>先不发</held>"), "先不发")
        self.assertEqual(parse_hold_reason("正文 <hold/>"), "")
        self.assertEqual(parse_hold_reason("没有 hold"), "")

    def test_hold_interpretation_drops_attempt_side_effects(self) -> None:
        parsed = MarkerInterpreter().interpret(
            '正文 <todo at="2099-05-05 20:00">later</todo> '
            '<send to="email:Zephyr">hi</send> <hold>重写一下</hold>'
        )

        self.assertEqual(parsed.visible_reply, "")
        self.assertEqual(parsed.hold_status, "reroll")
        self.assertEqual(parsed.hold_reason, "重写一下")
        self.assertIsNotNone(parsed.hold_request)
        self.assertEqual(parsed.todo_changes, ())
        self.assertEqual(parsed.dispatch_requests, ())

    def test_held_interpretation_marks_final_outcome(self) -> None:
        parsed = MarkerInterpreter().interpret("正文 <held>先不发</held>")

        self.assertEqual(parsed.visible_reply, "")
        self.assertEqual(parsed.hold_status, "held")
        self.assertEqual(parsed.hold_reason, "先不发")
        self.assertIsNotNone(parsed.hold_request)

    def test_strip_xml_markers_removes_control_text(self) -> None:
        text = '正文 <todo at="2026-05-05 20:00">写日报</todo> 后文 <state value="mute" reason="专注" />'

        self.assertEqual(strip_xml_markers(text, {"todo", "state"}), "正文  后文")

    def test_assistant_flow_beats_drop_held_reply(self) -> None:
        beats = assistant_text_beats(
            '外层 <hold/> 中间 <hold>重写</hold>',
            t=datetime.now(timezone.utc),
            channel="chat",
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
            channel="chat",
            runtime="claude",
        )

        think_beats = [b for b in beats if b.kind == "think"]
        dialogue = [b for b in beats if b.kind == "message"]
        self.assertEqual([b.content for b in think_beats], ["私下推理 A", "私下推理 B"])
        self.assertTrue(all(b.channel == "chat" for b in think_beats))
        self.assertEqual(len(dialogue), 1)
        self.assertNotIn("<cot", dialogue[0].content)
        self.assertIn("对外正文", dialogue[0].content)
        self.assertIn("收尾", dialogue[0].content)

    def test_strip_xml_markers_strips_cot(self) -> None:
        self.assertEqual(strip_xml_markers("a <cot>x</cot> b", {"cot"}), "a  b")

    def test_marker_interpreter_handles_wake_sleep_markers(self) -> None:
        parsed = MarkerInterpreter().interpret(
            '<wake at="2099-05-12 20:00"/> <sleep at="2099-05-12 23:00"/>'
        )

        self.assertEqual([item.kind for item in parsed.todo_changes], ["wake", "sleep"])


if __name__ == "__main__":
    unittest.main()
