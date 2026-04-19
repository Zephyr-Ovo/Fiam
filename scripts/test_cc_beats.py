"""Test CC JSONL → Beat parsing."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiam.adapter.claude_code import ClaudeCodeAdapter


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "wb") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False).encode("utf-8") + b"\n")


def test_basic_beats():
    """User + assistant → cc beats."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"role": "user", "content": "你好"},
             "timestamp": "2026-04-19T10:00:00Z"},
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_1",
             "content": [{"type": "text", "text": "你好！有什么可以帮你的？"}]},
             "timestamp": "2026-04-19T10:00:05Z"},
        ])
        beats, offset = adapter.parse_beats(p)
        assert len(beats) == 2, f"Expected 2 beats, got {len(beats)}"
        assert beats[0].source == "cc"
        assert beats[0].text == "你好"
        assert beats[1].source == "cc"
        assert "帮你" in beats[1].text
        print("  basic beats OK")


def test_tool_use_beats():
    """tool_use blocks → action beats."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_2",
             "content": [
                 {"type": "text", "text": "让我看看这个文件"},
                 {"type": "tool_use", "id": "t1", "name": "Read",
                  "input": {"path": "/home/fiet/config.py"}},
             ]},
             "timestamp": "2026-04-19T10:01:00Z"},
        ])
        beats, _ = adapter.parse_beats(p)
        sources = [b.source for b in beats]
        assert "action" in sources, f"Expected action beat, got {sources}"
        action = [b for b in beats if b.source == "action"][0]
        assert "Read" in action.text
        assert "config.py" in action.text
        # The text part should generate a cc beat
        cc_beats = [b for b in beats if b.source == "cc"]
        assert len(cc_beats) == 1
        assert "看看" in cc_beats[0].text
        print("  tool_use beats OK")


def test_routing_markers():
    """[→tg:Name] and [→email:Name] → routed beats."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_3",
             "content": [{"type": "text",
              "text": "好的我来回复。[→tg:Zephyr] 收到！我等下看看那个bug"}]},
             "timestamp": "2026-04-19T10:02:00Z"},
        ])
        beats, _ = adapter.parse_beats(p)
        sources = [b.source for b in beats]
        assert "tg" in sources, f"Expected tg beat, got {sources}"
        tg_beat = [b for b in beats if b.source == "tg"][0]
        assert "Zephyr" in tg_beat.text
        assert "bug" in tg_beat.text
        # "好的我来回复" should still be cc beat
        cc_beats = [b for b in beats if b.source == "cc"]
        assert len(cc_beats) == 1
        assert "回复" in cc_beats[0].text
        print("  routing markers OK")


def test_dedup_assistant():
    """Same assistant msg_id → dedup (keep latest text)."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_4",
             "content": [{"type": "text", "text": "partial..."}]},
             "timestamp": "2026-04-19T10:03:00Z"},
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_4",
             "content": [{"type": "text", "text": "完整的回复内容"}]},
             "timestamp": "2026-04-19T10:03:01Z"},
        ])
        beats, _ = adapter.parse_beats(p)
        cc_beats = [b for b in beats if b.source == "cc"]
        assert len(cc_beats) == 1
        assert "完整" in cc_beats[0].text
        print("  dedup assistant OK")


def test_skip_attachments():
    """Attachments (recall/inbox) → skipped in beat mode."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "user", "uuid": "u1",
             "message": {"role": "user", "content": "hello"},
             "timestamp": "2026-04-19T10:04:00Z"},
            {"type": "attachment", "parentUuid": "u1",
             "attachment": {"type": "hook_additional_context",
              "content": ["[recall]\nold memory stuff\n[inbox]\nnew msg"]}},
        ])
        beats, _ = adapter.parse_beats(p)
        assert len(beats) == 1
        assert beats[0].source == "cc"
        assert beats[0].text == "hello"
        print("  skip attachments OK")


def test_skip_system_messages():
    """System-injected user messages → skipped."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"role": "user",
             "content": "<system-reminder>keep going</system-reminder>"},
             "timestamp": "2026-04-19T10:05:00Z"},
            {"type": "user", "message": {"role": "user",
             "content": "真正的用户消息"},
             "timestamp": "2026-04-19T10:05:01Z"},
        ])
        beats, _ = adapter.parse_beats(p)
        assert len(beats) == 1
        assert beats[0].text == "真正的用户消息"
        print("  skip system messages OK")


def test_incremental_beats():
    """Incremental parsing with byte offset."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"role": "user", "content": "first"},
             "timestamp": "2026-04-19T10:00:00Z"},
        ])
        beats1, offset1 = adapter.parse_beats(p)
        assert len(beats1) == 1

        # Append more
        with open(p, "ab") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user",
                    "content": "second"}, "timestamp": "2026-04-19T10:01:00Z"},
                    ensure_ascii=False).encode("utf-8") + b"\n")

        beats2, offset2 = adapter.parse_beats(p, offset1)
        assert len(beats2) == 1
        assert beats2[0].text == "second"
        print("  incremental beats OK")


def test_bash_tool():
    """Bash tool_use → action beat with truncated command."""
    adapter = ClaudeCodeAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        long_cmd = "cat " + "a" * 200
        _write_jsonl(p, [
            {"type": "assistant", "message": {"role": "assistant", "id": "msg_5",
             "content": [
                 {"type": "tool_use", "id": "t2", "name": "Bash",
                  "input": {"command": long_cmd}},
             ]},
             "timestamp": "2026-04-19T10:06:00Z"},
        ])
        beats, _ = adapter.parse_beats(p)
        action_beats = [b for b in beats if b.source == "action"]
        assert len(action_beats) == 1
        assert len(action_beats[0].text) < 100  # truncated
        assert "..." in action_beats[0].text
        print("  bash tool truncation OK")


if __name__ == "__main__":
    print("Testing CC JSONL → Beat parsing...")
    test_basic_beats()
    test_tool_use_beats()
    test_routing_markers()
    test_dedup_assistant()
    test_skip_attachments()
    test_skip_system_messages()
    test_incremental_beats()
    test_bash_tool()
    print("\nAll tests passed.")
