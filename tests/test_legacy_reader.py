"""旧词典读取测试：与引擎 WIN.py 交叉验证（有旧工程树时才跑）。"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.legacy.reader import LegacyDictError, load_legacy_layer, read_dict_file  # noqa: E402

DICT_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "SapphireCN", "dict")
LEGACY_WIN = Path(os.environ.get("AELOC_LEGACY_TEST_LOADER", "WIN.py"))

HAS_DICT = bool(os.environ.get("AELOC_LEGACY_DICT_KEY")) and os.path.isdir(DICT_DIR)
HAS_WIN = LEGACY_WIN.is_file()


@unittest.skipUnless(HAS_DICT, "本机无旧词典目录")
class TestLegacyReader(unittest.TestCase):
    def test_reads_a_layer(self):
        data = load_legacy_layer(DICT_DIR, "extra_overrides")
        self.assertGreater(len(data), 1000)
        for k, v in list(data.items())[:50]:
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, str)

    def test_missing_layer_is_empty(self):
        self.assertEqual(load_legacy_layer(DICT_DIR, "definitely_not_here"), {})

    def test_bad_file_raises(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
            f.write(b"LXAE2" + b"\x00" * 40)
            p = f.name
        try:
            with self.assertRaises(LegacyDictError):
                read_dict_file(p)
        finally:
            os.remove(p)

    @unittest.skipUnless(HAS_WIN, "本机无旧 WIN.py")
    def test_matches_engine_loader(self):
        """★ 关键：独立实现必须与引擎 WIN.py 的解密结果逐条一致。"""
        import importlib.util

        os.environ["SAPPHIRE_CN_DICT"] = DICT_DIR
        spec = importlib.util.spec_from_file_location("legacy_WIN_probe", str(LEGACY_WIN))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for layer in ("extra_overrides", "param_names_zh", "all_plugins"):
            engine = mod._load_any(layer)
            mine = load_legacy_layer(DICT_DIR, layer)
            self.assertEqual(mine, engine, f"{layer} 与引擎结果不一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)
