"""编码层测试：两套编码世界 + 三条通道 + 长度闸。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.encoder import (  # noqa: E402
    ST_OK, ST_SHORTENED, ST_TOO_LONG, ST_UNENCODABLE,
    SSO_INLINE_MAX_CHARS, CH_ANSI, CH_PIPL, CH_RDATA,
    STRATEGY_GBK_FIXER, STRATEGY_SKIP, STRATEGY_UTF8_NATIVE,
    WORLD_ANSI, WORLD_UTF8,
    byte_len, choose_strategy, describe, encode_text, plan_all, plan_ansi,
    plan_for_strategy, plan_rdata, plan_utf16, probe_slack, sso_inline_for,
)

class TestEncodeText(unittest.TestCase):
    def test_ansi_vs_utf8_bytes(self):
        self.assertEqual(byte_len("色相", WORLD_ANSI), 4)
        self.assertEqual(byte_len("色相", WORLD_UTF8), 6)

    def test_unencodable_in_gbk(self):
        self.assertIsNone(encode_text("\u2122", WORLD_ANSI))  # (TM)
        self.assertEqual(encode_text("\u2122", WORLD_UTF8), b"\xe2\x84\xa2")

    def test_unknown_world(self):
        with self.assertRaises(ValueError):
            encode_text("a", "latin1")


class TestPlanAnsi(unittest.TestCase):
    def test_fits(self):
        p = plan_ansi("Hue", "色相", WORLD_ANSI)  # 可用 3，GBK 4
        self.assertIn(p.status, (ST_SHORTENED, ST_TOO_LONG))

    def test_fits_exactly(self):
        p = plan_ansi("Amount", "数量", WORLD_ANSI)  # 可用 6，GBK 4
        self.assertEqual(p.status, ST_OK)
        self.assertEqual(p.payload, "数量".encode("gbk"))

    def test_utf8_world_needs_more(self):
        p = plan_ansi("Amount", "数量", WORLD_UTF8)
        self.assertEqual(p.status, ST_OK)
        self.assertEqual(p.payload, "数量".encode("utf-8"))

    def test_shortened(self):
        p = plan_ansi("Opacity", "不透明度", WORLD_ANSI)  # 可用 7，GBK 8 -> 需缩短
        self.assertEqual(p.status, ST_SHORTENED)
        self.assertTrue(p.ok)
        self.assertLessEqual(len(p.payload), p.avail_bytes)

    def test_too_long(self):
        p = plan_ansi("X", "完全不透明度调节", WORLD_ANSI)
        self.assertEqual(p.status, ST_TOO_LONG)
        self.assertFalse(p.ok)

    def test_unencodable(self):
        p = plan_ansi("Copyright", "版权\u2122", WORLD_ANSI)
        self.assertEqual(p.status, ST_UNENCODABLE)

    def test_avail_override(self):
        p = plan_ansi("Whatever", "中文", WORLD_ANSI, avail=100)
        self.assertEqual(p.status, ST_OK)


class TestSso(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(sso_inline_for("a" * 7))
        self.assertFalse(sso_inline_for("a" * 8))
        self.assertEqual(SSO_INLINE_MAX_CHARS, 7)

    def test_utf16_plan(self):
        p = plan_utf16("偏移 X")
        self.assertEqual(p.status, ST_OK)
        self.assertEqual(p.utf16_len, 4)
        self.assertEqual(len(p.payload), 8)

    def test_empty_utf16(self):
        self.assertEqual(plan_utf16("").status, ST_UNENCODABLE)


class TestRdata(unittest.TestCase):
    def test_always_gbk(self):
        p = plan_rdata("Amount", "数量")
        self.assertEqual(p.world, WORLD_ANSI)
        self.assertEqual(p.payload, "数量".encode("gbk"))

    def test_too_long_mentions_rdata(self):
        p = plan_rdata("X", "完全不透明度调节")
        self.assertEqual(p.status, ST_TOO_LONG)
        self.assertIn("rdata", p.reason)


class TestStrategy(unittest.TestCase):
    def test_ansi_world_uses_gbk_native(self):
        self.assertEqual(choose_strategy("Amount", "数量", WORLD_ANSI), "gbk_native")

    def test_utf8_world_writes_utf8_directly(self):
        """★ 更正后的关键约束：UTF-8 世界里必须直接写 UTF-8。

        不能"写 GBK 让修复层转码"—— 修复层不扩大缓冲区，转码后更长会被截断丢字。
        """
        self.assertEqual(
            choose_strategy("Glow Threshold", "发光阈值", WORLD_UTF8), STRATEGY_UTF8_NATIVE
        )
        # 修复层是否可用，都不改变主通道的写法
        self.assertEqual(
            choose_strategy("Glow Threshold", "发光阈值", WORLD_UTF8, fixer_available=True),
            STRATEGY_UTF8_NATIVE,
        )

    def test_rdata_and_pipl_always_gbk(self):
        """★ 真机实测（AE MCP 2026-10-20）：.rdata 与 .aex PiPL 都恒为 GBK。

        写 UTF-8 会被 AE 当 GBK 解码 -> 乱码（"通道切换" -> "閫氶亾鍒囨崲"）。
        """
        from core.encoder import CH_PIPL

        for ch in (CH_RDATA, CH_PIPL):
            self.assertEqual(
                choose_strategy("Amount", "数量", WORLD_UTF8, channel=ch,
                                fixer_available=True),
                "gbk_native",
                msg="通道 %s 必须写 GBK" % ch,
            )

    def test_skip_when_impossible(self):
        self.assertEqual(
            choose_strategy("X", "完全不透明度调节", WORLD_UTF8), STRATEGY_SKIP
        )

    def test_slack_rescues_entries(self):
        """运行时余量能救回"按原 ASCII 长度算不够"的条目。"""
        self.assertEqual(choose_strategy("Hue", "色相", WORLD_UTF8), STRATEGY_SKIP)
        self.assertEqual(
            choose_strategy("Hue", "色相", WORLD_UTF8, slack=4), STRATEGY_UTF8_NATIVE
        )

    def test_plan_for_strategy_gbk_payload(self):
        p = plan_for_strategy("Amount", "数量", WORLD_ANSI, "gbk_native")
        self.assertEqual(p.payload, "数量".encode("gbk"))

    def test_plan_for_strategy_utf8_payload(self):
        p = plan_for_strategy("Amount", "数量", WORLD_UTF8, STRATEGY_UTF8_NATIVE)
        self.assertEqual(p.payload, "数量".encode("utf-8"))

    def test_probe_slack(self):
        buf = b"Amount" + b"\x00" * 8 + b"X"
        self.assertEqual(probe_slack(buf, 6), 7)   # 8 个 0，留 1 个给终止符
        self.assertEqual(probe_slack(b"Amount\x00", 6), 0)
        self.assertEqual(probe_slack(b"AmountXY", 6), 0)


class TestRuntimeFinding(unittest.TestCase):
    """★ 2026-10-20 真机实测结论固化（AE 26.5x89，frida 探针）。"""

    def test_chinese_gbk_bytes_are_what_ae_reads(self):
        """采到的 '\xd7\xd4\xb6\xaf' 正是「自动」的 GBK 编码。

        这证明 AE 2026 运行期参数名读入用的是 GBK，不是 UTF-8。
        """
        raw = bytes([0xD7, 0xD4, 0xB6, 0xAF])
        self.assertEqual(raw.decode("gbk"), "自动")
        self.assertEqual("自动".encode("gbk"), raw)
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")

    def test_all_channels_default_to_gbk(self):
        """三条通道（PiPL / rdata / 参数名）中文链路都必须 GBK。"""
        from core.encoder import CH_PIPL, CH_RDATA

        for ch in (CH_ANSI, CH_PIPL, CH_RDATA):
            self.assertEqual(
                choose_strategy("Amount", "数量", WORLD_ANSI, channel=ch),
                "gbk_native",
                msg="通道 %s 应为 GBK" % ch,
            )


class TestPlanAll(unittest.TestCase):
    def test_four_channels(self):
        d = plan_all("Amount", "数量", WORLD_UTF8)
        self.assertEqual(set(d), {"ansi", "utf16", "pipl", "rdata"})

    def test_describe(self):
        # 2026-11-15：describe 改成给「运行时能不能装下」的结论，不再列编码细节。
        self.assertIn("可写入", describe("Amount", "数量", WORLD_ANSI))
        self.assertEqual(describe("Amount", "", WORLD_ANSI), "无译文")
        # 中文比英文原名长 → 明确报超出字节数（运行时可能被截断）
        long = describe("On", "这是一个很长的译名", WORLD_ANSI)
        self.assertIn("超出", long)
        self.assertIn("截断", long)


if __name__ == "__main__":
    unittest.main(verbosity=2)
