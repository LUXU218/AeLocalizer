# -*- coding: utf-8 -*-
"""dllpatch 还原功能测试(2026-10-29 起 dllpatch 只剩还原)。

写入路径(plan_dll/apply_dll/质量闸)已随 BCC 实时汉化(交接文档 §5.27)移除,
磁盘 DLL 恢复英文原版后不再改写;这里只测「从原厂备份还原」的行为。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import mock

from core import dllpatch  # noqa: E402

ORIGINAL = b"ORIGINAL-ENGLISH-IMAGE\x00" * 4
NAME = "Continuum_AE_8Bit.dll"


class TestLooksLocalized(unittest.TestCase):
    def test_chinese_detected(self):
        self.assertTrue(dllpatch._looks_localized("灯光".encode("gbk")))

    def test_english_clean(self):
        self.assertFalse(dllpatch._looks_localized(b"plain ascii dll"))


class TestRestore(unittest.TestCase):
    def _make_target(self, td):
        """造一个被汉化的目标文件,返回其路径。"""
        target = os.path.join(td, "target", NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(b"HANZI-LOCALIZED-IMAGE\x00" * 4)
        return target

    def test_restore_from_local_backup(self):
        """dll_backup 里的【英文】副本可以当还原源。"""
        with tempfile.TemporaryDirectory() as td,                 mock.patch.object(dllpatch, "KNOWN_ORIGINALS", {}):
            target = self._make_target(td)
            broot = os.path.join(td, "dll_backup")
            os.makedirs(broot, exist_ok=True)
            with open(os.path.join(broot, NAME), "wb") as f:
                f.write(ORIGINAL)
            ok, msg = dllpatch.restore_dll(target, broot)
            self.assertTrue(ok, msg)
            self.assertIn("本地备份", msg)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), ORIGINAL)

    def test_restore_refuses_localized_backup(self):
        """dll_backup 里是【汉化版】(含中文)时必须拒绝 —— 2026-10-22 实翻车。"""
        with tempfile.TemporaryDirectory() as td,                 mock.patch.object(dllpatch, "KNOWN_ORIGINALS", {}):
            target = self._make_target(td)
            broot = os.path.join(td, "dll_backup")
            os.makedirs(broot, exist_ok=True)
            with open(os.path.join(broot, NAME), "wb") as f:
                f.write(b"GBKBOM\x00" + "灯光".encode("gbk") + b"\x00")
            ok, msg = dllpatch.restore_dll(target, broot)
            self.assertFalse(ok)
            self.assertIn("汉化版", msg)

    def test_restore_without_any_backup(self):
        ok, msg = dllpatch.restore_dll(r"Z:\nope\Continuum_AE_8Bit.dll",
                                       r"Z:\nobak")
        self.assertFalse(ok)
        self.assertIn("找不到", msg)


if __name__ == "__main__":
    unittest.main()
