from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("dashboard_server", SCRIPTS / "dashboard_server.py")
assert spec and spec.loader
dashboard_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_server)


class AppBackendRouterTest(unittest.TestCase):
    def test_daily_chat_uses_api(self) -> None:
        self.assertEqual(dashboard_server._select_app_chat_backend("今天晚点提醒我喝水"), "api")

    def test_code_chat_uses_cc(self) -> None:
        self.assertEqual(dashboard_server._select_app_chat_backend("帮我看一下 pytest 报错"), "cc")

    def test_attachments_use_cc(self) -> None:
        self.assertEqual(
            dashboard_server._select_app_chat_backend("帮我看这个文件", [{"path": "/tmp/a.txt"}]),
            "cc",
        )


if __name__ == "__main__":
    unittest.main()
