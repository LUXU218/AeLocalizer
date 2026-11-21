"""效果实例显示名缓存的离线测试（tests/fake_ae.py 的假进程）。

2026-11-07 真机定案后的语义：
  · 「效果控件」面板标题读的是实例 +display_off 的 UTF-16 显示名缓存；
  · 实例的 +0x10 不是名字（普查里全是乱码），写它既改不到标题，又会被 AE
    存进工程（fnma）——所以 +0x10 一个字节都不写；
  · FLT_FCSpec 注册表条目（vtable = flt+fc_vtable）照样只读，顶部「效果」
    菜单因此保持英文。
"""
from __future__ import annotations

import struct
import unittest
from unittest import mock

from tests.fake_ae import FLT_BASE, HEAP_BASE, PARADE_ADDR, FakeAE

from core import liveparams

INST_BASE = HEAP_BASE + 0x200000 + 0x20000      # 假进程堆里的空块
INST_STRIDE = 0x200
#: 真机上实例 +0x10 的样子（别的数据，不是名字）
GARBAGE = bytes(range(0x80, 0x90))


def _fake_regions(fake):
    """假进程的扫描源（真 AE 上是 iter_writable_regions，这里直接给区间）。"""
    return lambda p: [(base, len(data)) for base, data in fake.regions.items()]


