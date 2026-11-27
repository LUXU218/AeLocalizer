# -*- coding: utf-8 -*-
"""参数发现本体的有界/续步回归（native RPM 用 fake，发现函数不 mock）。"""
import struct
import time
import types
import unittest
from unittest import mock

from core import paramdiscovery as pd


class _Proc:
    pid = 77
    creation_time = 123

    def __init__(self, base=0x100000, raw=b"", tdb=b"tdb.dll"):
        self.base = base
        self.raw = raw
        self.tdb = tdb
        self.mem = {}

    def modules(self):
        return [{"name": "tdb.dll", "base": self.base, "path": self.tdb}]

    def read(self, address, size):
        # 真实 RPM：只允许落在同一已分配块内，短读/跨界一律失败。
        if address == self.base + 0x1000:
            if address + size > address + len(self.raw):
                raise OSError("raw section overrun")
            return self.raw[:size]
        for start, data in self.mem.items():
            if start <= address and address + size <= start + len(data):
                off = address - start
                return data[off:off + size]
        raise OSError("bad read 0x%x size=%d" % (address, size))

    def u64(self, address):
        return struct.unpack("<Q", self.read(address, 8))[0]


def _pool_payload(parade_name_addr=0x400600):
    """坏 header 在前；好 pool 的样本不含 Parade，完整链里才有。"""
    raw = bytearray(0x200)
    # bad candidate: buckets points to unmapped 0x999999 -> OSError
    struct.pack_into("<Q", raw, 0x08, 16)
    struct.pack_into("<Q", raw, 0x28, 0x999999)
    # good candidate at +0x80
    off = 0x80
    count = 64
    buckets = 0x200000
    struct.pack_into("<Q", raw, off + 8, count)
    struct.pack_into("<Q", raw, off + 40, buckets)
    p = _Proc(raw=bytes(raw))
    heads = [0] * count
    # 协议：bucket slot 地址 -> u64 -> node。每槽单独 8B。
    # 33 个样本头，Parade 只在链后节点；样本前 32 看不到它。
    for i in range(33):
        node = 0x300000 + i * 0x100
        name = 0x400000 + i * 0x100
        slot = 0x210000 + i * 8
        heads[i] = slot
        p.mem[slot] = struct.pack("<Q", node)
        p.mem[node] = (struct.pack("<Q", 0) + b"\0" * 8
                       + struct.pack("<Q", name) + b"\0" * 56)
        p.mem[name] = (b"ADBE Sample %d\0" % i).ljust(32, b"\0")
    # bucket 0 chain: node0 -> ParadeNode -> 0；Parade 不在前 32 样本。
    node0 = 0x300000
    parade_node = 0x300000 + 33 * 0x100
    name6 = parade_name_addr
    p.mem[node0] = (struct.pack("<Q", parade_node) + b"\0" * 8
                    + struct.pack("<Q", 0x400000) + b"\0" * 56)
    # 真节点 80B：next(8)+reserved(8)+nameptr(8)+pad(56)。
    p.mem[parade_node] = b"\0" * 16 + struct.pack("<Q", name6) + b"\0" * 56
    _name_len = 72 if parade_name_addr == 0x1FFB8 else 32
    p.mem[name6] = (b"ADBE Effect Parade\0").ljust(_name_len, b"\0")
    p.mem[buckets] = struct.pack("<" + "Q" * count, *heads)
    return p


class DiscoverPoolTest(unittest.TestCase):
    def setUp(self):
        pd._POOL_CACHE.clear()

    def test_bad_header_is_skipped_and_parade_beyond_sample(self):
        p = _pool_payload()
        with mock.patch.object(pd, "_writable_sections",
                               return_value=[(0x1000, len(p.raw))]):
            rva = pd.discover_match_pool(p, types.SimpleNamespace())
        self.assertEqual(rva, 0x1080)

    def test_parade_name_near_page_end_uses_bounded_read(self):
        # 0x1FFB8 距 0x20000 页尾仅 72B：固定 96B 读必失败。
        p = _pool_payload(parade_name_addr=0x1FFB8)
        with mock.patch.object(pd, "_writable_sections",
                               return_value=[(0x1000, len(p.raw))]):
            rva = pd.discover_match_pool(p, types.SimpleNamespace())
        self.assertEqual(rva, 0x1080)


