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

spec = importlib.util.spec_from_file_location("dashboard_server", SCRIPTS / "dashboard_server.py")
assert spec and spec.loader
dashboard_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_server)

from fiam.config import FiamConfig  # noqa: E402
from fiam.markers import parse_route_markers  # noqa: E402


class CatalogTest(unittest.TestCase):
    def test_config_loads_catalog_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{(root / "home").as_posix()}"',
                    "",
                    "[api]",
                    'provider = "openai_compatible"',
                    'model = "Claude-Opus-4.6"',
                    'base_url = "https://api.poe.com/v1"',
                    'api_key_env = "POE_API_KEY"',
                    "",
                    "[catalog.claude]",
                    'provider = "poe"',
                    'model = "Claude-Opus-4.6"',
                    'fallbacks = ["Claude-Sonnet-4.6"]',
                    "extended_thinking = true",
                    "budget_tokens = 32000",
                    "",
                    "[catalog.gemini]",
                    'provider = "aistudio"',
                    'model = "gemini-2.5-flash-lite"',
                    'fallbacks = []',
                ]) + "\n",
                encoding="utf-8",
            )

            config = FiamConfig.from_toml(toml, root)

        self.assertEqual(config.catalog["claude"].provider, "poe")
        self.assertEqual(config.catalog["claude"].fallbacks, ["Claude-Sonnet-4.6"])
        self.assertTrue(config.catalog["claude"].extended_thinking)
        self.assertEqual(config.catalog["claude"].budget_tokens, 32000)
        self.assertEqual(config.catalog["gemini"].model, "gemini-2.5-flash-lite")

    def test_config_seeds_catalog_from_legacy_api_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{(root / "home").as_posix()}"',
                    "",
                    "[api]",
                    'provider = "openai_compatible"',
                    'model = "Claude-Opus-4.6"',
                    'base_url = "https://api.poe.com/v1"',
                    'api_key_env = "POE_API_KEY"',
                    "",
                    "[api.fallback]",
                    'provider = "google_openai"',
                    'model = "gemini-2.5-flash-lite"',
                    'api_key_env = "GEMINI_API_KEY"',
                ]) + "\n",
                encoding="utf-8",
            )

            config = FiamConfig.from_toml(toml, root)

        self.assertEqual(config.catalog["claude"].provider, "poe")
        self.assertEqual(config.catalog["gemini"].provider, "aistudio")

    def test_route_marker_parses_family_and_reason(self) -> None:
        markers = parse_route_markers('<route family="gemini" reason="math/code fallback"/>')

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].family, "gemini")
        self.assertEqual(markers[0].reason, "math/code fallback")

    def test_route_marker_is_private_control_and_sets_family(self) -> None:
        reply, queued_todos, hold_kind, carry_over = dashboard_server._apply_app_control_markers(
            'visible <route family="gemini" reason="better fit"/>',
            channel="chat",
            runtime="cc",
            user_text="continue",
            attachments=[],
        )

        self.assertEqual(reply, "visible")
        self.assertEqual(queued_todos, 0)
        self.assertEqual(hold_kind, "")
        self.assertEqual(carry_over, {"family": "gemini", "reason": "better fit"})

    def test_catalog_family_overrides_api_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            from fiam.config import Catalog

            config.catalog["gemini"] = Catalog(
                provider="aistudio",
                model="gemini-2.5-flash-lite",
                fallbacks=["gemini-2.5-flash"],
            )
            routed = dashboard_server._config_for_catalog_family(config, "gemini")

        self.assertEqual(routed.api_provider, "google_openai")
        self.assertEqual(routed.api_model, "gemini-2.5-flash-lite")
        self.assertEqual(routed.api_fallback_model, "gemini-2.5-flash")

    def test_anthropic_catalog_family_selects_native_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            from fiam.config import Catalog

            config.catalog["claude"] = Catalog(provider="anthropic", model="claude-sonnet-4-5")
            routed = dashboard_server._config_for_catalog_family(config, "claude")

        self.assertEqual(routed.api_provider, "anthropic")
        self.assertEqual(routed.api_key_env, "ANTHROPIC_API_KEY")

    def test_catalog_toml_section_replacement(self) -> None:
        text = 'home_path = "F:/home"\n\n[api]\nmodel = "old"\n'
        updated = dashboard_server._replace_toml_section(
            text,
            "catalog.claude",
            dashboard_server._catalog_section_lines(
                "claude",
                {
                    "provider": "poe",
                    "model": "Claude-Opus-4.6",
                    "fallbacks": ["Claude-Sonnet-4.6"],
                    "extended_thinking": True,
                    "budget_tokens": 32000,
                },
            ),
        )

        self.assertIn("[api]\nmodel = \"old\"", updated)
        self.assertIn("[catalog.claude]", updated)
        self.assertIn('fallbacks = ["Claude-Sonnet-4.6"]', updated)


if __name__ == "__main__":
    unittest.main()
