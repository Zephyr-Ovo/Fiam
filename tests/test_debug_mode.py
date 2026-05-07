from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("debug_mode", SCRIPTS / "fiam_lib" / "debug_mode.py")
assert spec and spec.loader
debug_mode = importlib.util.module_from_spec(spec)
sys.modules["debug_mode"] = debug_mode
spec.loader.exec_module(debug_mode)


class DebugModeTest(unittest.TestCase):
    def test_set_debug_enabled_preserves_other_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "fiam.toml"
            toml.write_text(
                "\n".join([
                    'home_path = "F:/home"',
                    "",
                    "[nodes]",
                    'isp_host = "example"',
                ]) + "\n",
                encoding="utf-8",
            )

            debug_mode.set_debug_enabled(toml, True)
            text = toml.read_text(encoding="utf-8")
            self.assertIn("[nodes]", text)
            self.assertIn('isp_host = "example"', text)
            self.assertIn("[debug]", text)
            self.assertIn("enabled = true", text)

            debug_mode.set_debug_enabled(toml, False)
            text = toml.read_text(encoding="utf-8")
            self.assertIn("enabled = false", text)


if __name__ == "__main__":
    unittest.main()