class DiscoverParadesTest(unittest.TestCase):
    def setUp(self):
        pd._PARADE_CACHE.clear()

    def test_two_regions_two_budgets_reach_second_parade(self):
        needle = b"A" * 16
        # region1 0x1008 is parade1, region2 0x2008 is parade2
        p = _Proc()
        region1 = bytearray(0x100)
        region1[0x10:0x20] = needle
        struct.pack_into("<QQ", region1, 0x28, 0x5000, 0x5010)
        p.mem[0x1000] = bytes(region1)
        region2 = bytearray(0x100)
        region2[0x10:0x20] = needle
        struct.pack_into("<QQ", region2, 0x28, 0x5100, 0x5110)
        p.mem[0x2000] = bytes(region2)
        p.mem[0x5000] = struct.pack("<QQ", 0x6000, 0)
        p.mem[0x5100] = struct.pack("<QQ", 0x6100, 0)
        p.mem[0x6020] = struct.pack("<Q", 0x1008)
        p.mem[0x6120] = struct.pack("<Q", 0x2008)
        p.mem[0x7020] = struct.pack("<Q", 0x6000)
        p.mem[0x7120] = struct.pack("<Q", 0x6100)
        p.mem[0x7008] = needle
        p.mem[0x7108] = needle

        class _Cat:
            names = {needle: "ADBE Effect Parade"}
            profile = types.SimpleNamespace(match_pool=1, group_vector=0x20)

            @staticmethod
            def children(root):
                yield (0x7000 if root == 0x6000 else 0x7100), needle

        cat = _Cat()
        # 1st: now=0, first budget check=0, second region check=11 -> stop.
        # 2nd: now=11, first check=11, second check=11 -> finish; next_rescan=11.
        with mock.patch.object(pd, "iter_heap_regions",
                               return_value=[(0x1000, 0x100), (0x2000, 0x100)]), \
             mock.patch.object(pd.time, "monotonic",
                               side_effect=[0, 0, 11, 11, 11, 11]):
            first = pd.discover_parades(p, cat, deadline=10)
            pending = pd.parade_scan_pending(p, cat)
            second = pd.discover_parades(p, cat, deadline=100)
            done = pd.parade_scan_pending(p, cat)
        self.assertEqual(first, {0x1008})
        self.assertTrue(pending)
        self.assertEqual(second, {0x1008, 0x2008})
        self.assertFalse(done)


class TransientRegionTest(unittest.TestCase):
    def setUp(self):
        pd._PARADE_CACHE.clear()

    def test_unreadable_chunk_is_skipped_and_cursor_advances(self):
        needle = b"B" * 16

        class P(_Proc):
            def read(self, address, size):
                if address == 0x1000:
                    raise OSError("page protection changed")
                return super().read(address, size)

        class P2(_Proc):
            def __init__(self):
                super().__init__()
                self.failed = False

            def read(self, address, size):
                if address == 0x1000 and not self.failed:
                    self.failed = True
                    raise OSError("page protection changed")
                return super().read(address, size)

        p = P2()
        # avoid _Proc's special raw-section address 0x101000.
        p.base = 0x500000
        region = bytearray(0x200000)
        region[0x100010:0x100020] = needle
        struct.pack_into("<QQ", region, 0x100028, 0x5000, 0x5010)
        struct.pack_into("<QQ", region, 0x4000, 0x6000, 0)
        struct.pack_into("<Q", region, 0x5020, 0x101008)
        region[0x6008:0x6018] = needle
        struct.pack_into("<Q", region, 0x6020, 0x6000)
        p.mem[0x1000] = bytes(region)

        class Cat:
            names = {needle: "ADBE Effect Parade"}
            profile = types.SimpleNamespace(match_pool=1, group_vector=0x20)

            @staticmethod
            def children(root):
                yield 0x7000, needle

        with mock.patch.object(pd, "iter_heap_regions",
                               return_value=[(0x1000, 0x200000)]):
            found = pd.discover_parades(p, Cat(),
                                        deadline=time.monotonic() + 2)
            pending = pd.parade_scan_pending(p, Cat())
        self.assertEqual(found, {0x101008})
        self.assertFalse(pending)


