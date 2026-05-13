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
from fiam.store.beat import Beat, append_beat  # noqa: E402
from fiam.store.object_catalog import ObjectCatalog  # noqa: E402
from fiam.store.objects import ObjectStore  # noqa: E402
from fiam.runtime.tools import execute_tool_call  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
import json  # noqa: E402


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

    def test_config_requires_explicit_catalog_sections(self) -> None:
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

        self.assertEqual(config.catalog, {})

    def test_route_marker_parses_family_and_reason(self) -> None:
        markers = parse_route_markers('<route family="gemini" reason="math/code fallback"/>')

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].family, "gemini")
        self.assertEqual(markers[0].reason, "math/code fallback")

    def test_route_marker_is_private_control_and_sets_family(self) -> None:
        reply, queued_todos, hold_reason, route = dashboard_server._apply_app_control_markers(
            'visible <route family="gemini" reason="better fit"/>',
            channel="chat",
            runtime="cc",
            user_text="continue",
            attachments=[],
        )

        self.assertEqual(reply, "visible")
        self.assertEqual(queued_todos, 0)
        self.assertEqual(hold_reason, "")
        self.assertEqual(route, {"family": "gemini", "reason": "better fit"})

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

    def test_object_catalog_searches_event_object_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            digest = ObjectStore(config.object_dir).put_bytes(b"object bytes", suffix="")
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
                actor="ai",
                channel="email",
                kind="attachment",
                content="attachment: note.txt",
                meta={
                    "object_hash": digest,
                    "object_name": "note.txt",
                    "object_mime": "text/plain",
                    "object_size": 12,
                    "object_summary": "short note attachment",
                    "object_tags": ["note", "dispatch"],
                    "dispatch_id": "disp_1",
                    "turn_id": "turn_1",
                },
            ))

            catalog = ObjectCatalog.from_config(config)
            records = catalog.search("note")

            self.assertEqual(records[0].object_hash, digest)
            self.assertEqual(records[0].token, f"obj:{digest[:12]}")
            self.assertEqual(records[0].summary, "short note attachment")
            self.assertEqual(records[0].tags, ("note", "dispatch"))
            self.assertEqual(catalog.resolve_token(f"obj:{digest[:12]}"), digest)

            tool_result = json.loads(execute_tool_call(config, "ObjectSearch", json.dumps({"query": "dispatch", "token": f"obj:{digest[:12]}"})))
            self.assertEqual(tool_result["object_hash"], digest)
            self.assertEqual(tool_result["records"][0]["object_hash"], digest)

    def test_object_catalog_reads_upload_manifest_and_rejects_ambiguous_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            first = "a" * 64
            second = "a" * 12 + "b" * 52
            manifest = config.home_path / "uploads" / "manifest.jsonl"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("\n".join([
                json.dumps({"uploaded_at": "2026-05-13T09:00:00+00:00", "object_hash": first, "name": "alpha.png", "mime": "image/png", "size": 5}),
                json.dumps({"uploaded_at": "2026-05-13T09:01:00+00:00", "object_hash": second, "name": "beta.png", "mime": "image/png", "size": 6}),
            ]) + "\n", encoding="utf-8")

            catalog = ObjectCatalog.from_config(config)

            self.assertEqual(catalog.search("beta")[0].object_hash, second)
            self.assertEqual(catalog.resolve_token(f"obj:{first[:12]}"), "")
            self.assertEqual(catalog.resolve_token(f"obj:{first[:16]}"), first)


if __name__ == "__main__":
    unittest.main()
