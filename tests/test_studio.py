from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.append(path)

spec = importlib.util.spec_from_file_location("dashboard_server", SCRIPTS / "dashboard_server.py")
assert spec and spec.loader
dashboard_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_server)


class StudioVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="studio_test_")
        os.environ["FIAM_STUDIO_VAULT_DIR"] = self.tmp

    def tearDown(self) -> None:
        os.environ.pop("FIAM_STUDIO_VAULT_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_share_appends_block_and_commits(self) -> None:
        res = dashboard_server._studio_share({
            "source": "atrium",
            "url": "https://example.com/x",
            "selection": "hello world",
            "agent": "copilot",
            "tags": ["web", "test"],
        })
        self.assertTrue(res["ok"])
        self.assertTrue(res["rel_path"].startswith("inbox/"))
        self.assertFalse(res["archive"])
        body = (Path(self.tmp) / res["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("atrium", body)
        self.assertIn("> hello world", body)
        self.assertIn("source: https://example.com/x", body)
        self.assertIn("#web", body)
        # second share appends, doesn't replace
        res2 = dashboard_server._studio_share({
            "source": "manual",
            "selection": "second",
            "agent": "zephyr",
        })
        self.assertEqual(res["rel_path"], res2["rel_path"])
        body2 = (Path(self.tmp) / res2["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("hello world", body2)
        self.assertIn("second", body2)

    def test_quicknote_routes_to_desk(self) -> None:
        res = dashboard_server._studio_quicknote({"text": "remember this"})
        self.assertTrue(res["rel_path"].startswith("desk/"))
        body = (Path(self.tmp) / res["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("remember this", body)
        self.assertIn("quicknote", body)

    def test_shelf_writes_frontmatter_archive(self) -> None:
        res = dashboard_server._studio_share({
            "source": "atrium",
            "target_file": "shelf/web/example.md",
            "url": "https://example.com",
            "selection": "ARCHIVED BODY",
            "agent": "copilot",
            "tags": ["read-later"],
        })
        self.assertTrue(res["archive"])
        body = (Path(self.tmp) / res["rel_path"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("---\n"))
        self.assertIn("source: atrium", body)
        self.assertIn("url: https://example.com", body)
        self.assertIn("ARCHIVED BODY", body)

    def test_shelf_refuses_overwrite(self) -> None:
        dashboard_server._studio_share({
            "source": "atrium", "target_file": "shelf/web/dup.md",
            "selection": "first", "agent": "copilot",
        })
        with self.assertRaises(ValueError):
            dashboard_server._studio_share({
                "source": "atrium", "target_file": "shelf/web/dup.md",
                "selection": "second", "agent": "copilot",
            })

    def test_path_traversal_blocked(self) -> None:
        for bad in ("../escape.md", "/etc/passwd", "inbox/../../escape.md", ".git/config", "foo/.git/x"):
            with self.assertRaises(ValueError, msg=bad):
                dashboard_server._studio_share({
                    "source": "manual",
                    "target_file": bad,
                    "selection": "x",
                    "agent": "zephyr",
                })

    def test_target_file_must_be_md(self) -> None:
        with self.assertRaises(ValueError):
            dashboard_server._studio_share({
                "source": "manual", "target_file": "inbox/note.txt",
                "selection": "x", "agent": "zephyr",
            })

    def test_list_returns_files_and_log(self) -> None:
        dashboard_server._studio_share({
            "source": "manual", "selection": "a", "agent": "zephyr",
        })
        dashboard_server._studio_share({
            "source": "manual", "target_file": "desk/note.md",
            "selection": "b", "agent": "zephyr",
        })
        listing = dashboard_server._studio_list()
        paths = [f["path"] for f in listing["files"]]
        self.assertTrue(any(p.startswith("inbox/") for p in paths))
        self.assertIn("desk/note.md", paths)
        # git log entries (if git is available)
        if shutil.which("git"):
            self.assertGreaterEqual(len(listing["log"]), 1)
            self.assertTrue(all("sha" in entry and "msg" in entry for entry in listing["log"]))

    def test_list_with_dir_filter(self) -> None:
        dashboard_server._studio_share({
            "source": "manual", "target_file": "desk/note.md",
            "selection": "deskonly", "agent": "zephyr",
        })
        dashboard_server._studio_share({
            "source": "manual", "selection": "inboxonly", "agent": "zephyr",
        })
        listing = dashboard_server._studio_list(dir_filter="desk")
        paths = [f["path"] for f in listing["files"]]
        self.assertTrue(all(p.startswith("desk/") for p in paths))

    def test_file_read_roundtrip(self) -> None:
        res = dashboard_server._studio_share({
            "source": "manual", "selection": "hello roundtrip", "agent": "zephyr",
        })
        text, meta = dashboard_server._studio_file(res["rel_path"])
        self.assertIn("hello roundtrip", text)
        self.assertEqual(meta["path"], res["rel_path"])

    def test_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            dashboard_server._studio_file("inbox/missing.md")

    def test_share_requires_content(self) -> None:
        with self.assertRaises(ValueError):
            dashboard_server._studio_share({"source": "manual", "agent": "zephyr"})


if __name__ == "__main__":
    unittest.main()
