"""导出器测试：临时构造旧词典目录，验证合并/过滤/产物。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dictpack import read_pack  # noqa: E402
from core.dictpack.pack import verify_pack  # noqa: E402
from core.export import BAD_TRANSLATIONS, collect_layers, export_pack  # noqa: E402


def _write_layer(d: str, name: str, obj: dict) -> None:
    with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dict_dir = os.path.join(self.tmp, "dict")
        os.makedirs(self.dict_dir)

    def test_priority_merge(self):
        # term_fix 优先于 all_plugins（与引擎 LAYER_ORDER 一致）
        _write_layer(self.dict_dir, "term_fix", {"Glow": "发光"})
        _write_layer(self.dict_dir, "all_plugins", {"Glow": "光晕", "Blur": "模糊"})
        merged, counts = collect_layers(self.dict_dir)
        self.assertEqual(merged["Glow"]["zh"], "发光")
        self.assertEqual(merged["Glow"]["layer"], "term_fix")
        self.assertEqual(merged["Blur"]["zh"], "模糊")

    def test_filters(self):
        _write_layer(
            self.dict_dir,
            "term_fix",
            {
                "Same": "Same",          # 译名==原文 → 剔除
                "NoCjk": "Plain",        # 无中文 → 剔除
                "Good": "好",             # 保留
                "1 点：位置": "1 点：位置",  # 键含中文 → 剔除
                "Right": BAD_TRANSLATIONS["Right"],  # 黑名单 → 剔除
            },
        )
        out = os.path.join(self.tmp, "t.aeloc")
        rep = export_pack(self.dict_dir, out, name="t", version="1")
        self.assertEqual(rep.kept, 1)
        self.assertEqual(len(rep.dropped_identity), 1)
        self.assertEqual(len(rep.dropped_blacklist), 1)
        self.assertEqual(len(rep.dropped_cjk_key), 1)

    def test_produces_valid_pack(self):
        _write_layer(self.dict_dir, "term_fix", {"Glow Threshold": "发光阈值"})
        out = os.path.join(self.tmp, "t.aeloc")
        rpt = os.path.join(self.tmp, "t.report.md")
        export_pack(self.dict_dir, out, name="测试", version="2.0.0", report_path=rpt)
        with open(out, "rb") as f:
            pack = read_pack(f.read())
        self.assertEqual(pack.name, "测试")
        self.assertEqual(pack.version, "2.0.0")
        self.assertEqual(pack.entries["Glow Threshold"]["zh"], "发光阈值")
        ok, msg = verify_pack(pack)
        self.assertTrue(ok, msg)
        self.assertTrue(os.path.exists(rpt))
        with open(rpt, encoding="utf-8") as f:
            self.assertIn("导出报告", f.read())

    def test_too_long_flagged(self):
        _write_layer(self.dict_dir, "term_fix", {"Hue": "色相"})  # 3 字节可用，译名 4 字节
        out = os.path.join(self.tmp, "t.aeloc")
        rep = export_pack(self.dict_dir, out, name="t", version="1")
        self.assertEqual(len(rep.too_long) + len(rep.shortened), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
