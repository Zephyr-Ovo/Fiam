from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.runtime.api import ApiCompletion, ApiRuntime, FallbackApiClient
from fiam.runtime.prompt import build_plain_prompt_parts
from fiam.store.pool import Pool


class FakeEmbedder:
    def embed(self, text: str) -> np.ndarray:
        vec = np.array([1.0, float(len(text) % 7 + 1), 0.5], dtype=np.float32)
        return vec / np.linalg.norm(vec)


class FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, messages, model, temperature, max_tokens, tools=None) -> ApiCompletion:
        self.calls.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
        })
        return ApiCompletion(
            text=self.reply,
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 4},
            raw={"id": "fake"},
        )


class FailingClient:
    def __init__(self, message: str = "primary down") -> None:
        self.message = message
        self.calls: list[dict] = []

    def complete(self, *, messages, model, temperature, max_tokens, tools=None) -> ApiCompletion:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        raise RuntimeError(self.message)


class ToolLoopClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *, messages, model, temperature, max_tokens, tools=None) -> ApiCompletion:
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            return ApiCompletion(
                text="",
                model=model,
                usage={"prompt_tokens": 3, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 2}},
                raw={"id": "tool-1"},
                tool_calls=[{
                    "id": "call_list",
                    "type": "function",
                    "function": {"name": "Glob", "arguments": "{\"pattern\": \"*\"}"},
                }],
            )
        return ApiCompletion(
            text="done",
            model=model,
            usage={"prompt_tokens": 5, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}},
            raw={"id": "tool-2"},
        )


