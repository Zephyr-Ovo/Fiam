"""Tests for fiam.runtime.tools (sandbox + editor executors)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fiam.runtime.tools import execute_tool_call


def _cfg(home: Path) -> SimpleNamespace:
    return SimpleNamespace(home_path=home)


class ToolsTest(unittest.TestCase):
    def test_read_list_create_replace_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = _cfg(home)
            (home / "self").mkdir()
            (home / "self" / "identity.md").write_text("hello world\nsecond line\n", encoding="utf-8")

            self.assertIn("hello", execute_tool_call(cfg, "read_file", json.dumps({"path": "self/identity.md"})))

            entries = json.loads(execute_tool_call(cfg, "list_dir", json.dumps({"path": "self"})))
            self.assertEqual([e["name"] for e in entries], ["identity.md"])

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "str_replace", json.dumps({
                    "path": "self/identity.md", "old": "hello world", "new": "hi there",
                })),
            )
            self.assertTrue((home / "self" / "identity.md").read_text(encoding="utf-8").startswith("hi there"))

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "insert", json.dumps({
                    "path": "self/identity.md", "line": 0, "content": "# top",
                })),
            )
            self.assertTrue((home / "self" / "identity.md").read_text(encoding="utf-8").startswith("# top\n"))

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "create_file", json.dumps({
                    "path": "self/lessons.md", "content": "first lesson",
                })),
            )
            self.assertTrue((home / "self" / "lessons.md").exists())

    def test_str_replace_unique_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "f.md").write_text("xx xx", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "str_replace", json.dumps({
                "path": "f.md", "old": "xx", "new": "y",
            }))
            self.assertTrue(out.startswith("error: old string matches 2 times"))

    def test_create_file_refuses_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "f.md").write_text("x", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "create_file", json.dumps({
                "path": "f.md", "content": "y",
            }))
            self.assertTrue(out.startswith("error: file already exists"))

    def test_sandbox_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (Path(tmp) / "secret.txt").write_text("nope", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "read_file", json.dumps({"path": "../secret.txt"}))
            self.assertTrue(out.startswith("error: path escapes home"))

    def test_unknown_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = execute_tool_call(_cfg(Path(tmp)), "rm_rf", "{}")
            self.assertEqual(out, "error: unknown tool 'rm_rf'")

    def test_grep_schedule_and_state_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = _cfg(home)
            (home / "uploads" / "2026-05-04").mkdir(parents=True)
            (home / "uploads" / "2026-05-04" / "big.txt").write_text(
                "alpha\nneedle detail\nomega\n",
                encoding="utf-8",
            )

            hits = json.loads(execute_tool_call(cfg, "grep_files", json.dumps({
                "path": "uploads",
                "query": "needle",
            })))
            self.assertEqual(hits[0]["line"], 2)

            wake_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            out = json.loads(execute_tool_call(cfg, "schedule_wake", json.dumps({
                "wake_at": wake_at,
                "type": "notify",
                "reason": "test wake",
            })))
            self.assertTrue(out["ok"])
            self.assertTrue((home / "self" / "schedule.jsonl").exists())

            state = json.loads(execute_tool_call(cfg, "set_ai_state", json.dumps({
                "state": "busy",
                "reason": "testing",
            })))
            self.assertEqual(state["state"], "busy")
            self.assertTrue((home / "self" / "ai_state.json").exists())


if __name__ == "__main__":
    unittest.main()
