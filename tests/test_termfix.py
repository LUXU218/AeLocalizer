# -*- coding: utf-8 -*-
"""术语修正层（term_fix）测试。

★ 为什么要有这一层（2026-10-30 真机实测）：
  守护（WIN.build_dict）按固定顺序合并多层词典，先到先得：
      term_fix -> extra_overrides -> dict_full_groundtruth
               -> param_names_zh -> dict_ae_groundtruth -> all_plugins
  我们生成的目录里只有 extra_overrides，历史错译每次重建都会重新冒出来。
  所以必须在 term_fix 这一层把它们钉住，并且——这条最关键——
  **构建词典时要每次都重写一遍**，否则用户点一次"重建"就退回错译。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import termfix  # noqa: E402
from core.enginedict import build_engine_dict, clear_engine_dict  # noqa: E402
from core.legacy.reader import LAYER_ORDER, load_legacy_layer  # noqa: E402

#: 实测抓到的错译（AI 历史产出 / 早期规则表），用来证明修正真的换掉了它们
KNOWN_BAD = {
    "Glow": "发光",
    "Glow Width": "发光宽度",
    "Ambient": "周围",
    "Occlusion": "遮蔽",
    "Specular": "镜像",
    "Velocity": "速率",
    "Blur Res": "模糊品质",
    "Shutter Duration": "拖尾长度",
    "NR Passes": "降噪",
    "NR Color Strength": "降噪",
    "Force Scene Reset": "强制",
    "Size": "字数",
    "Last parameter": "末个 parameter",
    "Upsampling Method": "Upsampling 方法",
    "Wrap X": "X 环绕",
    "Wrap Y": "Y 环绕",
}


def engine_merge(dict_dir):
    """按 guard_uni / WIN.build_dict 的真实顺序模拟合并（setdefault 先到先得）。"""
    src = {}
    for name in LAYER_ORDER:
        for k, v in load_legacy_layer(dict_dir, name).items():
            src.setdefault(k, v)
    return src


class TestTermFixTable(unittest.TestCase):
    def test_table_not_empty_and_changes_values(self):
        self.assertGreaterEqual(len(termfix.TERM_FIXES), 40)
        for en, zh in termfix.TERM_FIXES.items():
            self.assertTrue(en and zh, en)
            self.assertNotEqual(en, zh, en)

    def test_fixes_beat_known_bad_values(self):
        for en, bad in KNOWN_BAD.items():
            self.assertIn(en, termfix.TERM_FIXES, "缺 %s 的修正" % en)
            self.assertNotEqual(termfix.TERM_FIXES[en], bad, "%s 还是错的" % en)

    def test_extra_entries_override_builtin(self):
        m = termfix.merged({"Glow": "自定义辉光"})
        self.assertEqual(m["Glow"], "自定义辉光")
        self.assertEqual(m["Ambient"], "环境")


class TestTermFixFiles(unittest.TestCase):
    def test_write_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            n = termfix.write_term_fix(td)
            self.assertEqual(n, len(termfix.TERM_FIXES))
            self.assertTrue(os.path.exists(os.path.join(td, termfix.TERM_FIX_JSON)))
            self.assertTrue(os.path.exists(os.path.join(td, termfix.TERM_FIX_DAT)))
            got = termfix.read_term_fix(td)
            self.assertEqual(got.get("Glow"), "辉光")

    def test_dat_is_readable_by_engine_reader(self):
        with tempfile.TemporaryDirectory() as td:
            termfix.write_term_fix(td)
            layer = load_legacy_layer(td, "term_fix")
            self.assertEqual(layer.get("Size Red"), "红色大小")

    def test_clear_removes_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            termfix.write_term_fix(td)
            self.assertTrue(termfix.clear_term_fix(td))
            self.assertFalse(os.path.exists(os.path.join(td, termfix.TERM_FIX_DAT)))


class TestBuildWritesTermFix(unittest.TestCase):
    def test_build_engine_dict_writes_term_fix(self):
        with tempfile.TemporaryDirectory() as td:
            res = build_engine_dict({"Blur": "模糊"}, td)
            self.assertGreaterEqual(res.term_fix, 40)
            self.assertTrue(os.path.exists(os.path.join(td, termfix.TERM_FIX_DAT)))

    def test_term_fix_wins_over_extra_overrides(self):
        """核心断言：extra_overrides 里是错的，合并结果仍必须是修正后的。"""
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"Glow": "发光", "Ambient": "周围"}, td)
            merged = engine_merge(td)
            self.assertEqual(merged["Glow"], "辉光")
            self.assertEqual(merged["Ambient"], "环境")

    def test_clear_engine_dict_also_drops_term_fix(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"Blur": "模糊"}, td)
            clear_engine_dict(td)
            self.assertFalse(os.path.exists(os.path.join(td, termfix.TERM_FIX_DAT)))
            self.assertFalse(os.path.exists(os.path.join(td, termfix.TERM_FIX_JSON)))


class TestGatesRejectKnownBad(unittest.TestCase):
    def test_blacklist_catches_history_errors(self):
        from core.ai import gates

        for en, bad in KNOWN_BAD.items():
            hit, _why = gates.check_blacklist(en, bad)
            self.assertTrue(hit, "闸没拦住 %s -> %s" % (en, bad))

    def test_good_translation_passes(self):
        from core.ai import gates

        hit, _why = gates.check_blacklist("Glow", "辉光")
        self.assertFalse(hit)


class TestEnsureTermFix(unittest.TestCase):
    """升级用户不重建词典也要生效：启动引擎前补一次修正层。"""

    def test_writes_when_missing(self):
        d = tempfile.mkdtemp()
        n = termfix.ensure_term_fix(d)
        self.assertEqual(n, len(termfix.merged()))
        self.assertTrue(os.path.exists(os.path.join(d, termfix.TERM_FIX_DAT)))
        self.assertEqual(termfix.read_term_fix(d), termfix.merged())

    def test_idempotent(self):
        d = tempfile.mkdtemp()
        self.assertGreater(termfix.ensure_term_fix(d), 0)
        self.assertEqual(termfix.ensure_term_fix(d), 0)

    def test_rewrites_when_stale(self):
        d = tempfile.mkdtemp()
        termfix.write_term_fix(d, {"Glow": "发光"})  # 旧内容
        self.assertGreater(termfix.ensure_term_fix(d), 0)
        self.assertEqual(termfix.read_term_fix(d)["Glow"], "辉光")

    def test_missing_dir_is_noop(self):
        self.assertEqual(termfix.ensure_term_fix(os.path.join(tempfile.mkdtemp(), "nope")), 0)


if __name__ == "__main__":
    unittest.main()
