"""AI 配置测试：默认 DeepSeek、可自由更换。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai.providers import AIConfig, DEFAULT_PROVIDER, PRESETS, mask_key  # noqa: E402


class TestAIConfig(unittest.TestCase):
    def test_default_is_deepseek(self):
        self.assertEqual(DEFAULT_PROVIDER, "deepseek")
        self.assertEqual(AIConfig().provider, "deepseek")

    def test_preset_fills_blanks(self):
        c = AIConfig(api_key="sk-test").resolved()
        self.assertEqual(c.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(c.model, "deepseek-chat")
        self.assertEqual(c.endpoint(), "https://api.deepseek.com/v1/chat/completions")

    def test_user_can_override_any_field(self):
        c = AIConfig(
            provider="deepseek", base_url="http://127.0.0.1:8000/v1",
            model="my-local-model", api_key="x",
        ).resolved()
        self.assertEqual(c.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(c.model, "my-local-model")

    def test_switch_provider(self):
        c = AIConfig(provider="qwen", api_key="k").resolved()
        self.assertIn("dashscope", c.base_url)
        self.assertEqual(c.model, "qwen-plus")

    def test_ollama_needs_no_key(self):
        c = AIConfig(provider="ollama").resolved()
        self.assertEqual(c.validate(), [])

    def test_custom_requires_fields(self):
        problems = AIConfig(provider="custom").validate()
        self.assertTrue(any("Base URL" in p for p in problems))
        self.assertTrue(any("模型" in p for p in problems))

    def test_missing_key_detected(self):
        problems = AIConfig(provider="deepseek").validate()
        self.assertTrue(any("API Key" in p for p in problems))

    def test_mask_key(self):
        self.assertEqual(mask_key("sk-1234567890abcd"), "•" * 13 + "abcd")
        self.assertEqual(mask_key(""), "")

    def test_all_presets_have_meta(self):
        for k, p in PRESETS.items():
            self.assertEqual(k, p.key)
            if k != "custom":
                self.assertTrue(p.base_url.startswith("http"), k)
                self.assertTrue(p.default_model, k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
