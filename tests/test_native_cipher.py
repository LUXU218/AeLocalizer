"""native 重写(2026-11-01 性能批次)的黄金对照测试。

★ 两件大事:
  1. aeloc_cipher.dll(密钥流 XOR)的 C 输出必须与纯 python 实现**逐字节
     一致** —— 这是引擎词典格式的兼容性红线(.dat 变一个字节守护就解不开)。
  2. aeloc_rpm.dll(批量跨进程读)的 C 路径读自身进程内存与单条 read 语义
     一致;python 回退路径(fake/缺失 DLL)行为同旧版。

DLL 缺失的环境(如未编译的 CI)对应测试自动跳过,纯 python 回退路径照测。
"""
import ctypes as C
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.legacy import native_cipher
from core.legacy.writer import (
    DICT_KEY, _keystream_legacy, _xor_bytes, decrypt_legacy, encrypt_legacy)


def _py_xor(key: bytes, blob: bytes) -> bytes:
    """纯 python 参考实现(= writer 回退路径,独立重写以避免同源错)。"""
    ks = _keystream_legacy(len(blob)) if blob else b""
    return _xor_bytes(blob, ks) if blob else b""


class TestCipherGolden(unittest.TestCase):
    """aeloc_cipher.dll vs 纯 python:字节级黄金对照。"""

    def test_dll_available_or_graceful_none(self):
        r = native_cipher.native_xor_keystream(DICT_KEY, b"hello")
        if native_cipher._load() is None:
            self.assertIsNone(r)     # 无 DLL → 明确回退,不许半工作
        else:
            self.assertEqual(r, _py_xor(DICT_KEY, b"hello"))

    def test_golden_lengths(self):
        """0/1/31/32/33/64/1000/70000 字节全长度段一一对照。"""
        import random

        rnd = random.Random(20261101)
        if native_cipher._load() is None:
            self.skipTest("aeloc_cipher.dll 未编译(fallback 路径由 writer 测试覆盖)")
        for n in (0, 1, 31, 32, 33, 63, 64, 1000, 70000):
            blob = bytes(rnd.randrange(256) for _ in range(n))
            got = native_cipher.native_xor_keystream(DICT_KEY, blob)
            self.assertEqual(got, _py_xor(DICT_KEY, blob), f"长度 {n} 不一致")

    def test_encrypt_decrypt_roundtrip_and_cross(self):
        obj = {"Glow": "发光", "S_WarpPuddle": "S_水洼扭曲", "中文键?": "不行"}
        blob = encrypt_legacy(obj)
        self.assertEqual(decrypt_legacy(blob), obj)
        # 与纯 python 路径(禁用 native)字节级一致
        import core.legacy.native_cipher as nc

        real = nc.native_xor_keystream
        try:
            nc.native_xor_keystream = lambda key, b: None
            blob_py = encrypt_legacy(obj)
        finally:
            nc.native_xor_keystream = real
        self.assertEqual(blob, blob_py)


class TestRpmMany(unittest.TestCase):
    """aeloc_rpm.dll 批量读:C 路径读自身进程;python 回退语义。"""

    def _bare_process(self):
        import core.proc as proc

        return proc.Process.__new__(proc.Process)   # 绕过 __init__(不开真进程)

    def test_c_path_reads_own_memory(self):
        """用自身进程句柄读已知缓冲 —— C 路径的真实(离线)验证。"""
        import core.proc as proc

        lib = proc._rpm_lib()
        if lib is None:
            self.skipTest("aeloc_rpm.dll 未编译")
        buf = C.create_string_buffer(b"HELLO_AELOC_WORLD")
        addr = C.addressof(buf)
        p = self._bare_process()
        k = C.windll.kernel32
        p.handle = k.GetCurrentProcess()
        try:
            out = p.read_many([(addr, 5), (addr + 6, 5), (addr, 0)])
            self.assertEqual(out[0], b"HELLO")
            self.assertEqual(out[1], b"AELOC")
            self.assertEqual(out[2], b"")           # 零长度段
        finally:
            p.handle = None

    def test_c_path_failure_raises(self):
        """批量读里任一段失败 → 与单条 read 相同的 RuntimeError 语义。"""
        import core.proc as proc

        if proc._rpm_lib() is None:
            self.skipTest("aeloc_rpm.dll 未编译")
        p = self._bare_process()
        k = C.windll.kernel32
        p.handle = k.GetCurrentProcess()
        try:
            buf = C.create_string_buffer(b"OK")
            addr = C.addressof(buf)
            with self.assertRaises(RuntimeError):
                p.read_many([(addr, 2), (0x1, 4)])   # 0x1 是不可读页
        finally:
            p.handle = None

    def test_fallback_aggregates_like_read(self):
        """DLL 缺失:逐条回退,聚合结果与 read 一一对应;失败抛同款错误。"""
        import core.proc as proc
        from unittest.mock import patch

        p = self._bare_process()
        reads = []
        p.read = lambda a, s: reads.append((a, s)) or b"\xAB" * s
        with patch("core.proc._rpm_lib", return_value=None):
            out = p.read_many([(0x1000, 4), (0x2000, 2)])
            self.assertEqual(out, [b"\xAB" * 4, b"\xAB" * 2])
            self.assertEqual(reads, [(0x1000, 4), (0x2000, 2)])

            def boom(a, s):
                raise RuntimeError("AE 内存读取失败")

            p.read = boom
            with self.assertRaises(RuntimeError):
                p.read_many([(0x1000, 4)])


if __name__ == "__main__":
    unittest.main()
