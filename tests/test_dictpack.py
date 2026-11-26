"""词典包安全与格式测试。"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dictpack import build_pack, read_pack  # noqa: E402
from core.dictpack.pack import PackError, verify_pack  # noqa: E402


def rewrite_pack_json(data, name, edit):
    """Update one JSON member in a synthetic ZIP without recalculating its manifest."""
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(result, "w") as target:
        for member in source.namelist():
            content = source.read(member)
            if member == name:
                obj = json.loads(content)
                edit(obj)
                content = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            target.writestr(member, content)
    return result.getvalue()


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

    def test_missing_dictionary_hash_is_rejected(self):
        data = build_pack(pack_id="c", name="n", version="1", author="a", entries={})
        data = rewrite_pack_json(data, "manifest.json", lambda manifest: manifest.pop("files"))
        self.assertEqual(verify_pack(read_pack(data)), (False, "缺少 dict.json 的 SHA-256 校验信息"))

    def test_missing_glossary_hash_is_rejected(self):
        data = build_pack(
            pack_id="c", name="n", version="1", author="a", entries={},
            glossary={"Mask": {"zh": "蒙版"}},
        )
        data = rewrite_pack_json(
            data, "manifest.json", lambda manifest: manifest["files"].pop("glossary.json"),
        )
        self.assertEqual(verify_pack(read_pack(data)), (False, "缺少 glossary.json 的 SHA-256 校验信息"))

    def test_present_but_empty_signature_is_rejected(self):
        original = build_pack(pack_id="c", name="n", version="1", author="a", entries={})
        for signature in (None, {}, ""):
            with self.subTest(signature=signature):
                data = rewrite_pack_json(
                    original, "manifest.json", lambda manifest: manifest.update(signature=signature),
                )
                pack = read_pack(data)
                self.assertTrue(pack.signed)
                self.assertEqual(verify_pack(pack), (False, "签名校验失败（签名格式或算法无效）"))

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
