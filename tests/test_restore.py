"""分层还原测试。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.restore import RestoreLayer, list_layers  # noqa: E402


class TestListLayers(unittest.TestCase):
    def test_returns_four_layers(self):
        ls = list_layers("")
        keys = {l.key for l in ls}
        self.assertIn("effect_names", keys)
        self.assertIn("param_names", keys)
        self.assertIn("utf8_fixer", keys)

    def test_all_have_label_and_desc(self):
        for l in list_layers(""):
            self.assertTrue(l.label, l.key)
            self.assertTrue(l.desc, l.key)

    def test_effect_names_always_available(self):
        """效果名总有办法还原（备份 或 matchName 重建）。"""
        for l in list_layers(""):
            if l.key == "effect_names":
                self.assertTrue(l.available)

    def test_fixer_status(self):
        for l in list_layers(r"C:\nonexistent_ae"):
            if l.key == "utf8_fixer":
                self.assertFalse(l.available)
                self.assertIn("未安装", l.status)


class TestRestoreLayer(unittest.TestCase):
    def test_defaults(self):
        l = RestoreLayer(key="x", label="X", desc="d")
        self.assertFalse(l.available)
        self.assertEqual(l.status, "")


if __name__ == "__main__":
    unittest.main()
