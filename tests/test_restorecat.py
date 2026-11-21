"""分类名还原测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.restorecat import translate_category  # noqa: E402


class TestTranslateCategory(unittest.TestCase):
    def test_sapphire(self):
        self.assertEqual(translate_category("Sapphire 调整"), "Sapphire Adjust")
        self.assertEqual(translate_category("Sapphire 灯光"), "Sapphire Lighting")
        self.assertEqual(translate_category("Sapphire 变形"), "Sapphire Distort")

    def test_bcc(self):
        self.assertEqual(translate_category("BCC 模糊"), "BCC Blur")
        self.assertEqual(translate_category("BCC 色彩校正"),
                         "BCC Color Correction")
        self.assertEqual(translate_category("BCC 扭曲"), "BCC Distort")

    def test_bcc_plus(self):
        self.assertEqual(translate_category("BCC+ 模糊"), "BCC+ Blur")

    def test_unknown_returns_empty(self):
        """推不出来的返回空串，不硬猜。"""
        self.assertEqual(translate_category("未知分类"), "")
        self.assertEqual(translate_category("Sapphire 不存在的分类"), "")

    def test_english_passthrough(self):
        """已经是英文的不动。"""
        self.assertEqual(translate_category("Sapphire Adjust"), "")
        self.assertEqual(translate_category(""), "")

    def test_whitespace_trimmed(self):
        self.assertEqual(translate_category("  Sapphire 调整  "),
                         "Sapphire Adjust")


if __name__ == "__main__":
    unittest.main()
