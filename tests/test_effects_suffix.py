# -*- coding: utf-8 -*-
"""AE 重复实例后缀（'CC Light Rays 2'）的译名解析回归测试。

背景（真机实测，AE 2022 / 2025 / 2026 三版一致）：
  同一个效果往同一图层加第二份时，AE 自动把显示名改成
  「原名 + U+0020 + 十进制序号」—— 第二份叫 'CC Light Rays 2'，第三份
  'CC Light Rays 3'。词典的键只有无序号的原名，于是第二份起精确查表落空，
  导致同一效果的后续实例仍显示英文。

修法（纯追加，不改既有查表逻辑）：
  1. 先原样调 resolve_effect_name —— 命中即返回，既有行为一个字不变；
  2. 未命中才剥后缀重查，命中后把后缀原样拼回。

★ 为什么不能反过来（先剥后缀）：
  'BCC Noise Map 2'、'Primatte Keyer 6'、'CC Light Rays 2' 之外的
  'Depth Scanner 2' 这类**真实产品名本身带数字**，它们字面就在词典里。
  先剥后缀会把它们错拆成 'BCC Noise Map' / 'Primatte Keyer 6' 的 base，
  译名跟着错。必须先精确查。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import liveparams  # noqa: E402
from core.effects import (  # noqa: E402
    resolve_effect_name,
    resolve_effect_name_instance,
    split_instance_suffix,
)


class TestSplitInstanceSuffix(unittest.TestCase):
    def test_plain_suffix(self):
        self.assertEqual(split_instance_suffix("CC Light Rays 2"),
                         ("CC Light Rays", " 2"))
        self.assertEqual(split_instance_suffix("CC Light Rays 12"),
                         ("CC Light Rays", " 12"))

    def test_no_suffix(self):
        for name in ("CC Light Rays", "BCC+Fluorescent", "S_WarpPolar"):
            self.assertEqual(split_instance_suffix(name), (name, ""))

    def test_cjk_base_suffix(self):
        """已汉化的工程里再加一份，AE 用中文显示名当 base。"""
        self.assertEqual(split_instance_suffix("CC 光线 2"), ("CC 光线", " 2"))

    def test_empty(self):
        self.assertEqual(split_instance_suffix(""), ("", ""))


class TestResolveEffectNameInstance(unittest.TestCase):
    TBL = {
        "CC Light Rays": "CC 光线",
        "Gaussian Blur": "高斯模糊",
        "BCC Noise Map 2": "BCC 噪波图 2",      # 真实名本身带数字
        "Primatte Keyer 6": "Primatte 抠像 6",  # 真实名本身带数字
        "Blur": "模糊",
    }

    def test_second_instance_resolves(self):
        self.assertEqual(
            resolve_effect_name_instance("CC Light Rays 2", "", self.TBL), "CC 光线 2")
        self.assertEqual(
            resolve_effect_name_instance("CC Light Rays 3", "", self.TBL), "CC 光线 3")

    def test_first_instance_unchanged(self):
        self.assertEqual(
            resolve_effect_name_instance("CC Light Rays", "", self.TBL), "CC 光线")

    def test_exact_wins_over_strip(self):
        """字面存在时精确命中优先 —— 不能被拆成别的东西。"""
        self.assertEqual(
            resolve_effect_name_instance("BCC Noise Map 2", "", self.TBL),
            "BCC 噪波图 2")
        self.assertEqual(
            resolve_effect_name_instance("Primatte Keyer 6", "", self.TBL),
            "Primatte 抠像 6")
        # 与旧函数完全一致
        self.assertEqual(
            resolve_effect_name("BCC Noise Map 2", "", self.TBL),
            resolve_effect_name_instance("BCC Noise Map 2", "", self.TBL))

    def test_prefix_preserved_through_suffix(self):
        """S_ 前缀在「剥后缀 → 查表 → 拼回」的每一段都要保住。"""
        self.assertEqual(
            resolve_effect_name_instance("S_WarpPolar 2", "", {"WarpPolar": "极坐标扭曲"}),
            "S_极坐标扭曲 2")

    def test_matchname_also_split(self):
        self.assertEqual(
            resolve_effect_name_instance("Foo 2", "CC Light Rays 2", self.TBL),
            "CC 光线 2")

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve_effect_name_instance("Nope 2", "", self.TBL))

    def test_already_cjk_not_rewritten(self):
        """已汉化的工程里 AE 生成的中文底名字不重译（避免二次改写）。"""
        self.assertIsNone(resolve_effect_name_instance("CC 光线 2", "", self.TBL))


class TestNameTableInstanceSuffix(unittest.TestCase):
    """NameTable.translate 的后缀兜底：长度闸必须按拼回后缀后的文案判。"""

    def test_second_instance(self):
        nt = liveparams.NameTable(None, {"CC Light Rays": "CC 光线"})
        self.assertEqual(nt.translate("CC Light Rays 2"), "CC 光线 2")

    def test_length_gate_uses_full_text(self):
        # "模糊" + " 2" = 4 字符 <= "Blur 2"(6) → 允许
        nt = liveparams.NameTable(None, {"Blur": "模糊"})
        self.assertEqual(nt.translate("Blur 2"), "模糊 2")

    def test_too_long_still_rejected(self):
        # "Ab" → "中文名字" 已经超长，带后缀更超长 → 不换
        nt = liveparams.NameTable(None, {"Ab": "中文名字"})
        self.assertEqual(nt.translate("Ab 2"), "")

    def test_exact_first(self):
        nt = liveparams.NameTable(None, {"BCC Noise Map 2": "BCC 噪波图 2",
                                        "BCC Noise Map": "错误译文"})
        self.assertEqual(nt.translate("BCC Noise Map 2"), "BCC 噪波图 2")


if __name__ == "__main__":
    unittest.main()
