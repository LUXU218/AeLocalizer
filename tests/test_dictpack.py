"""词典包安全与格式测试。"""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dictpack import build_pack, read_pack  # noqa: E402
from core.dictpack.pack import PackError, verify_pack  # noqa: E402


class TestDictPack(unittest.TestCase):
    def test_roundtrip(self):
        entries = {"Glow Threshold": {"zh": "发光阈值"}, "Wrap X": {"zh": "环绕 X"}}
        glossary = {"Mask": {"zh": "蒙版", "strict": True}}
        data = build_pack(
            pack_id="com.test", name="测试包", version="1.0.0", author="tester",
            entries=entries, glossary=glossary, license_text="MIT", readme="# hi",
        )
        pack = read_pack(data)
        self.assertEqual(pack.entries, entries)
        self.assertEqual(pack.glossary, glossary)
        self.assertEqual(pack.name, "测试包")
        ok, msg = verify_pack(pack)
        self.assertTrue(ok, msg)

    def test_tamper_detected(self):
        data = build_pack(pack_id="c", name="n", version="1", author="a", entries={"a": {"zh": "b"}})
        pack = read_pack(data)
        pack.entries["a"]["zh"] = "被改了"
        ok, msg = verify_pack(pack)
        self.assertFalse(ok)

    def test_zip_slip_blocked(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", "{}")
            zf.writestr("dict.json", "{}")
            zf.writestr("../evil.json", "{}")
        with self.assertRaises(PackError):
            read_pack(buf.getvalue())

    def test_schema_mismatch(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", '{"schema":"other/9"}')
            zf.writestr("dict.json", '{"entries":{}}')
        with self.assertRaises(PackError):
            read_pack(buf.getvalue())

    def test_control_chars_rejected(self):
        with self.assertRaises(PackError):
            build_pack(pack_id="a", name="b", version="1", author="c",
                       entries={"x": {"zh": "bad\x01"}})

    def test_missing_manifest(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("dict.json", '{"entries":{}}')
        with self.assertRaises(PackError):
            read_pack(buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