class ApiRuntimeTest(unittest.TestCase):
    def make_config(self, root: Path) -> FiamConfig:
        home = root / "home"
        code = root / "code"
        config = FiamConfig(
            home_path=home,
            code_path=code,
            user_name="Zephyr",
            embedding_dim=3,
            memory_mode="manual",
            api_model="cheap/test-model",
            api_base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
        config.ensure_dirs()
        config.constitution_md_path.write_text("你是 ai。", encoding="utf-8")
        config.personality_path.write_text("喜欢保持连续身份。", encoding="utf-8")
        return config

    def test_api_runtime_builds_prompt_and_writes_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            pool = Pool(config.pool_dir, dim=config.embedding_dim)
            conductor = Conductor(
                pool=pool,
                embedder=FakeEmbedder(),
                config=config,
                flow_path=config.flow_path,
                memory_mode="manual",
            )
            client = FakeClient("收到。\n[→favilla:Zephyr] 已记录")

            def refresh_recall(vec: np.ndarray) -> int:
                config.background_path.write_text("<!-- recall -->\n- 昨天聊过 API runtime", encoding="utf-8")
                (config.background_path.parent / ".recall_dirty").touch()
                return 1

            runtime = ApiRuntime(
                config,
                client=client,
                conductor=conductor,
                recall_refresher=refresh_recall,
            )
            result = runtime.ask("帮我记一下 API 入口", source="favilla")

            self.assertTrue(result.ok)
            self.assertEqual(result.backend, "api")
            self.assertEqual(result.recall_fragments, 1)
            self.assertEqual(client.calls[0]["model"], "cheap/test-model")

            def _content_text(c: Any) -> str:
                if isinstance(c, list):
                    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                return str(c)

            prompt_text = "\n\n".join(_content_text(m["content"]) for m in client.calls[0]["messages"])
            self.assertIn("你是 ai。", prompt_text)
            self.assertIn("喜欢保持连续身份。", prompt_text)
            self.assertIn("[recall]", prompt_text)
            self.assertIn("昨天聊过 API runtime", prompt_text)
            self.assertIn("帮我记一下 API 入口", prompt_text)

            lines = [json.loads(line) for line in config.flow_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([line["scene"] for line in lines], ["user@favilla", "ai@favilla", "ai@favilla"])
            self.assertEqual(lines[1]["runtime"], "api")
            self.assertEqual(lines[2]["runtime"], "api")
            self.assertNotIn("meta", lines[0])
            self.assertEqual(lines[2]["text"], "收到。")

    def test_api_config_loads_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{home.as_posix()}"',
                    'user_name = "Zephyr"',
                    "",
                    "[api]",
                    'provider = "openai_compatible"',
                    'model = "deepseek/deepseek-chat-v3-0324:free"',
                    'base_url = "https://openrouter.ai/api/v1"',
                    'api_key_env = "OPENROUTER_API_KEY"',
                    "temperature = 0.2",
                    "max_tokens = 256",
                    "timeout_seconds = 15",
                    "tools_enabled = true",
                    "tools_max_loops = 7",
                ]) + "\n",
                encoding="utf-8",
            )
            config = FiamConfig.from_toml(toml, root)

            self.assertEqual(config.api_model, "deepseek/deepseek-chat-v3-0324:free")
            self.assertEqual(config.api_base_url, "https://openrouter.ai/api/v1")
            self.assertEqual(config.api_key_env, "OPENROUTER_API_KEY")
            self.assertEqual(config.api_temperature, 0.2)
            self.assertEqual(config.api_max_tokens, 256)
            self.assertEqual(config.api_timeout_seconds, 15)
            self.assertTrue(config.api_tools_enabled)
            self.assertEqual(config.api_tools_max_loops, 7)

    def test_debug_config_applies_runtime_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{home.as_posix()}"',
                    "",
                    "[daemon]",
                    "idle_timeout_minutes = 30",
                    "poll_interval_seconds = 30",
                    "",
                    "[api]",
                    "tools_max_loops = 4",
                    "",
                    "[app]",
                    'default_runtime = "cc"',
                    "recall_include_recent = false",
                    "",
                    "[conductor]",
                    'memory_mode = "auto"',
                    "",
                    "[debug]",
                    "enabled = true",
                    "idle_timeout_minutes = 2",
                    "poll_interval_seconds = 5",
                    'memory_mode = "manual"',
                    "api_tools_max_loops = 12",
                    'app_default_runtime = "api"',
                    "app_recall_include_recent = true",
                ]) + "\n",
                encoding="utf-8",
            )
            config = FiamConfig.from_toml(toml, root)

            self.assertTrue(config.debug_mode)
            self.assertEqual(config.idle_timeout_minutes, 2)
            self.assertEqual(config.poll_interval_seconds, 5)
            self.assertEqual(config.memory_mode, "manual")
            self.assertEqual(config.api_tools_max_loops, 12)
            self.assertEqual(config.app_default_runtime, "api")
            self.assertTrue(config.app_recall_include_recent)

    def test_api_fallback_config_loads_nested_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{home.as_posix()}"',
                    "",
                    "[api]",
                    'provider = "google_openai"',
                    'model = "gemini-2.5-flash"',
                    'api_key_env = "GEMINI_API_KEY"',
                    "",
                    "[api.fallback]",
                    'provider = "vertex_openai"',
                    'model = "google/gemini-2.5-flash"',
                    'api_key_env = "GOOGLE_APPLICATION_CREDENTIALS"',
                ]) + "\n",
                encoding="utf-8",
            )
            config = FiamConfig.from_toml(toml, root)

            self.assertEqual(config.api_provider, "google_openai")
            self.assertEqual(config.api_model, "gemini-2.5-flash")
            self.assertEqual(config.api_fallback_provider, "vertex_openai")
            self.assertEqual(config.api_fallback_model, "google/gemini-2.5-flash")
            self.assertEqual(config.api_fallback_key_env, "GOOGLE_APPLICATION_CREDENTIALS")

    def test_fallback_client_uses_vertex_model_after_primary_failure(self) -> None:
        primary = FailingClient()
        fallback = FakeClient("pong")
        client = FallbackApiClient(primary, fallback, fallback_model="google/gemini-2.5-flash")

        completion = client.complete(
            messages=[{"role": "user", "content": "ping"}],
            model="gemini-2.5-flash",
            temperature=0.0,
            max_tokens=64,
            tools=None,
        )

        self.assertEqual(completion.text, "pong")
        self.assertEqual(primary.calls[0]["model"], "gemini-2.5-flash")
        self.assertEqual(fallback.calls[0]["model"], "google/gemini-2.5-flash")

    def test_plain_prompt_parts_use_constitution_self_recall_then_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            config.constitution_md_path.write_text("constitution text", encoding="utf-8")
            (config.self_dir / "identity.md").write_text("identity text", encoding="utf-8")
            (config.self_dir / "impressions.md").write_text("impressions text", encoding="utf-8")
            config.background_path.write_text("<!-- hidden -->\nrecall text", encoding="utf-8")
            (config.background_path.parent / ".recall_dirty").touch()

            system_context, user_prompt = build_plain_prompt_parts(config, "hello", source="favilla")

            self.assertLess(system_context.index("constitution text"), system_context.index("# identity"))
            self.assertLess(system_context.index("# identity"), system_context.index("# impressions"))
            self.assertNotIn("[recall]", system_context)
            self.assertEqual(user_prompt, "[recall]\nrecall text\n\nhello")
            self.assertFalse((config.background_path.parent / ".recall_dirty").exists())

    def test_api_tool_loop_executes_local_tool_and_sums_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            client = ToolLoopClient()
            runtime = ApiRuntime(config, client=client)

            result = runtime.ask("list files", source="favilla", record=False, include_recall=False)

            self.assertEqual(result.reply, "done")
            self.assertEqual(result.tool_loops, 2)
            self.assertEqual(result.usage["prompt_tokens"], 8)
            self.assertEqual(result.usage["completion_tokens"], 3)
            self.assertEqual(result.usage["prompt_tokens_details"]["cached_tokens"], 6)
            self.assertIsNotNone(client.calls[0]["tools"])
            self.assertEqual(client.calls[1]["messages"][-1]["role"], "tool")

    def test_image_attachment_uses_image_model_and_content_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            image_path = config.home_path / "uploads" / "test.jpg"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
            client = FakeClient("看到了")
            runtime = ApiRuntime(config, client=client)
            old = os.environ.get("FIAM_API_IMAGE_MODEL")
            os.environ["FIAM_API_IMAGE_MODEL"] = "vision/test-model"
            try:
                result = runtime.ask(
                    "描述图片",
                    source="favilla",
                    record=False,
                    include_recall=False,
                    image_attachments=[{"path": str(image_path), "mime": "image/jpeg"}],
                )
            finally:
                if old is None:
                    os.environ.pop("FIAM_API_IMAGE_MODEL", None)
                else:
                    os.environ["FIAM_API_IMAGE_MODEL"] = old

            self.assertTrue(result.ok)
            self.assertEqual(client.calls[0]["model"], "vision/test-model")
            self.assertIsNone(client.calls[0]["tools"])
            user_content = client.calls[0]["messages"][-1]["content"]
            self.assertIsInstance(user_content, list)
            self.assertEqual(user_content[0]["type"], "text")
            self.assertEqual(user_content[1]["type"], "image_url")
            self.assertTrue(user_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_image_attachment_falls_back_to_vision_description_for_text_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(Path(tmp))
            config.api_model = "text-only/test-model"
            config.vision_model = "google/gemini-2.5-flash"
            image_path = config.home_path / "uploads" / "map.jpg"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"\xff\xd8fake-map\xff\xd9")
            client = FakeClient("主模型看完描述后的回复")
            vision_client = FakeClient("图片显示一个带日期的地图标记。")
            runtime = ApiRuntime(config, client=client, vision_client=vision_client)
            old = os.environ.get("FIAM_API_IMAGE_MODEL")
            if old is not None:
                os.environ.pop("FIAM_API_IMAGE_MODEL", None)
            try:
                result = runtime.ask(
                    "这张图在哪里？",
                    source="favilla",
                    record=False,
                    include_recall=False,
                    image_attachments=[{"path": str(image_path), "mime": "image/jpeg"}],
                )
            finally:
                if old is not None:
                    os.environ["FIAM_API_IMAGE_MODEL"] = old

            self.assertTrue(result.ok)
            self.assertEqual(vision_client.calls[0]["model"], "google/gemini-2.5-flash")
            self.assertIsNone(vision_client.calls[0]["tools"])
            vision_content = vision_client.calls[0]["messages"][-1]["content"]
            self.assertEqual(vision_content[1]["type"], "image_url")
            self.assertEqual(client.calls[0]["model"], "text-only/test-model")
            self.assertIsNotNone(client.calls[0]["tools"])
            user_content = client.calls[0]["messages"][-1]["content"]
            self.assertIsInstance(user_content, str)
            self.assertIn("[image description fallback]", user_content)
            self.assertIn("带日期的地图标记", user_content)
            self.assertEqual(result.usage["prompt_tokens"], 20)


if __name__ == "__main__":
    unittest.main()