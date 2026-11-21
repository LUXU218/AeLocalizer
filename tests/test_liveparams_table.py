# -*- coding: utf-8 -*-
"""NameTable（实例名字槽）单测：扫描判据 / 翻译 / 写入与还原。"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import liveparams  # noqa: E402


class _FakeProc:
    """只实现 NameTable 用到的 read / 写入回调。"""
    def __init__(self, base, size):
        self.mem = bytearray(size)
        self.base = base
        self.writes = []

    def put(self, addr, raw):
        off = addr - self.base
        self.mem[off:off + len(raw)] = raw

    def read(self, addr, n):
        off = addr - self.base
        if off < 0 or off + n > len(self.mem):
            raise OSError("bad read")
        return bytes(self.mem[off:off + n])


class TestNameTable(unittest.TestCase):
    def _proc_with(self, names, base=0x10000):
        p = _FakeProc(base, 0x4000)
        for i, name in enumerate(names):
            addr = base + 0x100 + i * liveparams.SLOT
            slot = name.encode("utf-16-le") + b"\x00\x00"
            slot += b"\x00" * (liveparams.SLOT - len(slot))
            p.put(addr, slot)
        return p

    def test_translate_only_shorter(self):
        nt = liveparams.NameTable(None, {"Blur": "\u6a21\u7cca", "Very Long Name": "\u957f"})
        self.assertEqual(nt.translate("Blur"), "\u6a21\u7cca")
        # 中文比英文长时不换（避免越过 32 字节槽）
        self.assertEqual(nt.translate("Very Long Name"), "\u957f")
        nt2 = liveparams.NameTable(None, {"Ab": "\u4e2d\u6587\u5b57"})
        self.assertEqual(nt2.translate("Ab"), "")

    def test_slot_shape_is_32_bytes(self):
        self.assertEqual(liveparams.SLOT, 32)
        self.assertEqual(liveparams.MEM_PRIVATE, 0x20000)

    def test_scan_requires_translation(self):
        p = self._proc_with(["Blur", "Glow"])
        nt = liveparams.NameTable(p, {"Blur": "\u6a21\u7cca"})
        # patch region walker to the fake memory
        old = liveparams.iter_scan_regions
        liveparams.iter_scan_regions = lambda _p: [(p.base, len(p.mem))]
        try:
            found = nt.scan(["Blur", "Glow"])
        finally:
            liveparams.iter_scan_regions = old
        self.assertEqual([f["en"] for f in found], ["Blur"])

    def test_apply_and_restore_roundtrip(self):
        p = self._proc_with(["Blur", "Glow"])
        nt = liveparams.NameTable(p, {"Blur": "\u6a21\u7cca"})
        old_walk = liveparams.iter_scan_regions
        liveparams.iter_scan_regions = lambda _p: [(p.base, len(p.mem))]
        nt._write = lambda addr, payload: (p.put(addr, payload), True)[1]
        try:
            result = nt.apply(["Blur"])
            self.assertEqual(result["written"], 1)
            rec = result["records"][0]
            written = p.read(rec["addr"], liveparams.SLOT)
            self.assertEqual(written.decode("utf-16-le").split("\x00")[0], "\u6a21\u7cca")
            # 还原
            back = nt.restore([rec])
            self.assertEqual(back["restored"], 1)
            self.assertEqual(p.read(rec["addr"], 6), b"B\x00l\x00u\x00")
        finally:
            liveparams.iter_scan_regions = old_walk


if __name__ == "__main__":
    unittest.main()