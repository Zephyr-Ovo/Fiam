from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from fiam.track.collectors.edit import EditEvent, collect_edit_events
from fiam.track.collectors.system import SystemEvent, collect_system_events
from fiam.track.collectors.work import collect_work_events
from fiam.track.recall import recall
from fiam.track.summarizer import summarize_edits, summarize_system
from fiam.track.writer import write_track


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    base_env = os.environ.copy()
    base_env.update({
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
    })
    if env:
        base_env.update(env)
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, env=base_env)


def _make_commit(repo: Path, path: str, body: str, subject: str, when: datetime) -> None:
    fp = repo / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(body, encoding="utf-8")
    _git(repo, "add", path)
    iso = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    _git(repo, "commit", "-m", subject, env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})


class CollectEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="track_collect_"))
        _git(self.tmp, "init", "--quiet", "-b", "main")
        _git(self.tmp, "config", "user.email", "tester@example.com")
        _git(self.tmp, "config", "user.name", "Tester")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parses_commits_newest_first(self) -> None:
        t0 = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
        _make_commit(self.tmp, "desk/2026-05-13.md", "alpha\n", "morning note", t0)
        _make_commit(self.tmp, "shelf/article.md", "beta\nline two\n", "save article", t1)
        events = collect_edit_events(self.tmp)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].subject, "save article")
        self.assertEqual(events[1].subject, "morning note")
        self.assertEqual(events[0].ts, t1)
        self.assertIn("shelf/article.md", events[0].files)
        self.assertGreaterEqual(events[0].insertions, 2)

    def test_excludes_track_files(self) -> None:
        t = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
        _make_commit(self.tmp, "desk/x.md", "x\n", "desk write", t)
        _make_commit(self.tmp, "track/edit.md", "y\n", "track noise", t + timedelta(minutes=1))
        events = collect_edit_events(self.tmp)
        # The track-only commit gets dropped entirely (pathspec :(exclude)track/**).
        subjects = [e.subject for e in events]
        self.assertIn("desk write", subjects)
        self.assertNotIn("track noise", subjects)

    def test_no_git_returns_empty(self) -> None:
        plain = Path(tempfile.mkdtemp(prefix="track_no_git_"))
        try:
            self.assertEqual(collect_edit_events(plain), [])
        finally:
            shutil.rmtree(plain, ignore_errors=True)


class SummarizeTests(unittest.TestCase):
    def test_hierarchical_structure_fallback(self) -> None:
        events = [
            EditEvent(
                sha="a" * 40,
                ts=datetime(2026, 5, 14, 9, 15, tzinfo=timezone.utc),
                author="Zephyr",
                subject="add quicknote",
                files=("desk/2026-05-14.md",),
                insertions=3, deletions=0,
            ),
            EditEvent(
                sha="b" * 40,
                ts=datetime(2026, 5, 14, 18, 0, tzinfo=timezone.utc),
                author="Zephyr",
                subject="archive article",
                files=("shelf/x.md",),
                insertions=20, deletions=1,
            ),
            EditEvent(
                sha="c" * 40,
                ts=datetime(2026, 4, 30, 22, 0, tzinfo=timezone.utc),
                author="Zephyr",
                subject="month-end note",
                files=("desk/2026-04-30.md",),
            ),
        ]
        body = summarize_edits(events)
        self.assertIn("# 2026-05", body)
        self.assertIn("# 2026-04", body)
        self.assertIn("## 2026-05-14", body)
        self.assertIn("## 2026-04-30", body)
        self.assertIn("### 09:15 · add quicknote", body)
        self.assertIn("### 18:00 · archive article", body)
        # newest month first
        self.assertLess(body.index("# 2026-05"), body.index("# 2026-04"))

    def test_custom_summarize_fn_used(self) -> None:
        events = [
            EditEvent(sha="a" * 40, ts=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
                      author="Z", subject="s1", files=("desk/a.md",)),
        ]
        calls: list[str] = []

        def fake(level: str, ctx: str) -> str:
            calls.append(level)
            return f"[{level}] narrative"

        body = summarize_edits(events, summarize_fn=fake)
        self.assertIn("[month] narrative", body)
        self.assertIn("[day] narrative", body)
        self.assertEqual(sorted(set(calls)), ["day", "month"])


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="track_write_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_writes_with_frontmatter(self) -> None:
        target = write_track(self.vault, "edit", "# 2026-05\n\nbody\n")
        self.assertEqual(target, self.vault / "track" / "edit.md")
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("editable: none", text)
        self.assertIn("visibility: both", text)
        self.assertIn("track_name: edit", text)
        self.assertIn("# 2026-05", text)

    def test_rejects_bad_names(self) -> None:
        for bad in ("", "../escape", "with/slash", "dot.dot", "weird!"):
            with self.assertRaises(ValueError, msg=bad):
                write_track(self.vault, bad, "x")


class RecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="track_recall_"))
        self.now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
        body = textwrap.dedent("""\
            # 2026-05

            month-narrative-may

            ## 2026-05-14

            day-narrative-today

            ### 09:00 · fresh commit
            - sha: `aaa1111` · +3/-0
            - files: desk/a.md

            ## 2026-05-03

            day-narrative-eleven-days-ago

            ### 14:00 · mid commit
            - sha: `bbb2222` · +1/-0
            - files: desk/b.md

            # 2026-04

            month-narrative-april

            ## 2026-04-10

            day-narrative-april

            ### 11:00 · april commit
            - sha: `ccc3333` · +1/-0
            - files: desk/c.md

            # 2026-01

            month-narrative-january

            ## 2026-01-05

            ### 08:00 · january commit
            - sha: `ddd4444` · +1/-0
            - files: desk/d.md
            """)
        write_track(self.vault, "edit", body, now=self.now)

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_recent_keeps_full_detail(self) -> None:
        out = recall(self.vault, "edit", now=self.now)
        self.assertIn("### 09:00 · fresh commit", out)
        self.assertIn("sha: `aaa1111`", out)

    def test_eight_to_thirty_days_drops_h3(self) -> None:
        out = recall(self.vault, "edit", now=self.now)
        # 2026-05-03 is 11 days before 2026-05-14 → keep ##, drop ###
        self.assertIn("## 2026-05-03", out)
        self.assertNotIn("### 14:00 · mid commit", out)
        self.assertNotIn("sha: `bbb2222`", out)

    def test_thirty_one_to_ninety_days_keeps_only_h1(self) -> None:
        out = recall(self.vault, "edit", now=self.now)
        # 2026-04-10 is ~34 days before → keep #, drop ## and ###
        self.assertIn("# 2026-04", out)
        self.assertNotIn("## 2026-04-10", out)
        self.assertNotIn("### 11:00 · april commit", out)

    def test_over_ninety_days_titles_only(self) -> None:
        out = recall(self.vault, "edit", now=self.now)
        # 2026-01 is >90 days → tier 0 → drop everything, even #
        self.assertNotIn("# 2026-01", out)
        self.assertNotIn("january commit", out)

    def test_since_filter_hides_older(self) -> None:
        out = recall(self.vault, "edit", now=self.now,
                     since=datetime(2026, 5, 10, tzinfo=timezone.utc))
        self.assertIn("# 2026-05", out)
        self.assertIn("## 2026-05-14", out)
        self.assertNotIn("## 2026-05-03", out)
        self.assertNotIn("# 2026-04", out)


class CollectWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="track_work_"))
        _git(self.tmp, "init", "--quiet", "-b", "main")
        _git(self.tmp, "config", "user.email", "tester@example.com")
        _git(self.tmp, "config", "user.name", "Tester")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collects_code_repo_commits(self) -> None:
        t0 = datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc)
        _make_commit(self.tmp, "src/module.py", "x = 1\n", "Add module", t0)
        _make_commit(self.tmp, "scripts/run.sh", "#!/bin/sh\n", "Add run script", t0 + timedelta(hours=1))
        events = collect_work_events(self.tmp)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].subject, "Add run script")
        self.assertIn("scripts/run.sh", events[0].files)

    def test_no_git_returns_empty(self) -> None:
        plain = Path(tempfile.mkdtemp(prefix="track_work_no_git_"))
        try:
            self.assertEqual(collect_work_events(plain), [])
        finally:
            shutil.rmtree(plain, ignore_errors=True)

    def test_since_filter(self) -> None:
        t0 = datetime(2026, 5, 10, 8, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc)
        _make_commit(self.tmp, "a.py", "a\n", "old commit", t0)
        _make_commit(self.tmp, "b.py", "b\n", "new commit", t1)
        events = collect_work_events(self.tmp, since=datetime(2026, 5, 13, tzinfo=timezone.utc))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].subject, "new commit")


class CollectSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="track_system_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_traces(self, rows: list[dict]) -> None:
        import json
        traces = self.tmp / "turn_traces.jsonl"
        traces.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_parses_trace_rows(self) -> None:
        self._write_traces([
            {
                "phase": "dashboard.runtime",
                "status": "ok",
                "started_at": "2026-05-14T10:00:00+00:00",
                "ended_at": "2026-05-14T10:00:05+00:00",
                "duration_ms": 5000,
                "channel": "chat",
                "surface": "favilla",
                "turn_id": "turn_abc",
                "request_id": "req_123",
                "refs": {"model": "opus"},
            },
            {
                "phase": "commit.events",
                "status": "ok",
                "started_at": "2026-05-14T10:00:06+00:00",
                "duration_ms": 50,
                "channel": "",
                "surface": "",
                "turn_id": "turn_abc",
                "request_id": "req_123",
            },
        ])
        events = collect_system_events(self.tmp)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].phase, "commit.events")
        self.assertEqual(events[1].phase, "dashboard.runtime")
        self.assertEqual(events[1].model, "opus")

    def test_since_filter(self) -> None:
        self._write_traces([
            {"phase": "old", "status": "ok", "started_at": "2026-05-10T10:00:00+00:00", "duration_ms": 0},
            {"phase": "new", "status": "ok", "started_at": "2026-05-14T10:00:00+00:00", "duration_ms": 0},
        ])
        events = collect_system_events(self.tmp, since=datetime(2026, 5, 13, tzinfo=timezone.utc))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phase, "new")

    def test_limit(self) -> None:
        self._write_traces([
            {"phase": f"p{i}", "status": "ok", "started_at": f"2026-05-14T{10+i}:00:00+00:00", "duration_ms": 0}
            for i in range(5)
        ])
        events = collect_system_events(self.tmp, limit=2)
        self.assertEqual(len(events), 2)

    def test_no_file_returns_empty(self) -> None:
        self.assertEqual(collect_system_events(self.tmp), [])


class SummarizeSystemTests(unittest.TestCase):
    def test_hierarchical_structure(self) -> None:
        events = [
            SystemEvent(
                ts=datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc),
                phase="dashboard.runtime",
                status="ok",
                channel="chat",
                surface="favilla",
                duration_ms=5000,
            ),
            SystemEvent(
                ts=datetime(2026, 5, 14, 11, 30, tzinfo=timezone.utc),
                phase="commit.events",
                status="ok",
                channel="",
                surface="",
                duration_ms=50,
            ),
        ]
        body = summarize_system(events)
        self.assertIn("# 2026-05", body)
        self.assertIn("## 2026-05-14", body)
        self.assertIn("### 10:00 · runtime · ok", body)
        self.assertIn("### 11:30 · commit.events · ok", body)
        self.assertIn("5000ms", body)
        self.assertIn("chat/favilla", body)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(summarize_system([]), "")


class RecallWorkTests(unittest.TestCase):
    """Recall works for any track name, not just edit — verify with work track."""
    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="track_recall_work_"))
        self.now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
        body = textwrap.dedent("""\
            # 2026-05

            ## 2026-05-14

            ### 09:00 · Fix cc-channel
            - sha: `abc1234` · +10/-3
            - files: scripts/fiam_lib/cc_channel.py
            """)
        write_track(self.vault, "work", body, now=self.now)

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_recall_work_track(self) -> None:
        out = recall(self.vault, "work", now=self.now)
        self.assertIn("# 2026-05", out)
        self.assertIn("### 09:00 · Fix cc-channel", out)
        self.assertIn("sha: `abc1234`", out)


if __name__ == "__main__":
    unittest.main()
