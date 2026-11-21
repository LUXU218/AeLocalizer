"""core.paramcatalog 的离线测试:match 池遍历 + 参数流枚举 + 接口校验。"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import paramcatalog  # noqa: E402
from core.adapter import load_local, save_local  # noqa: E402
from core.paramcatalog import ParameterCatalog, enumerate_parameters  # noqa: E402

from fake_ae import FakeAE  # noqa: E402


class ParamCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_param_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeAE(self.tmp, with_params=True)
        patcher = mock.patch("core.livenames.file_version", lambda _p: self.fake.version)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = self.fake.process()
        query_patcher = mock.patch.object(paramcatalog, "virtual_query", self._make_fake_query(self.fake))
        query_patcher.start()
        self.addCleanup(query_patcher.stop)

    @staticmethod
    def _make_fake_query(fake):
        from core.proc import MBI

        spans = sorted((base, len(data)) for base, data in fake.regions.items())
        top = max(base + size for base, size in spans)

        def fake_query(_p, address):
            if address >= top:
                return None
            for base, size in spans:
                if base + size > address:
                    m = MBI()
                    if address < base:  # 空闲区:报告 span 让扫描器跳过去
                        m.BaseAddress = address
                        m.RegionSize = base - address
                        m.State = 0x10000
                        m.Type = 0
                        m.Protect = 0
                    else:
                        m.BaseAddress = base
                        m.RegionSize = size
                        m.State = 0x1000
                        m.Type = 0x20000
                        m.Protect = 4
                    return m
            return None

        return fake_query

    def test_match_pool_walks_all_names(self):
        catalog = ParameterCatalog(self.p, self.tmp)
        for _d, match, _c in self.fake.effects:
            self.assertIn(match, catalog.names.values())
        self.assertIn("ADBE Effect Parade", catalog.names.values())

    def test_enumerate_all_parameters(self):
        rows = enumerate_parameters(self.p, self.tmp)
        by_effect = {}
        for row in rows:
            by_effect.setdefault(row["effectMatchName"], []).append(row)
        self.assertEqual(len(by_effect), len(self.fake.effects))
        for display, match, _c in self.fake.effects:
            names = {r["name"] for r in by_effect[match]}
            self.assertEqual(names, {"Amount", "Opacity Param Long"})
            for row in by_effect[match]:
                self.assertEqual(row["instances"], 1)
                self.assertTrue(row["pfName"])

    def test_long_display_name_uses_heap_pointer(self):
        rows = enumerate_parameters(self.p, self.tmp)
        long_rows = [r for r in rows if r["name"] == "Opacity Param Long"]
        self.assertEqual(len(long_rows), len(self.fake.effects))

    def test_corrupted_getter_refuses(self):
        from fake_ae import TDB_BASE

        rva = self.fake.profile.parent_get
        self.p.write_buffer(TDB_BASE + rva, b"\x48\x8b\x41\x21\xc3")  # 偏移被改
        with self.assertRaises(RuntimeError) as ctx:
            ParameterCatalog(self.p, self.tmp)
        self.assertIn("参数结构接口不匹配", str(ctx.exception))

    def test_profile_without_tdb_layout_refuses(self):
        from dataclasses import replace

        profile = replace(self.fake.profile, count_get=0, count_bytes=b"", root_vtable=0)
        save_local(self.tmp, {self.fake.version: profile}, merge=False)
        with self.assertRaises(RuntimeError) as ctx:
            ParameterCatalog(self.p, self.tmp)
        self.assertIn("未适配参数枚举", str(ctx.exception))

    def test_scan_finds_parades(self):
        catalog = ParameterCatalog(self.p, self.tmp)
        parades = catalog.scan_parades()
        self.assertEqual(parades, {0x10000000 + 0x200000})  # PARADE_ADDR
        roots = catalog.roots()
        self.assertEqual(len(roots), len(self.fake.effects))


if __name__ == "__main__":
    unittest.main()
