"""枚举词典机制测试（下拉值通道）。

★ 这两条是实测踩过的坑，必须用测试固定住：
  ① 引擎的枚举通道按【单词】查表，不是整串 -> 整串条目对下拉值无效
  ② 设置 SAPPHIRE_CN_DICT 后，枚举词典必须放到我们的目录，
     否则只剩硬编码 338 条（原版 11713 条）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.enginedict import (  # noqa: E402
    DICT_DAT, DICT_FILE, ENUM_DAT, build_engine_dict, clear_engine_dict,
    engine_dict_ready, enum_dict_ready,
)


class TestEnumDict(unittest.TestCase):
    def test_build_writes_enum_dict(self):
        with tempfile.TemporaryDirectory() as td:
            res = build_engine_dict({"Blur": "模糊"}, td)
            self.assertTrue(os.path.exists(os.path.join(td, ENUM_DAT)))
            self.assertTrue(res.enum_ready)
            self.assertGreater(res.enum_entries, 0)

    def test_enum_dict_contains_words(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"Texture Only": "仅纹理", "Mult": "相乘"}, td)
            from core.legacy.reader import read_dict_file

            d = read_dict_file(os.path.join(td, ENUM_DAT))
            self.assertEqual(d.get("Texture Only"), "仅纹理")
            self.assertEqual(d.get("Mult"), "相乘")

    def test_pipe_entries_excluded_from_enum(self):
        """整串条目不能进枚举词典 —— 枚举通道按单词查表。"""
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"A|B|C": "甲|乙|丙", "Blur": "模糊"}, td)
            from core.legacy.reader import read_dict_file

            d = read_dict_file(os.path.join(td, ENUM_DAT))
            self.assertNotIn("A|B|C", d)
            self.assertEqual(d.get("Blur"), "模糊")

    def test_enum_ready_zero_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(enum_dict_ready(td), 0)

    def test_clear_removes_enum(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"A": "甲"}, td)
            self.assertGreater(enum_dict_ready(td), 0)
            clear_engine_dict(td)
            self.assertEqual(enum_dict_ready(td), 0)

    def test_summary_mentions_enum(self):
        with tempfile.TemporaryDirectory() as td:
            res = build_engine_dict({"A": "甲"}, td)
            self.assertIn("下拉值", res.summary())


class TestDictFiles(unittest.TestCase):
    def test_writes_both_json_and_dat(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"A": "甲"}, td)
            self.assertTrue(os.path.exists(os.path.join(td, DICT_FILE)))
            self.assertTrue(os.path.exists(os.path.join(td, DICT_DAT)))

    def test_engine_dict_ready_reads_dat(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"A": "甲", "B": "乙"}, td)
            self.assertEqual(engine_dict_ready(td), 2)


if __name__ == "__main__":
    unittest.main()
