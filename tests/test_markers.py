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
    parse_carry_over_markers,
    parse_final_markers,
    parse_hold_markers,
    parse_outbound_markers,
    parse_state_markers,
    parse_todo_markers,
    parse_wake_markers,
    strip_xml_markers,
)
from fiam.config import FiamConfig  # noqa: E402
from fiam.holds import load_hold_record  # noqa: E402
from fiam.runtime.turns import assistant_text_beats  # noqa: E402
from fiam_lib.app_markers import extract_hold_markers  # noqa: E402
from fiam_lib.todo import extract_scheduled_items, extract_state_tag  # noqa: E402


class MarkerParsingTest(unittest.TestCase):
    def test_outbound_markers_keep_existing_shape(self) -> None:
        markers = parse_outbound_markers("hi\n[→email:Zephyr] hello\n[→xiao:screen] emoji:spark")

        self.assertEqual([(m.channel, m.recipient, m.body) for m in markers], [
            ("email", "Zephyr", "hello"),
            ("xiao", "screen", "emoji:spark"),
        ])

    def test_outbound_markers_ignore_markdown_code_examples(self) -> None:
        text = (
            "Do not dispatch the inline example `[→email:Zephyr] hello`.\n"
            "```text\n[→xiao:screen] emoji:spark\n```\n"
            "[→email:Zephyr] real body with `[→email:Someone] literal`"
        )
        markers = parse_outbound_markers(text)

        self.assertEqual([(m.channel, m.recipient, m.body) for m in markers], [
            ("email", "Zephyr", "real body with `[→email:Someone] literal`"),
        ])

    def test_wake_marker_uses_body_for_time(self) -> None:
        markers = parse_wake_markers('<wake>2026-05-05 20:00</wake>', default_tz=timezone.utc)

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

    def test_extract_scheduled_items_handles_wake_and_todo(self) -> None:
        tags = extract_scheduled_items(
            '<wake>2026-05-06 09:00</wake>\n'
            '<todo at="2026-05-05 20:00">写日报</todo>'
        )

        self.assertEqual([tag["kind"] for tag in tags], ["wake", "todo"])
        self.assertEqual([tag["reason"] for tag in tags], ["", "写日报"])

    def test_state_markers_parse_and_last_one_wins(self) -> None:
        markers = parse_state_markers('<mute until="2026-05-05T22:00:00+08:00" reason="写代码" /><notify />')
        state = extract_state_tag('<mute until="2026-05-05T22:00:00+08:00" reason="写代码" /><notify />')

        self.assertEqual([m.state for m in markers], ["mute", "notify"])
        self.assertEqual(state, {"state": "notify", "reason": ""})

    def test_sleep_marker_defaults_to_open(self) -> None:
        state = extract_state_tag('<sleep reason="任务完成" />')

        self.assertEqual(state, {
            "state": "sleep",
            "reason": "任务完成",
            "until": "open",
            "sleeping_until": "open",
        })

    def test_carry_over_and_hold_markers(self) -> None:
        carry = parse_carry_over_markers('<carry_over to="api" reason="回聊天" />')
        hold = parse_hold_markers('<hold until="2026-05-05T21:00:00+08:00" reason="等下再发">草稿</hold>')

        self.assertEqual((carry[0].target, carry[0].reason), ("api", "回聊天"))
        self.assertEqual((hold[0].until, hold[0].reason, hold[0].draft), ("2026-05-05T21:00:00+08:00", "等下再发", "草稿"))

    def test_final_marker_parses_hold_delivery_text(self) -> None:
        final = parse_final_markers('私下笔记 <final>最终发给 Zephyr 的话</final>')

        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].text, "最终发给 Zephyr 的话")

    def test_held_reply_persists_draft_for_todo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            clean, queued, immediate = extract_hold_markers(
                '<hold until="2099-05-05T21:00:00+08:00" reason="等下再发">草稿</hold>',
                config,
                source="chat",
                runtime="api",
                user_text="原问题",
                attachments=[],
            )

            self.assertEqual(clean, "")
            self.assertFalse(immediate)
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["action"], "held_reply")
            self.assertTrue(queued[0]["hold_id"])
            record = load_hold_record(config, queued[0]["hold_id"])
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["draft"], "草稿")
            self.assertEqual(record["user_text"], "原问题")

    def test_strip_xml_markers_removes_control_text(self) -> None:
        text = '正文 <todo at="2026-05-05 20:00">写日报</todo> 后文 <mute reason="专注" />'

        self.assertEqual(strip_xml_markers(text, {"todo", "mute"}), "正文  后文")

    def test_assistant_flow_beats_do_not_keep_hold_or_final_body(self) -> None:
        beats = assistant_text_beats(
            '外层 <hold until="2099-05-05T21:00:00+08:00" reason="等">草稿</hold> <final>最终</final>',
            t=datetime.now(timezone.utc),
            scene="api",
            user_status="together",
            ai_status="online",
        )

        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0].text, "外层")


if __name__ == "__main__":
    unittest.main()