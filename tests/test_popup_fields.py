"""Behavior tests using realistic contiguous memory and valid pointer ranges."""
import unittest
from core import popup_fields

ROW = 0x10000
BUF = 0x20000
PARENT = 0x30000


class Memory:
    def __init__(self):
        self.mem = {ROW: bytearray(4096), BUF: bytearray(4096)}
        self.writes = 0

    def read(self, address, size):
        for start, region in self.mem.items():
            if start <= address and address + size <= start + len(region):
                return bytes(region[address - start:address - start + size])
        raise OSError('bad read')

    def put(self, address, data):
        for start, region in self.mem.items():
            if start <= address and address + len(data) <= start + len(region):
                region[address - start:address - start + len(data)] = data
                return
        raise OSError('bad write')

    def u64(self, address):
        return int.from_bytes(self.read(address, 8), 'little')

    def write_exact(self, address, data):
        self.put(address, data)
        self.writes += 1


class PopupFieldTest(unittest.TestCase):
    def setup_memory(self, text):
        p = Memory()
        p.put(ROW + 32, PARENT.to_bytes(8, 'little'))
        p.put(ROW + 0x70, BUF.to_bytes(8, 'little'))
        p.put(ROW + 0x68, BUF.to_bytes(8, 'little'))
        p.put(BUF, text.encode('gbk') + b'\0')
        row = {'effectMatchName': 'BCC', 'matchName': 'p', 'address': ROW,
               'guid': '00' * 16, 'parent': PARENT}
        return p, row

    def test_reads_gbk_and_dedups_aliases(self):
        p, row = self.setup_memory('关|并排|对比')
        fields = popup_fields.read_popup_fields(p, [row])
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]['items'], ['关', '并排', '对比'])
        self.assertEqual(p.writes, 0)

    def test_write_only_same_or_shorter_and_expected_raw(self):
        p, row = self.setup_memory('关|并排')
        field = popup_fields.read_popup_fields(p, [row])[0]
        self.assertTrue(popup_fields.write_popup_field(p, field, '关|排'))
        self.assertEqual(p.read(BUF, 6), '关|排'.encode('gbk') + b'\0')
        self.assertFalse(popup_fields.write_popup_field(p, field, '关|并排|更长'))
        self.assertEqual(p.writes, 1)

    def test_cross_page_cstring_requires_real_nul(self):
        class P:
            # two complete contiguous pages; string starts at page-tail 0x1ff0
            def __init__(self, full=True):
                self.mem = {0x1000: bytearray(0x2000 if full else 0x1000)}
                self.fail = set()
                if full:
                    raw = b"All Opaque|Normal|As Premult\0"
                    self.mem[0x1000][0xFF0:0xFF0 + len(raw)] = raw

            def read(self, a, n):
                for start, region in self.mem.items():
                    if start <= a and a + n <= start + len(region):
                        if start in self.fail:
                            raise OSError("next page unreadable")
                        return bytes(region[a - start:a - start + n])
                raise OSError("unmapped")

        p = P()
        self.assertEqual(popup_fields.read_cstring(p, 0x1FF0, 64),
                         b"All Opaque|Normal|As Premult")
        p2 = P(full=False)  # first page ends without NUL; next page absent
        p2.mem[0x1000][0xFF0:0x1000] = b"A" * 16
        self.assertIsNone(popup_fields.read_cstring(p2, 0x1FF0, 64))

    def test_changed_guid_rejects_without_writing(self):
        p, row = self.setup_memory('Off|On')
        field = popup_fields.read_popup_fields(p, [row])[0]
        p.put(ROW + 8, b'x' * 16)
        self.assertFalse(popup_fields.write_popup_field(p, field, '关|开'))
        self.assertEqual(p.writes, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