class ParadeRescanScheduleTest(unittest.TestCase):
    """done 缓存不能把 5 秒重扫 deadline 一直往后推。"""

    def setUp(self):
        pd._PARADE_CACHE.clear()
        self.needle = b"C" * 16
        self.p = _Proc()

    def _site(self, base, vector, root, child):
        needle = self.needle
        region = bytearray(0x100)
        region[0x10:0x20] = needle
        struct.pack_into("<QQ", region, 0x28, vector, vector + 0x10)
        self.p.mem[base] = bytes(region)
        self.p.mem[vector] = struct.pack("<QQ", root, 0)
        self.p.mem[root + 0x20] = struct.pack("<Q", base + 0x8)
        self.p.mem[child + 8] = needle
        self.p.mem[child + 0x20] = struct.pack("<Q", root)
        return base + 0x8

    def _cat(self, mapping):
        needle = self.needle

        class Cat:
            names = {needle: "ADBE Effect Parade"}
            profile = types.SimpleNamespace(match_pool=1, group_vector=0x20)

            @staticmethod
            def children(root):
                child = mapping.get(root)
                if child is not None:
                    yield child, needle

        return Cat()

    def test_frequent_calls_do_not_postpone_rescan_and_new_region_is_found(self):
        old_addr = self._site(0x1000, 0x5000, 0x6000, 0x7000)
        new_addr = self._site(0x3000, 0x5200, 0x6200, 0x7200)
        cat = self._cat({0x6000: 0x7000, 0x6200: 0x7200})
        regions = [(0x1000, 0x100)]

        def heap(_p):
            return list(regions)

        # t0: create + complete (entry now, end now => next_rescan=5).
        # t1..t4: done cache hits; must not consume an end-call or move it.
        # t5.1: deadline reached; rescan the refreshed region snapshot.
        with mock.patch.object(pd, "iter_heap_regions", side_effect=heap), \
             mock.patch.object(pd.time, "monotonic",
                               side_effect=[0, 0, 1, 2, 3, 4, 5.1, 5.1]):
            self.assertEqual(pd.discover_parades(self.p, cat), {old_addr})
            self.assertFalse(pd.parade_scan_pending(self.p, cat))
            key = (int(self.p.pid), int(self.p.creation_time), 1)
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 5.0)
            for _ in range(4):
                self.assertEqual(pd.discover_parades(self.p, cat), {old_addr})
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 5.0)
            regions.append((0x3000, 0x100))
            self.assertEqual(pd.discover_parades(self.p, cat),
                             {old_addr, new_addr})
            self.assertFalse(pd.parade_scan_pending(self.p, cat))
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 10.1)

    def test_all_old_parades_invalid_refreshes_regions_and_finds_new(self):
        old_addr = self._site(0x1000, 0x5000, 0x6000, 0x7000)
        new_addr = self._site(0x3000, 0x5200, 0x6200, 0x7200)
        cat = self._cat({0x6000: 0x7000, 0x6200: 0x7200})
        regions = [(0x1000, 0x100)]

        def heap(_p):
            return list(regions)

        with mock.patch.object(pd, "iter_heap_regions", side_effect=heap), \
             mock.patch.object(pd.time, "monotonic",
                               side_effect=[0, 0, 1, 1, 2, 3, 4]):
            self.assertEqual(pd.discover_parades(self.p, cat), {old_addr})
            key = (int(self.p.pid), int(self.p.creation_time), 1)
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 5.0)
            # 旧 A 失效；新 B 只在新增 region 里。done 缓存未到期时
            # 也必须刷新 regions，否则空集恢复会一直扫旧快照。
            self.p.mem[0x6020] = struct.pack("<Q", 0)
            regions.append((0x3000, 0x100))
            self.assertEqual(pd.discover_parades(self.p, cat), {new_addr})
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 6.0)
            for _ in range(3):
                self.assertEqual(pd.discover_parades(self.p, cat),
                                 {new_addr})
            self.assertEqual(pd._PARADE_CACHE[key]["next_rescan"], 6.0)
        self.assertFalse(pd.parade_scan_pending(self.p, cat))

    def test_popup_rescan_can_discover_new_parade_before_default_five_seconds(self):
        old = self._site(0x1000, 0x5000, 0x6000, 0x7000)
        new = self._site(0x3000, 0x5200, 0x6200, 0x7200)
        cat = self._cat({0x6000: 0x7000, 0x6200: 0x7200})
        regions = [(0x1000, 0x100)]
        with mock.patch.object(pd, "iter_heap_regions", side_effect=lambda _: list(regions)), \
             mock.patch.object(pd.time, "monotonic", side_effect=[0, 0, 1.9, 2.1, 2.1]):
            self.assertEqual(pd.discover_parades(self.p, cat, rescan_interval=2.0), {old})
            regions.append((0x3000, 0x100))
            self.assertEqual(pd.discover_parades(self.p, cat, rescan_interval=2.0), {old})
            self.assertEqual(pd.discover_parades(self.p, cat, rescan_interval=2.0), {old, new})

    def test_cancel_is_not_marked_done_and_resumes(self):
        addr = self._site(0x1000, 0x5000, 0x6000, 0x7000)
        cat = self._cat({0x6000: 0x7000})
        with mock.patch.object(pd, "iter_heap_regions",
                               return_value=[(0x1000, 0x100)]):
            self.assertEqual(pd.discover_parades(self.p, cat,
                                                 cancel=lambda: True), set())
            self.assertTrue(pd.parade_scan_pending(self.p, cat))
            self.assertEqual(pd.discover_parades(self.p, cat,
                                                 cancel=lambda: False), {addr})
        self.assertFalse(pd.parade_scan_pending(self.p, cat))


