"""引擎词典 .dat 编解码测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.legacy.reader import read_dict_file  # noqa: E402
from core.legacy.writer import (  # noqa: E402
    decrypt_legacy, encrypt_legacy, write_dat,
)


class TestLegacyWriter(unittest.TestCase):
    def test_roundtrip_ascii(self):
        obj = {"Blur": "模糊", "Amount": "数量"}
        self.assertEqual(decrypt_legacy(encrypt_legacy(obj)), obj)

    def test_roundtrip_unicode(self):
        obj = {"S_Glow": "S_光晕", "BCC+Ambient Light": "BCC+环境光"}
        self.assertEqual(decrypt_legacy(encrypt_legacy(obj)), obj)

    def test_empty_dict(self):
        self.assertEqual(decrypt_legacy(encrypt_legacy({})), {})

    def test_bad_blob_returns_none(self):
        self.assertIsNone(decrypt_legacy(b"not zlib at all"))

    def test_write_dat(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "extra_overrides.dat")
            n = write_dat({"A": "甲"}, p)
            self.assertTrue(os.path.exists(p))
            self.assertEqual(n, os.path.getsize(p))
            with open(p, "rb") as f:
                self.assertEqual(decrypt_legacy(f.read()), {"A": "甲"})

    def test_written_dat_readable_by_engine_reader(self):
        """★ 关键：写出来的 .dat 必须能被引擎那套读取逻辑解开。

        引擎（WIN.py 的 _load_any）对三代格式逐级回退，最老的是
        「zlib + SHA256 计数器密钥流」。这里用本仓库的等价实现
        （core.legacy.reader）验证，保证不是自说自话。
        """
        obj = {"Load Preset": "加载预设", "Blur": "模糊"}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "extra_overrides.dat")
            write_dat(obj, p)
            got = read_dict_file(p)
            self.assertEqual(got, obj)

    def test_atomic_replace(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.dat")
            write_dat({"A": "甲"}, p)
            write_dat({"B": "乙"}, p)
            with open(p, "rb") as f:
                self.assertEqual(decrypt_legacy(f.read()), {"B": "乙"})
            self.assertFalse(os.path.exists(p + ".tmp"))
