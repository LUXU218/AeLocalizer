"""core.adapter 的离线测试:profile 存储与选择 + registry_layout 交叉校验。"""
import os
import shutil
import struct
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.adapter import (  # noqa: E402
    CatalogMismatch,
    Profile,
    default_encoding,
    get_profile,
    is_supported,
    load_local,
    mismatch_text,
    nameless,
    registry_layout,
    save_local,
    year_of,
)

from fake_ae import FLT_BASE, FakeAE, FakeProcess  # noqa: E402


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_adapter_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_local_roundtrip(self):
        fake = FakeAE(self.tmp, save=False)
        save_local(self.tmp, {fake.version: fake.profile}, merge=False)
        loaded = load_local(self.tmp)
        self.assertIn(fake.version, loaded)
        restored = loaded[fake.version]
        self.assertEqual(restored, fake.profile)
        self.assertEqual(restored.count_bytes, fake.profile.count_bytes)

    def test_load_rejects_unknown_fields(self):
        from core.adapter import local_profiles_path

        path = local_profiles_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schemaVersion":1,"kind":"aeloc-adapter-profiles","profiles":{"1.0":{"bogus":1}}}', encoding="utf8")
        with self.assertRaises(ValueError):
            load_local(self.tmp)

    def test_load_missing_returns_empty(self):
        self.assertEqual(load_local(self.tmp), {})

    def test_get_profile_by_sha(self):
        fake = FakeAE(self.tmp)
        profile = get_profile(fake.version, fake.profile.flt_sha, self.tmp)
        self.assertEqual(profile.flt_sha, fake.profile.flt_sha)
        wrong = "0" * 64
        with self.assertRaises(RuntimeError) as ctx:
            get_profile(fake.version, wrong, self.tmp)
        self.assertIn("未执行写入", str(ctx.exception))
        self.assertIn("自动识别与适配", mismatch_text(fake.version))

    def test_unsupported_version_rejected(self):
        with self.assertRaises(RuntimeError):
            get_profile("24.1.0.9", None, self.tmp)
        self.assertFalse(is_supported("24.1.0.9", None, self.tmp))

    def test_year_and_encoding_helpers(self):
        self.assertEqual(year_of("25.3.0.71"), "2025")
        self.assertEqual(year_of("18.2.1.8"), "2021")
        self.assertEqual(default_encoding("26.0.0.67"), "utf8")
        self.assertEqual(default_encoding("25.3.0.71"), "mbcs")
        self.assertEqual(default_encoding("18.2.1.8"), "mbcs")


class NamelessTests(unittest.TestCase):
    def test_empty_and_valid(self):
        self.assertTrue(nameless(b"\0" + b"\x11" * 47, "utf8"))
        self.assertFalse(nameless(b"Foo\0" + b"\x11" * 40, "utf8"))

    def test_undecodable_match_name(self):
        raw = "辉光".encode("mbcs") + b"\0"
        self.assertFalse(nameless(raw, "mbcs"))
        self.assertTrue(nameless(raw, "utf8"))  # utf8 读不动 → 视为无名


class RegistryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_layout_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeAE(self.tmp)
        self.p = self.fake.process()

    def test_unique_offset_found(self):
        result = registry_layout(self.p, FLT_BASE, self.fake.profile, self.fake.host_catalog)
        self.assertEqual(result["offset"], self.fake.profile.registry_vector)
        self.assertEqual(result["encoding"], self.fake.profile.encoding)
        self.assertEqual(result["matchEncoding"], "")

    def test_utf8_build_reads_unique(self):
        fake = FakeAE(self.tmp, encoding="utf8")
        result = registry_layout(fake.process(), FLT_BASE, fake.profile, fake.host_catalog)
        self.assertEqual(result["encoding"], "utf8")

    def test_corrupted_name_detected(self):
        index = 1
        raw = bytearray(self.fake.display_name_bytes(index))
        # 把中文换成别的字:所有编码读法都对不上 host 目录
        raw[0:2] = "雾".encode("mbcs")
        raw[2:] = b"\0" * (len(raw) - 2)
        self.fake.set_display_name_bytes(index, bytes(raw))
        with self.assertRaises(CatalogMismatch) as ctx:
            registry_layout(self.p, FLT_BASE, self.fake.profile, self.fake.host_catalog)
        self.assertIn("最接近的目录", str(ctx.exception))
        self.assertTrue(ctx.exception.details["candidates"])

    def test_missing_effect_detected(self):
        catalog = self.fake.host_catalog[:-1]
        with self.assertRaises(CatalogMismatch):
            registry_layout(self.p, FLT_BASE, self.fake.profile, catalog)

    def test_duplicate_matchname_conflict(self):
        catalog = self.fake.host_catalog
        catalog.append({"displayName": "另一个名字", "matchName": "S_Glow", "category": "Sapphire"})
        with self.assertRaises(CatalogMismatch) as ctx:
            registry_layout(self.p, FLT_BASE, self.fake.profile, catalog)
        self.assertIn("装了两份", str(ctx.exception))

    def test_ascii_catalog_is_ambiguous(self):
        # 全英文名时 utf8/mbcs 读法都成立 → 拒绝猜测(给出可操作的指引)
        for index in range(len(self.fake.effects)):
            address = self.fake.object_addresses[index] + 16
            raw = ("Effect %d" % index).encode("ascii")
            raw = (raw + b"\0").ljust(48, b"\0")
            self.fake.set_display_name_bytes(index, raw)
        with self.assertRaises(CatalogMismatch) as ctx:
            registry_layout(self.p, FLT_BASE, self.fake.profile, [{"displayName": "Effect %d" % i, "matchName": m, "category": c} for i, (d, m, c) in enumerate(self.fake.effects)])
        self.assertIn("无法区分编码", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