class RowsRefreshTest(unittest.TestCase):
    def test_cached_name_check_stops_after_visible_prefix_and_reloads_on_visible_miss(self):
        class Row(dict):
            parent_reads = 0

            def __getitem__(self, key):
                if key == "parent":
                    self.parent_reads += 1
                return super().__getitem__(key)

        p = _Proc()
        profile = types.SimpleNamespace(match_pool=1, flt_sha="f", tdb_sha="t")
        names_key = (p.pid, p.creation_time, "f", "t", 1)
        visible = Row(parent=1, matchName="param", effectMatchName="effect")
        background = [Row(parent=2, matchName="", effectMatchName="") for _ in range(8)]
        catalog = types.SimpleNamespace(profile=profile, names={})
        catalog.rows = lambda **kwargs: [*background, visible]

        def reload_names():
            visible["matchName"] = "new name"
            return {}

        catalog.match_pool = mock.Mock(side_effect=reload_names)
        with mock.patch.object(pd, "_POPUP_NAMES", {names_key: {}}), \
             mock.patch.object(pd, "_PARADE_CACHE", {}), \
             mock.patch.object(pd, "Registry", return_value=types.SimpleNamespace(profile=profile)), \
             mock.patch.object(pd, "profile_with_pool", return_value=profile), \
             mock.patch.object(pd, "ParameterCatalog", return_value=catalog), \
             mock.patch.object(pd, "_visible_parades", return_value=({5}, {1})), \
             mock.patch.object(pd, "_valid_parade", return_value=True):
            _, rows, _, pending = pd.parameter_rows(
                p, None, with_state=True, visible_streams=(10,), popup_ownership=True)
            self.assertTrue(pending)
            self.assertEqual(rows, [visible, *background])
            # Each later background row participated only in the stable sort.
            self.assertEqual([r.parent_reads for r in background[1:]], [1] * 7)
            catalog.match_pool.assert_not_called()

            visible["matchName"] = ""
            _, rows, _, _ = pd.parameter_rows(
                p, None, with_state=True, visible_streams=(10,), popup_ownership=True)
            catalog.match_pool.assert_called_once_with()
            self.assertEqual(rows[0]["matchName"], "new name")

    def test_new_visible_stream_bypasses_heap_discovery_and_revalidates_owners(self):
        import dataclasses

        @dataclasses.dataclass
        class Profile:
            match_pool: int = 1
            root_vtable: int = 0x10

        profile = Profile()
        p = _Proc()
        p.u64 = {0x120: 0x200, 0x220: 0x300, 0x420: 0x500, 0x520: 0x600, 0x200: 0x110}.__getitem__
        p.read = lambda address, size: b"known" if address == 0x308 else b"other"
        cached_row = {"parent": 0x240}
        new_row = {"parent": 0x200}
        catalog = types.SimpleNamespace(profile=profile, flt=0x100, names={b"known": "ADBE Effect Parade"})
        catalog.rows = lambda: [cached_row, new_row]
        cache = {(p.pid, p.creation_time, 1): {"parades": {0x390}, "done": True}}
        with mock.patch.object(pd, "_PARADE_CACHE", cache), \
             mock.patch.object(pd, "Registry", return_value=types.SimpleNamespace(profile=profile)), \
             mock.patch.object(pd, "ParameterCatalog", return_value=catalog), \
             mock.patch.object(pd, "_valid_parade", side_effect=lambda p, cat, addr: addr != 0x600), \
             mock.patch.object(pd, "discover_parades") as scan:
            _, rows, parades, pending = pd.parameter_rows(
                p, None, with_state=True, visible_streams=(0x100, 0x400))
        self.assertEqual((rows, parades, pending), ([new_row, cached_row], {0x300, 0x390}, True))
        scan.assert_not_called()

    def test_current_pass_revalidates_cached_parades_without_running_heap_discovery(self):
        import dataclasses

        @dataclasses.dataclass
        class Profile:
            match_pool: int = 1

        p = _Proc()
        profile = Profile()
        current_rows = [{"name": "current"}]
        catalog = types.SimpleNamespace(profile=profile)
        catalog.rows = lambda: current_rows if catalog.scan_parades() == {2} else []
        cache = {(p.pid, p.creation_time, 1): {"parades": {1, 2}}}
        with mock.patch.object(pd, "_PARADE_CACHE", cache), \
             mock.patch.object(pd, "Registry", return_value=types.SimpleNamespace(profile=profile)), \
             mock.patch.object(pd, "ParameterCatalog", return_value=catalog), \
             mock.patch.object(pd, "_valid_parade", side_effect=lambda p, cat, addr: addr == 2), \
             mock.patch.object(pd, "discover_parades") as scan:
            _, rows, parades, pending = pd.parameter_rows(
                p, None, with_state=True, prefer_current=True)
        self.assertEqual((rows, parades, pending), (current_rows, {2}, True))
        scan.assert_not_called()

    def test_parade_slice_starts_after_catalog_construction(self):
        import dataclasses

        @dataclasses.dataclass
        class Profile:
            match_pool: int = 1

        profile = Profile()
        with mock.patch.object(pd, "Registry", return_value=types.SimpleNamespace(profile=profile)), \
             mock.patch.object(pd, "ParameterCatalog", return_value=types.SimpleNamespace(profile=profile)), \
             mock.patch.object(pd, "discover_parades", return_value=set()) as scan, \
             mock.patch.object(pd, "parade_scan_pending", return_value=False), \
             mock.patch.object(pd.time, "monotonic", return_value=10.7):
            pd.parameter_rows(_Proc(), None, deadline=10.2, parade_scan_budget=0.20)
        self.assertAlmostEqual(scan.call_args.kwargs["deadline"], 10.9)

    def test_parameter_rows_are_not_cached(self):
        import dataclasses

        @dataclasses.dataclass
        class _Profile:
            match_pool: int = 1

        class _Reg:
            profile = _Profile()

        source = {"rows": [{"name": "A"}]}

        class _Cat:
            def __init__(self, *a, **k):
                self.profile = _Reg.profile

            def rows(self):
                return list(source["rows"])

        with mock.patch.object(pd, "Registry", return_value=_Reg()), \
             mock.patch.object(pd, "ParameterCatalog", _Cat), \
             mock.patch.object(pd, "discover_parades", return_value={1}), \
             mock.patch.object(pd, "parade_scan_pending", return_value=False):
            a = pd.parameter_rows(_Proc(), None)
            source["rows"] = [{"name": "A"}, {"name": "B"}]
            b = pd.parameter_rows(_Proc(), None)
        self.assertEqual(len(a[1]), 1)
        self.assertEqual(len(b[1]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
