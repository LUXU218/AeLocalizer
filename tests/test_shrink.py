"""译名缩短测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.shrink import candidates, gbk_len, shortest_fitting  # noqa: E402


class TestShrink(unittest.TestCase):
    def test_gbk_len(self):
        self.assertEqual(gbk_len("abc"), 3)
        self.assertEqual(gbk_len("中文"), 4)
        self.assertIsNone(gbk_len("\u2122"))  # ™ 不在 GBK

    def test_fits_as_is(self):
        self.assertEqual(shortest_fitting("色相", 4), "色相")

    def test_shortens(self):
        out = shortest_fitting("不透明度", 6)
        self.assertIsNotNone(out)
        self.assertLessEqual(gbk_len(out), 6)

    def test_impossible_returns_none(self):
        self.assertIsNone(shortest_fitting("完全不透明度调节", 2))

    def test_candidates_are_unique(self):
        cs = candidates("显示不透明度调整")
        self.assertEqual(len(cs), len(set(cs)))

    def test_strips_spaces(self):
        out = shortest_fitting("发光 阈值", 8)
        self.assertIsNotNone(out)
        self.assertNotIn(" ", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
