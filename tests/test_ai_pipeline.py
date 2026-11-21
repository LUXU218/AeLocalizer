"""AI 翻译管线测试：闸门 / 解析 / 缓存 / 预算 / 重试（全部离线，用假客户端）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai.client import AIError, ChatResult, Usage  # noqa: E402
from core.ai.gates import (  # noqa: E402
    GLOSSARY_FIXED, OK, REJECTED, SHORTENED, UNTRANSLATED,
    check_blacklist, check_encoding, check_length, check_untranslated,
    gates_from_text, parse_response, run_gates,
)
from core.ai.pipeline import (  # noqa: E402
    BudgetExceeded, Cache, Cancelled, Translator, estimate_cost,
)
from core.ai.prompt import PROMPT_VERSION, build_messages  # noqa: E402
from core.ai.providers import AIConfig  # noqa: E402
from core.encoder import WORLD_UTF8  # noqa: E402


class FakeClient:
    """假客户端：按预设返回，记录调用次数。"""

    def __init__(self, payloads, *, usage=None):
        self.payloads = list(payloads)
        self.calls = 0
        self.usage = usage or Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    def chat(self, messages, *, max_tokens=2048):
        self.calls += 1
        if self.calls <= len(self.payloads):
            text = self.payloads[self.calls - 1]
        else:
            text = self.payloads[-1]
        if isinstance(text, Exception):
            raise text
        return ChatResult(text=text, usage=self.usage, model="fake")


def _json(items):
    return json.dumps(items, ensure_ascii=False)


class TestParse(unittest.TestCase):
    def test_plain_array(self):
        items, err = parse_response(_json([{"en": "A", "zh": "甲"}]))
        self.assertEqual(err, "")
        self.assertEqual(items, [{"en": "A", "zh": "甲"}])

    def test_code_fence(self):
        fence = chr(96) * 3
        text = fence + "json\n" + _json([{"en": "A", "zh": "甲"}]) + "\n" + fence
        items, err = parse_response(text)
        self.assertEqual(err, "")
        self.assertEqual(len(items), 1)

    def test_extra_prose(self):
        text = "好的，结果如下：\n" + _json([{"en": "A", "zh": "甲"}]) + "\n希望有帮助"
        items, err = parse_response(text)
        self.assertEqual(len(items), 1)

    def test_entries_wrapper(self):
        text = _json({"entries": [{"en": "A", "zh": "甲"}]})
        items, err = parse_response(text)
        self.assertEqual(len(items), 1)

    def test_single_object(self):
        items, _ = parse_response(_json({"en": "A", "zh": "甲"}))
        self.assertEqual(len(items), 1)

    def test_garbage(self):
        items, err = parse_response("抱歉我无法完成")
        self.assertEqual(items, [])
        self.assertTrue(err)

    def test_alias_keys(self):
        items, _ = parse_response(_json([{"english": "A", "chinese": "甲"}]))
        self.assertEqual(items[0]["en"], "A")


class TestGates(unittest.TestCase):
    def test_encoding_rejects_control(self):
        self.assertFalse(check_encoding("坏\x01字")[0])
        self.assertFalse(check_encoding("替换\ufffd")[0])
        self.assertFalse(check_encoding("")[0])
        self.assertTrue(check_encoding("正常")[0])

    def test_encoding_rejects_emoji(self):
        self.assertFalse(check_encoding("发光\U0001F600")[0])

    def test_blacklist(self):
        hit, _ = check_blacklist("Right", "正确")
        self.assertTrue(hit)
        self.assertFalse(check_blacklist("Right", "右侧")[0])

    def test_untranslated(self):
        self.assertTrue(check_untranslated("Glow", "Glow"))
        self.assertTrue(check_untranslated("Glow", "glow "))
        self.assertFalse(check_untranslated("Glow", "发光"))

    def test_length_ok(self):
        st, used, _ = check_length("Amount", "数量", WORLD_UTF8)
        self.assertEqual(st, OK)
        self.assertEqual(used, "数量")

    def test_length_shortened(self):
        st, used, why = check_length("Hue", "色相", WORLD_UTF8)
        self.assertIn(st, (SHORTENED, REJECTED))

    def test_length_unencodable(self):
        st, _, _ = check_length("Copyright", "版权\u2122", "ansi")
        self.assertEqual(st, REJECTED)

    def test_run_gates_mixed(self):
        raw = [
            {"en": "Amount", "zh": "数量"},
            {"en": "Right", "zh": "正确"},          # 黑名单
            {"en": "Glow", "zh": "Glow"},           # 未译
            {"en": "Bad", "zh": "坏\x01"},          # 控制字符
        ]
        rep = run_gates(raw, world=WORLD_UTF8)
        self.assertEqual(rep.total, 4)
        self.assertEqual(rep.ok, 1)
        self.assertEqual(rep.rejected, 2)
        self.assertEqual(rep.untranslated, 1)
        self.assertIn("通过 1", rep.summary())

    def test_glossary_strict_fixes(self):
        gl = {"Matte": {"zh": "遮罩", "strict": True}}
        rep = run_gates([{"en": "Matte", "zh": "蒙版"}], glossary=gl)
        self.assertEqual(rep.glossary_fixed, 1)
        self.assertEqual(rep.entries[0].zh, "遮罩")

    def test_glossary_nonstrict_ignored(self):
        gl = {"Matte": {"zh": "遮罩", "strict": False}}
        rep = run_gates([{"en": "Matte", "zh": "蒙版"}], glossary=gl)
        self.assertEqual(rep.glossary_fixed, 0)
        self.assertEqual(rep.entries[0].zh, "蒙版")

    def test_gates_from_text_parse_error(self):
        rep = gates_from_text("完全不是 JSON")
        self.assertTrue(rep.parse_error)
        self.assertEqual(rep.total, 0)


class TestPrompt(unittest.TestCase):
    def test_has_fewshot(self):
        msgs = build_messages(["Glow"])
        self.assertEqual(msgs[0]["role"], "system")
        self.assertTrue(any(m["role"] == "assistant" for m in msgs))
        self.assertIn("Glow", msgs[1]["content"])

    def test_glossary_injected(self):
        msgs = build_messages(["Matte"], glossary={"Matte": {"zh": "遮罩"}})
        self.assertIn("遮罩", msgs[1]["content"])

    def test_glossary_limited(self):
        gl = {"W%d" % i: {"zh": "词%d" % i} for i in range(200)}
        names = ["W%d" % i for i in range(200)]
        msgs = build_messages(names, glossary=gl)
        self.assertLess(msgs[1]["content"].count("=>"), 60)

    def test_prompt_version_exists(self):
        self.assertTrue(PROMPT_VERSION)


class TestCache(unittest.TestCase):
    def test_key_stable(self):
        a = Cache.key("Glow", "p", "g1", "m")
        b = Cache.key("Glow", "p", "g1", "m")
        self.assertEqual(a, b)

    def test_key_changes_with_inputs(self):
        base = Cache.key("Glow", "p", "g1", "m")
        self.assertNotEqual(base, Cache.key("Glow2", "p", "g1", "m"))
        self.assertNotEqual(base, Cache.key("Glow", "p2", "g1", "m"))
        self.assertNotEqual(base, Cache.key("Glow", "p", "g2", "m"))
        self.assertNotEqual(base, Cache.key("Glow", "p", "g1", "m2"))

    def test_roundtrip_disk(self):
        p = os.path.join(tempfile.mkdtemp(), "c.json")
        c = Cache(p)
        c.put("k1", "值1")
        c.flush()
        c2 = Cache(p)
        self.assertEqual(c2.get("k1"), "值1")


class TestCost(unittest.TestCase):
    def test_known_model(self):
        c = estimate_cost("deepseek-chat", 1_000_000, 1_000_000)
        self.assertGreater(c, 0)
        self.assertLess(c, 1.0)

    def test_unknown_model_uses_default(self):
        self.assertGreater(estimate_cost("whatever", 1000, 1000), 0)


class TestTranslator(unittest.TestCase):
    def _config(self, **kw):
        base = dict(provider="deepseek", api_key="k", batch_size=2, budget_usd=10.0)
        base.update(kw)
        return AIConfig(**base)

    def test_batches_and_accepts(self):
        # 英文名要够长，UTF-8 下才放得下中文（3 字节/汉字）
        payload = _json([
            {"en": "Amount", "zh": "数量"},
            {"en": "Radius", "zh": "半径"},
            {"en": "Threshold", "zh": "阈值"},
            {"en": "Intensity", "zh": "强度"},
        ])
        client = FakeClient([payload])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache())
        res = t.translate(["Amount", "Radius", "Threshold", "Intensity"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(res.progress.done, 4)
        self.assertEqual(res.progress.accepted, 4)

    def test_batches_split(self):
        """3 条 / batch_size=2 -> 2 批。

        ★ 2026-10-23：加了「条数校验 + 重试」后要小心：
          原测试让模型每次都只返回 1 条，而第 1 批有 2 条 ->
          条数不符触发重试 -> 总调用变 3 次。这不是 bug，是新保护。

          所以改成：payload 按批次给足条数（第 1 批 2 条、第 2 批 1 条），
          这个测试只验证「分批是否正确」，重试另由
          test_count_mismatch_retries 覆盖。
        """
        p1 = _json([{"en": "Amount", "zh": "数量"},
                    {"en": "Radius", "zh": "半径"}])
        p2 = _json([{"en": "Threshold", "zh": "阈值"}])
        client = FakeClient([p1, p2])
        t = Translator(self._config(batch_size=2), client=client, cache=Cache())
        res = t.translate(["Amount", "Radius", "Threshold"])
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(res.accepted_entries), 3)
        self.assertEqual(res.errors, [])

    def test_count_mismatch_retries(self):
        """模型漏条时必须自动重试一次（2026-10-23 治本改造）。"""
        bad = _json([{"en": "Amount", "zh": "数量"}])              # 只给 1/2 条
        good = _json([{"en": "Amount", "zh": "数量"},
                      {"en": "Radius", "zh": "半径"}])
        client = FakeClient([bad, good])
        t = Translator(self._config(batch_size=2), client=client, cache=Cache())
        res = t.translate(["Amount", "Radius"])
        self.assertEqual(client.calls, 2)          # 第一次 + 重试
        self.assertEqual(len(res.accepted_entries), 2)

    def test_short_name_rejected_by_length_gate(self):
        payload = _json([{"en": "Blur", "zh": "模糊"}])
        client = FakeClient([payload])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache(), world=WORLD_UTF8)
        res = t.translate(["Blur"])
        self.assertEqual(res.progress.rejected, 1)
        self.assertEqual(res.progress.accepted, 0)

    def test_default_pipeline_preserves_full_translations_in_draft_and_cache(self):
        payload = _json([{"en": "Opacity", "zh": "不透明度"}, {"en": "Blur", "zh": "模糊"}])
        client = FakeClient([payload])
        t = Translator(self._config(), client=client, cache=Cache())
        for _ in range(2):
            result = t.translate(["Opacity", "Blur"])
            self.assertEqual([e.used_zh for e in result.accepted_entries], ["不透明度", "模糊"])
            self.assertEqual(result.progress.shortened, 0)
            self.assertEqual(result.progress.rejected, 0)
        self.assertEqual(client.calls, 1)

    def test_legacy_short_translation_cache_is_not_reused(self):
        from unittest.mock import patch

        cache = Cache()
        payload = _json([{"en": "Opacity", "zh": "不透明度"}])
        client = FakeClient([payload])
        t = Translator(self._config(), client=client, cache=cache)
        with patch("core.ai.pipeline.PROMPT_VERSION", "p4"):
            old_key = Cache.key("Opacity", "", t._glossary_version(), t.config.model)
        cache.put(old_key, "不透明")
        result = t.translate(["Opacity"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.cache_hits, 0)
        self.assertEqual(result.accepted_entries[0].used_zh, "不透明度")

    def test_cache_prevents_second_call(self):
        payload = _json([{"en": "Amount", "zh": "数量"}])
        cache = Cache()
        client = FakeClient([payload])
        t = Translator(self._config(batch_size=10), client=client, cache=cache)
        t.translate(["Amount"])
        self.assertEqual(client.calls, 1)
        t2 = Translator(self._config(batch_size=10), client=client, cache=cache)
        res2 = t2.translate(["Amount"])
        self.assertEqual(client.calls, 1)          # 没再请求
        self.assertEqual(res2.cache_hits, 1)

    def test_budget_exceeded(self):
        payload = _json([{"en": "Amount", "zh": "数量"}])
        usage = Usage(prompt_tokens=10_000_000, completion_tokens=10_000_000, total_tokens=20_000_000)
        client = FakeClient([payload], usage=usage)
        t = Translator(self._config(budget_usd=0.001, batch_size=1), client=client, cache=Cache())
        with self.assertRaises(BudgetExceeded):
            t.translate(["Amount", "Blur"])

    def test_retry_on_retryable(self):
        payload = _json([{"en": "Amount", "zh": "数量"}])
        client = FakeClient([AIError("boom", retryable=True), payload])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache())
        res = t.translate(["Amount"])
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(res.errors), 0)

    def test_no_retry_on_fatal(self):
        client = FakeClient([AIError("bad key", retryable=False)])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache())
        res = t.translate(["A"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(res.errors), 1)

    def test_cancel(self):
        client = FakeClient([_json([{"en": "Amount", "zh": "数量"}])])
        t = Translator(self._config(batch_size=1), client=client, cache=Cache())
        t.cancel()
        with self.assertRaises(Cancelled):
            t.translate(["Amount", "Blur"])

    def test_progress_callback(self):
        seen = []
        payload = _json([{"en": "Amount", "zh": "数量"}])
        client = FakeClient([payload])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache())
        t.translate(["Amount"], on_progress=seen.append)
        self.assertTrue(seen)
        self.assertEqual(seen[-1].done, 1)

    def test_dedup_names(self):
        payload = _json([{"en": "Amount", "zh": "数量"}])
        client = FakeClient([payload])
        t = Translator(self._config(batch_size=10), client=client, cache=Cache())
        res = t.translate(["Amount", "Amount", "Amount"])
        self.assertEqual(res.progress.total, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
