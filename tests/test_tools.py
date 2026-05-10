"""Tests for fiam.runtime.tools (sandbox + editor executors)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fiam.runtime.tools import execute_tool_call


class _Cfg(SimpleNamespace):
    timezone: str = "Asia/Shanghai"

    def project_tz(self):
        return ZoneInfo(self.timezone)

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_local(self) -> datetime:
        return self.now_utc().astimezone(self.project_tz())

    def ensure_timezone(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.project_tz())
        return dt


def _cfg(home: Path) -> SimpleNamespace:
    return _Cfg(home_path=home)


class ToolsTest(unittest.TestCase):
    def test_read_list_create_replace_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = _cfg(home)
            (home / "self").mkdir()
            (home / "self" / "identity.md").write_text("hello world\nsecond line\n", encoding="utf-8")

            self.assertIn("hello", execute_tool_call(cfg, "Read", json.dumps({"path": "self/identity.md"})))

            entries = json.loads(execute_tool_call(cfg, "Glob", json.dumps({"pattern": "self/*"})))
            self.assertIn("self/identity.md", entries)

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "Edit", json.dumps({
                    "path": "self/identity.md", "old_string": "hello world", "new_string": "hi there",
                })),
            )
            self.assertTrue((home / "self" / "identity.md").read_text(encoding="utf-8").startswith("hi there"))

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "Edit", json.dumps({
                    "path": "self/identity.md", "old_string": "", "new_string": "# top\n",
                })),
            )
            self.assertTrue((home / "self" / "identity.md").read_text(encoding="utf-8").startswith("# top\n"))

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "Write", json.dumps({
                    "path": "self/lessons.md", "content": "first lesson",
                })),
            )
            self.assertTrue((home / "self" / "lessons.md").exists())

    def test_str_replace_unique_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "f.md").write_text("xx xx", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "Edit", json.dumps({
                "path": "f.md", "old_string": "xx", "new_string": "y",
            }))
            self.assertTrue(out.startswith("error: old_string matches 2 times"))

    def test_create_file_refuses_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "f.md").write_text("x", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "Write", json.dumps({
                "path": "f.md", "content": "y",
            }))
            self.assertTrue(out.startswith("error: file already exists"))

    def test_sandbox_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (Path(tmp) / "secret.txt").write_text("nope", encoding="utf-8")
            out = execute_tool_call(_cfg(home), "Read", json.dumps({"path": "../secret.txt"}))
            self.assertTrue(out.startswith("error: path escapes home"))

    def test_unknown_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = execute_tool_call(_cfg(Path(tmp)), "rm_rf", "{}")
            self.assertEqual(out, "error: unknown tool 'rm_rf'")

    def test_grep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = _cfg(home)
            (home / "uploads" / "2026-05-04").mkdir(parents=True)
            (home / "uploads" / "2026-05-04" / "big.txt").write_text(
                "alpha\nneedle detail\nomega\n",
                encoding="utf-8",
            )

            hits = json.loads(execute_tool_call(cfg, "Grep", json.dumps({
                "path": "uploads",
                "query": "needle",
            })))
            self.assertEqual(hits[0]["line"], 2)

    def test_add_todo_and_set_ai_state_no_longer_native_tools(self) -> None:
        # add_todo and set_ai_state were removed: they live in XML markers now.
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(Path(tmp))
            self.assertEqual(
                execute_tool_call(cfg, "add_todo", "{}"),
                "error: unknown tool 'add_todo'",
            )
            self.assertEqual(
                execute_tool_call(cfg, "set_ai_state", "{}"),
                "error: unknown tool 'set_ai_state'",
            )


if __name__ == "__main__":
    unittest.main()