class EffectNameTableTest(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = tempfile.mkdtemp(prefix="aeloc_effname_")
        self.fake = FakeAE(self.tmp, with_params=True)
        self.p = self.fake.process(write=True)
        for table in (liveparams._EFFECT_OBJ_MEMO, liveparams._EFFECT_SCAN_AT,
                      liveparams._EFFECT_DIAG, liveparams._EFFECT_REGIONS_MEMO,
                      liveparams._NEEDLE_CURSOR, liveparams._PARADES_MEMO,
                      liveparams._PARADES_AT, liveparams._ROOTS_MEMO,
                      liveparams._ROOTS_AT):
            table.clear()
        for name in ("iter_scan_regions", "iter_heap_regions"):
            q = mock.patch.object(liveparams, name, _fake_regions(self.fake))
            q.start()
            self.addCleanup(q.stop)
        # 本轮起生产只信 parade 根向量；fake 也必须提供同样的真实结构。
        self._root_n = 0
        seed = liveparams.LiveParams(self.p, {}, profile=self.fake.profile)
        seed.discover_parades()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---- 摆对象 ---------------------------------------------------
    def _put_name_field(self, obj, text):
        """按旧 IG5 结构写对象 +0x10 的 48 字节（真机实例这里是别的数据）。"""
        at = obj + liveparams.EFFECT_NAME_OFF
        raw = (text.encode("mbcs") + b"\x00").ljust(48, b"\x00")
        base, region = self.p._region(at, 48)
        region[at - base: at - base + 48] = raw

    def _put_instance(self, index, text, vtable=None, name_field=None):
        """摆一个效果实例：+0x10 = 乱码（真机形态），显示名缓存 = text。

        name_field 给字符串时会按注册表条目的形态把 +0x10 写成名字。
        """
        obj = INST_BASE + index * INST_STRIDE
        base, region = self.p._region(obj, 0x40)
        off = obj - base
        region[off:off + 8] = struct.pack(
            "<Q", vtable if vtable is not None else
            FLT_BASE + self.fake.profile.root_vtable)
        at = obj + liveparams.EFFECT_NAME_OFF
        raw = ((name_field.encode("mbcs") if name_field else GARBAGE)
               + b"\x00").ljust(48, b"\x00")
        b2, r2 = self.p._region(at, 48)
        r2[at - b2: at - b2 + 48] = raw
        if name_field is None:
            # 真实 effect root 的 +0x20 是所属 parade 指针；+0x10 的 48B
            # 结构测试数据会覆盖它，必须在最后补回（registry 条目没有此字段）。
            b3, r3 = self.p._region(obj + 0x20, 8)
            pos3 = obj + 0x20 - b3
            r3[pos3:pos3 + 8] = struct.pack("<Q", PARADE_ADDR)
            # 内联缓存只放得下 7 个字符，长名字走堆缓冲
            self._put_display(obj, text, inline=len(text) < 8)
        self._add_parade_root(obj)
        return obj

    def _add_parade_root(self, obj):
        """把 fake 实例挂进 parade 根向量，生产发现链只信这条向量。"""
        vec = HEAP_BASE + 0x360000
        i = self._root_n
        self._root_n += 1
        b, r = self.p._region(vec + i * 8, 8)
        r[vec + i * 8 - b: vec + i * 8 - b + 8] = struct.pack("<Q", obj)
        end = vec + (i + 1) * 8
        b2, r2 = self.p._region(
            PARADE_ADDR + self.fake.profile.group_vector + 8, 8)
        pos = PARADE_ADDR + self.fake.profile.group_vector + 8 - b2
        r2[pos:pos + 8] = struct.pack("<Q", end)

    def _put_display(self, obj, text, inline=True, capacity=256):
        """摆对象 +display_off 的 UTF-16 显示名缓存（std::wstring 布局）。"""
        off = obj + self.fake.profile.display_cache
        chars = text.encode("utf-16-le")
        buf = bytearray(32)
        if inline:
            buf[0:len(chars)] = chars
            struct.pack_into("<QQ", buf, 16, len(text), 7)
        else:   # 堆缓冲：指针在 +0，字符另放
            at = INST_BASE + 0x100000 + (obj - INST_BASE)
            base, region = self.p._region(at, len(chars) + 2)
            region[at - base: at - base + len(chars) + 2] = chars + b"\0\0"
            buf[0:8] = struct.pack("<Q", at)
            struct.pack_into("<QQ", buf, 16, len(chars) // 2, capacity)
        base, region = self.p._region(off, 32)
        region[off - base: off - base + 32] = bytes(buf)
        return off

    def _fake_write(self, address, data):
        """FakeProcess 没有 k/handle，写入走它的 write_buffer（带回读校验）。"""
        if len(data) > 48:
            base, region = self.p._region(address, len(data))
            region[address - base: address - base + len(data)] = data
            return True
        self.p.write_buffer(address, data)
        return True

    def _table(self, translations):
        nt = liveparams.EffectNameTable(self.p, translations,
                                        profile=self.fake.profile)
        # 假模块文件只有 4KB，_in_flt 的区间要手工放大到 FLT 映射大小
        nt._flt_code = [(FLT_BASE, FLT_BASE + 0x10000)]
        patcher = mock.patch.object(liveparams.EffectNameTable, "_write",
                                    self._fake_write)
        patcher.start()
        self.addCleanup(patcher.stop)
        return nt

    def _field(self, obj):
        base, region = self.p._region(obj, 48)
        off = obj - base
        return bytes(region[off:off + 48])

    # ---- 用例 -----------------------------------------------------
    def test_heap_display_accepts_full_reported_capacity(self):
        obj = self._put_instance(0, "Long Effect")
        self._put_display(obj, "Long Effect", inline=False, capacity=15)
        nt = self._table({})
        name = "光" * 15
        self.assertTrue(nt.write_display(obj, name, expect="Long Effect"))
        self.assertEqual(nt.read_display(obj), name)

    def test_heap_display_records_utf16_units(self):
        obj = self._put_instance(0, "Long Effect")
        at = self._put_display(obj, "Long Effect", inline=False, capacity=15)
        nt = self._table({})
        name = "光" * 12 + "\U00020000"
        self.assertTrue(nt.write_display(obj, name, expect="Long Effect"))
        self.assertEqual(struct.unpack("<Q", self.p.read(at + 16, 8))[0], 14)
        self.assertEqual(nt.read_display(obj), name)

    def test_heap_display_still_rejects_names_over_capacity(self):
        obj = self._put_instance(0, "Long Effect")
        self._put_display(obj, "Long Effect", inline=False, capacity=15)
        nt = self._table({})
        self.assertFalse(nt.write_display(obj, "光" * 16, expect="Long Effect"))
        self.assertEqual(nt.read_display(obj), "Long Effect")

    def test_finds_instance_by_display_name_and_writes_chinese(self):
        """+0x10 是乱码也要能认出来 —— 名字看显示名缓存。"""
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 1, res)
        self.assertEqual(nt.read_display(obj), "辉光")
        self.assertGreaterEqual(nt.diag["inst"], 1)

    def test_never_writes_registry_entry(self):
        """同名同布局的注册表条目（vtable = flt+fc_vtable）一个字节都不能动。"""
        reg = self._put_instance(0, "S_Glow",
                                 vtable=FLT_BASE + self.fake.profile.fc_vtable,
                                 name_field="S_Glow")
        before = self._field(reg)
        disp_before = self.p.read(reg + self.fake.profile.display_cache, 32)
        inst = self._put_instance(1, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 1, res)
        self.assertEqual(nt.read_display(inst), "辉光")
        self.assertEqual(self._field(reg), before)     # 顶部菜单的数据源不被写
        self.assertEqual(self.p.read(reg + self.fake.profile.display_cache, 32),
                         disp_before)

    def test_never_writes_name_field(self):
        """★ 关键回归：+0x10 一个字节都不许写（写它会被 AE 存进工程）。"""
        obj = self._put_instance(0, "S_Glow")
        before = self._field(obj)
        nt = self._table({"S_Glow": "辉光"})
        res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 1, res)
        self.assertEqual(self._field(obj), before)
        self.assertEqual(nt.read_display(obj), "辉光")

    def test_remembered_instance_needs_no_rescan(self):
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        nt.apply(["S_Glow"])
        self.assertEqual(nt.read_display(obj), "辉光")
        # 把显示名改回英文（模拟 AE 自己重写），并禁止全量重扫
        self._put_display(obj, "S_Glow")
        liveparams._EFFECT_SCAN_AT[nt._key] = liveparams.time.monotonic()
        with mock.patch.object(nt, "scan", side_effect=AssertionError("不该重扫")):
            res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 1, res)
        self.assertEqual(nt.read_display(obj), "辉光")

    def test_new_instance_found_on_rescan(self):
        self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光", "CC Snowfall": "下雪"})
        nt.apply(["S_Glow"])
        obj2 = self._put_instance(1, "CC Snowfall")
        liveparams._EFFECT_SCAN_AT.pop(nt._key, None)
        res = nt.apply(["S_Glow", "CC Snowfall"])
        self.assertEqual(res["written"], 1, res)
        self.assertEqual(nt.read_display(obj2), "下雪")

    def test_uninitialised_display_cache_is_left_alone(self):
        """+display_off 那段没初始化(size=0)就绝不写（offset 猜错的安全阀）。"""
        obj = INST_BASE
        base, region = self.p._region(obj, 0x40)
        off = obj - base
        region[off:off + 8] = struct.pack(
            "<Q", FLT_BASE + self.fake.profile.root_vtable)
        self._put_name_field(obj, "S_Glow")     # 只有 +0x10 有名字
        disp_off = obj + self.fake.profile.display_cache
        before = self.p.read(disp_off, 32)
        nt = self._table({"S_Glow": "辉光"})
        res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 0, res)
        self.assertEqual(self.p.read(disp_off, 32), before)
        self.assertEqual(nt.read_display(obj), "")

    def test_display_cache_mismatch_is_not_written(self):
        """显示名缓存里不是 expect 时 write_display 拒绝写，只记 dispmiss。"""
        obj = self._put_instance(0, "S_Glow", name_field="S_Glow")
        self._put_display(obj, "别的名字")
        off = obj + self.fake.profile.display_cache
        before = self.p.read(off, 32)
        nt = self._table({"S_Glow": "辉光"})
        self.assertFalse(nt.write_display(obj, "辉光", expect="S_Glow"))
        self.assertEqual(self.p.read(off, 32), before)
        self.assertEqual(nt.diag["dispmiss"], 1)

    def test_translate_skips_already_chinese(self):
        nt = self._table({"S_Glow": "辉光"})
        self.assertEqual(nt.translate("辉光"), "")
        self.assertEqual(nt.translate("S_Glow"), "辉光")
        self.assertEqual(nt.translate("S_Other"), "")

    def test_instance_suffix_uses_known_base(self):
        nt = self._table({"S_Glow": "辉光"})
        self.assertEqual(nt.translate("S_Glow 2"), "辉光 2")
        self.assertEqual(nt.translate("S_Nope 2"), "")

    def test_known_english_accepts_translated_name(self):
        """回读时看到中文，也要能反查出英文名（否则关通道后还原不了）。"""
        nt = self._table({"S_Glow": "辉光"})
        self.assertEqual(nt.known_english("S_Glow"), "S_Glow")
        self.assertEqual(nt.known_english("辉光"), "S_Glow")
        self.assertEqual(nt.known_english("辉光 2"), "S_Glow 2")

    def test_ambiguous_chinese_is_not_guessed_or_written(self):
        obj = self._put_instance(0, "发光")
        nt = self._table({"Shine": "发光", "Glow": "发光"})
        self.assertEqual(nt.known_english("发光"), "")
        self.assertEqual(nt.known_english("发光 2"), "")
        self.assertEqual(nt.revert()["restored"], 0)
        self.assertEqual(nt.read_display(obj), "发光")

    def test_restore_writes_english_back(self):
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        res = nt.apply(["S_Glow"])
        nt.restore(res["records"])
        self.assertEqual(nt.read_display(obj), "S_Glow")

    def test_revert_restores_engine_chinese(self):
        """工程文件带进来的中文标题（不是本会话写的）也要还原成英文。"""
        obj = self._put_instance(0, "辉光")      # 显示名一开始就是中文
        nt = self._table({"S_Glow": "辉光"})
        res = nt.revert()
        self.assertGreaterEqual(res["restored"], 1, res)
        self.assertEqual(nt.read_display(obj), "S_Glow")
        self.assertEqual(self.p.read(obj + liveparams.EFFECT_NAME_OFF, 6),
                         bytes(range(0x80, 0x86)))          # +0x10 未动

    def test_revert_keeps_english_untouched(self):
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        res = nt.revert()
        self.assertEqual(res["restored"], 0, res)
        self.assertEqual(nt.read_display(obj), "S_Glow")

    def test_too_long_translation_is_not_written(self):
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉" * 30})   # 内联缓存只有 7 字符
        res = nt.apply(["S_Glow"])
        self.assertEqual(res["written"], 0, res)
        self.assertEqual(nt.read_display(obj), "S_Glow")

    def test_dead_object_is_forgotten(self):
        obj = self._put_instance(0, "S_Glow")
        nt = self._table({"S_Glow": "辉光"})
        nt.apply(["S_Glow"])
        memo_before = len(liveparams._EFFECT_OBJ_MEMO[nt._key])
        self.assertGreaterEqual(memo_before, 1)
        base, region = self.p._region(obj, 8)
        off = obj - base
        region[off:off + 8] = struct.pack("<Q", 0)   # 虚表被清 → 对象已释放
        nt.apply(["S_Glow"])
        left = {obj for obj, _r in liveparams._EFFECT_OBJ_MEMO.get(nt._key, ())}
        self.assertNotIn(obj, left)


if __name__ == "__main__":
    unittest.main()
