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
                execute_tool_call(cfg, "Write", json.dumps({
                    "path": "self/lessons.md", "content": "first lesson",
                })),
            )
            self.assertTrue((home / "self" / "lessons.md").exists())

            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "write_file", json.dumps({
                    "path": "notes/today.md", "content": "line one\n",
                })),
            )
            self.assertEqual(
                "ok",
                execute_tool_call(cfg, "write_file", json.dumps({
                    "path": "notes/today.md", "content": "line two\n", "mode": "append",
                })),
            )
            self.assertEqual((home / "notes" / "today.md").read_text(encoding="utf-8"), "line one\nline two\n")

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

    def test_grep_todo_and_state_tools(self) -> None:
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

            at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            out = json.loads(execute_tool_call(cfg, "add_todo", json.dumps({
                "at": at,
                "type": "notify",
                "reason": "test todo",
            })))
            self.assertTrue(out["ok"])
            self.assertTrue((home / "self" / "todo.jsonl").exists())

            state = json.loads(execute_tool_call(cfg, "set_ai_state", json.dumps({
                "state": "busy",
                "reason": "testing",
            })))
            self.assertEqual(state["state"], "busy")
            self.assertTrue((home / "self" / "ai_state.json").exists())

            now = json.loads(execute_tool_call(cfg, "get_time", "{}"))
            self.assertTrue(now["ok"])
            self.assertIn("utc", now)
            self.assertEqual(now["timezone"], "Asia/Shanghai")
            self.assertTrue(now["local"].endswith("+08:00"))

            naive_local = (
                datetime.now(timezone.utc)
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .replace(tzinfo=None, microsecond=0)
                + timedelta(hours=1)
            ).isoformat()
            naive = json.loads(execute_tool_call(cfg, "add_todo", json.dumps({
                "at": naive_local,
                "type": "notify",
                "reason": "naive local todo",
            })))
            self.assertTrue(naive["ok"])
            self.assertTrue(naive["at"].endswith("+08:00"))

            relative = json.loads(execute_tool_call(cfg, "add_todo", json.dumps({
                "delay_minutes": 5,
                "type": "check",
                "reason": "relative todo",
            })))
            self.assertTrue(relative["ok"])
            self.assertEqual(relative["type"], "check")

            invalid = execute_tool_call(cfg, "add_todo", json.dumps({
                "delay_minutes": 5,
                "type": "seek",
                "reason": "invalid todo type",
            }))
            self.assertIn("private, notify, or check", invalid)


if __name__ == "__main__":
    unittest.main()
