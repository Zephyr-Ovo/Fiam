from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fiam.config import FiamConfig
from fiam.conductor import Conductor
from fiam.runtime.api import ApiCompletion, ApiRuntime
from fiam.store.pool import Pool


class FakeEmbedder:
    def embed(self, text: str) -> np.ndarray:
        vec = np.array([1.0, float(len(text) % 7 + 1), 0.5], dtype=np.float32)
        return vec / np.linalg.norm(vec)


class FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, messages, model, temperature, max_tokens) -> ApiCompletion:
        self.calls.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return ApiCompletion(
            text=self.reply,
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 4},
            raw={"id": "fake"},
        )


class ApiRuntimeTest(unittest.TestCase):
    def make_config(self, root: Path) -> FiamConfig:
        home = root / "home"
        code = root / "code"
        config = FiamConfig(
            home_path=home,
            code_path=code,
            ai_name="Fiet",
            user_name="Zephyr",
            embedding_dim=3,
            memory_mode="manual",
            api_model="cheap/test-model",
            api_base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
        config.ensure_dirs()
        config.claude_md_path.write_text("你是 Fiet。", encoding="utf-8")
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

            prompt_text = "\n\n".join(m["content"] for m in client.calls[0]["messages"])
            self.assertIn("你是 Fiet。", prompt_text)
            self.assertIn("[self]", prompt_text)
            self.assertIn("喜欢保持连续身份。", prompt_text)
            self.assertIn("[recall]", prompt_text)
            self.assertIn("昨天聊过 API runtime", prompt_text)
            self.assertIn("[wake:favilla] 帮我记一下 API 入口", prompt_text)

            lines = [json.loads(line) for line in config.flow_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([line["source"] for line in lines], ["api", "dispatch", "api"])
            self.assertEqual(lines[0]["meta"], {"runtime": "api", "input_source": "favilla", "role": "user"})
            self.assertEqual(lines[1]["meta"]["target"], "favilla")
            self.assertEqual(lines[2]["text"], "fiet：收到。")

    def test_api_config_loads_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            toml = root / "fiam.toml"
            toml.write_text(
                "\n".join([
                    f'home_path = "{home.as_posix()}"',
                    'ai_name = "Fiet"',
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


if __name__ == "__main__":
    unittest.main()