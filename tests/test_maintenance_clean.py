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

from fiam.config import FiamConfig

spec = importlib.util.spec_from_file_location("maintenance", SCRIPTS / "fiam_lib" / "maintenance.py")
assert spec and spec.loader
maintenance = importlib.util.module_from_spec(spec)
sys.modules["maintenance"] = maintenance
spec.loader.exec_module(maintenance)


class MaintenanceCleanTest(unittest.TestCase):
    def test_full_whiteboard_clean_preserves_config_and_self_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / "code"
            home = root / "home"
            config = FiamConfig(
                home_path=home,
                code_path=code,
                user_name="Zephyr",
                embedding_dim=3,
            )
            config.ensure_dirs()
            config.constitution_md_path.write_text("constitution", encoding="utf-8")
            (config.self_dir / "identity.md").write_text("identity", encoding="utf-8")
            (code / "fiam.toml").write_text("home_path = 'x'\n", encoding="utf-8")

            generated_files = [
                config.pool_dir / "events" / "ev.md",
                config.feature_dir / "beats.jsonl",
                config.flow_path,
                config.annotation_state_path,
                home / "transcript" / "favilla.jsonl",
                home / "uploads" / "manifest.jsonl",
                config.background_path,
                home / ".recall_dirty",
                home / "app_cuts.jsonl",
                config.active_session_path,
                config.todo_path,
                config.ai_state_path,
                config.state_path,
                config.daily_summary_path,
                config.pending_external_path,
                home / "pending_external.processing",
                config.inbox_dir / "in.md",
                config.outbox_dir / "out.md",
                config.self_dir / "retired" / "old.json",
                code / "logs" / "sessions" / "old.jsonl",
            ]
            for path in generated_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            targets = maintenance.collect_clean_targets(code, config)
            labels = "\n".join(target.label for target in targets)
            self.assertIn("app chat transcript", labels)
            self.assertIn("uploaded test files", labels)
            self.assertIn("pool events/vectors/edges", labels)

            maintenance.apply_clean_targets(targets, config)

            for path in generated_files:
                self.assertFalse(path.exists(), path)
            self.assertTrue(config.constitution_md_path.exists())
            self.assertTrue((config.self_dir / "identity.md").exists())
            self.assertTrue((code / "fiam.toml").exists())
            self.assertTrue((config.pool_dir / "events").is_dir())
            self.assertTrue(config.feature_dir.is_dir())
            self.assertTrue(config.inbox_dir.is_dir())
            self.assertTrue(config.outbox_dir.is_dir())


if __name__ == "__main__":
    unittest.main()