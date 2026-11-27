# -*- coding: utf-8 -*-
"""parade 根向量发现路径的单测：新加效果必须在下一拍就被看到。

★ 这是「加了效果要等两秒以上才变中文」那个问题的回归闸：
   - 老路径靠「私有堆新提交页」的差分猜新流 —— 堆会复用已提交内存，
     新效果经常一页都不新增，于是几个小时都发现不了；
   - 新路径读 parade 对象自己的效果流向量 —— 那是工程里现存效果的
     权威清单，每拍读一次只要几次跨进程小读（毫秒级）。
"""
import os
import struct
import sys
import tempfile
import shutil
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import liveparams  # noqa: E402

from fake_ae import (  # noqa: E402
    FLT_BASE, HEAP_BASE, HEAP_SIZE, PARADE_ADDR, ROOT_BASE, ROOT_STRIDE, FakeAE,
)


def _fake_query(fake):
    from core.proc import MBI

    spans = sorted((base, len(data)) for base, data in fake.regions.items())
    top = max(base + size for base, size in spans)

    def query(_p, address):
        if address >= top:
            return None
        for base, size in spans:
            if base + size > address:
                m = MBI()
                if address < base:
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

    return query


class ParadeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_parade_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeAE(self.tmp, with_params=True)
        patcher = mock.patch.object(liveparams, "virtual_query", _fake_query(self.fake))
        patcher.start()
        self.addCleanup(patcher.stop)
        # 会话级缓存全部清掉：每个用例的临时目录不同，但模块表是全局的
        for table in (liveparams._PARADES_MEMO, liveparams._PARADES_AT,
                      liveparams._PARADE_FAILS, liveparams._PARADE_FAIL_AT,
                      liveparams._ROOTS_MEMO, liveparams._ROOTS_AT,
                      liveparams._REGIONS_MEMO, liveparams._HITS_MEMO,
                      liveparams._LAYOUT_MEMO, liveparams._LAYOUT_OK,
                      liveparams._LAYOUT_FAILS, liveparams._LAYOUT_RETRY_AT,
                      liveparams._MODULES_MEMO, liveparams._PROFILE_MEMO):
            table.clear()
        self.p = self.fake.process()
        self.prof = self.fake.profile

    # ---- 构造工具 ----
    def _live(self, translations=None):
        lp = liveparams.LiveParams(
            self.p, translations or {}, profile=self.prof)
        # 布局校验本身要全堆扫一次，本文件只测发现路径，直接给结论
        liveparams._LAYOUT_MEMO[lp._key] = (
            self.prof.display_cache, self.prof.root_vtable, "test")
        lp.display_off = self.prof.display_cache
        lp.root_vt = self.prof.root_vtable
        lp._layout_done = True
        # 假模块文件只有 4KB，_in_code 判据按真实模块尺寸来（vtable RVA 是 0x2010）
        lp.code = [(FLT_BASE, FLT_BASE + 0x10000),
                   (FLT_BASE + 0x100000, FLT_BASE + 0x110000)]
        return lp

    def _put(self, address, raw):
        base = HEAP_BASE if address >= HEAP_BASE else FLT_BASE
        region = self.fake.heap if base == HEAP_BASE else self.fake.flt
        off = address - base
        region[off:off + len(raw)] = raw

    def _fake_write(self, address, data):
        """跨进程写：假进程没有 WriteProcessMemory，直接落到稀疏内存上。"""
        self._put(address, data)
        return bytes(self.fake.process().read(address, len(data))) == data

    def _put_display(self, stream, text):
        """给流对象摆一个显示名缓存（短名走 SSO 内联）。"""
        off = stream - HEAP_BASE + self.prof.display_cache
        chars = text.encode("utf-16-le")
        cache = bytearray(32)
        if len(text) <= liveparams.INLINE_CAP:
            cache[0:len(chars)] = chars
            cap = len(text)
        else:
            addr = HEAP_BASE + 0x1F0000
            self._put(addr, chars + b"\x00\x00")
            cache[0:8] = struct.pack("<Q", addr)
            cap = len(text) + 1
        struct.pack_into("<QQ", cache, 16, len(text), cap)
        self.fake.heap[off:off + 32] = bytes(cache)

    def _add_root(self, index, match):
        """在 parade 根向量尾部追加一个效果流（模拟"用户刚加了个效果"）。"""
        root = ROOT_BASE + index * ROOT_STRIDE
        self._put(root, struct.pack("<Q", FLT_BASE + self.prof.root_vtable))
        self._put(root + 32, struct.pack("<Q", PARADE_ADDR))
        vec = HEAP_BASE + 0x360000
        self._put(vec + index * 8, struct.pack("<Q", root))
        end = vec + (index + 1) * 8
        # 更新 parade+group_vector 的 end（begin 不动）
        self._put(PARADE_ADDR + self.prof.group_vector + 8, struct.pack("<Q", end))
        return root

    # ---- 用例 ----
    def test_discover_finds_parade_vector(self):
        lp = self._live()
        found = lp.discover_parades()
        self.assertIn(PARADE_ADDR, found)
        roots = lp.roots_from_parades()
        self.assertEqual(len(roots), len(self.fake.effects))
        self.assertEqual(sorted(roots), sorted(
            ROOT_BASE + i * ROOT_STRIDE for i in range(len(self.fake.effects))))

    def test_new_effect_seen_on_next_tick_without_rescan(self):
        """第二拍之前往根向量里追加一个流 —— 下一拍必须直接看到，且不扫堆。"""
        lp = self._live()
        lp.discover_parades()
        before = lp.roots_from_parades()
        self.assertEqual(len(before), len(self.fake.effects))

        new_root = self._add_root(len(self.fake.effects), "S_NewFX")
        self._put_display(new_root, "S_NewFX")

        with mock.patch.object(liveparams, "scan_heap",
                               side_effect=AssertionError("不该再全堆扫描")):
            after = lp.walk(None, expand=False)
        streams = {s.stream for s in after}
        self.assertIn(new_root, streams)

    def test_effect_table_second_on_sees_new_parade_root(self):
        """★ 原始「第二遍不汉化」产品回归：第二轮 ON 必须看到第一轮新加对象。

        预热 _WALK_CACHE 只含初始根流 → ON 期间向同一 parade 追加新流 →
        OFF/revert → 第二次 ON（EffectNameTable 真实 apply 路径）必须把新流
        也写回中文，且带 " 3" 序号，全程不重新全堆扫描。
        """
        # 先建立 parade 记忆，并只预热初始根流
        seed = self._live()
        seed.discover_parades()
        roots = seed.roots_from_parades()
        for root in roots:
            self._put_display(root, "S_Glow")

        table = {"S_Glow": "S_发光"}
        nt = liveparams.EffectNameTable(self.p, table, profile=self.prof)
        nt._flt_code = [(FLT_BASE, FLT_BASE + 0x10000)]
        patcher = mock.patch.object(liveparams.EffectNameTable, "_write",
                                    self._fake_write)
        patcher.start()
        self.addCleanup(patcher.stop)
        old_memo = dict(liveparams._EFFECT_OBJ_MEMO)
        liveparams._EFFECT_OBJ_MEMO.clear()
        self.addCleanup(lambda: (liveparams._EFFECT_OBJ_MEMO.clear(),
                                 liveparams._EFFECT_OBJ_MEMO.update(old_memo)))

        # 模拟第 1 轮缓存：只有初始对象
        nt._current_roots()
        liveparams._WALK_CACHE[int(self.p.pid)] = {
            "streams": list(roots), "t": 0.0, "tried": 0.0}

        # ON 期间新增同 parade 的实例（带 3 序号），不重扫堆
        new_root = self._add_root(len(self.fake.effects), "S_Glow 3")
        self._put_display(new_root, "S_Glow 3")
        with mock.patch.object(nt, "scan",
                               side_effect=AssertionError("不该全堆扫描")):
            res1 = nt.apply(["S_Glow"])
        self.assertEqual(nt.read_display(new_root), "S_发光 3", res1)

        # OFF：全部还原英文（revert 也会刷新当前 parade 根向量）
        nt.revert()
        self.assertEqual(nt.read_display(new_root), "S_Glow 3")

        # 第二次 ON：缓存里仍可能只有旧 roots，但必须重新从 parade 读到新流
        with mock.patch.object(nt, "scan",
                               side_effect=AssertionError("第二次 ON 不该全堆扫描")):
            res2 = nt.apply(["S_Glow"])
        self.assertEqual(nt.read_display(new_root), "S_发光 3", res2)
        self.assertIn(new_root, {e.obj for e in nt._walk_pass()})

    def test_apply_writes_display_cache_and_never_touches_registry(self):
        """★ 核心回归：只改流上的显示名缓存，FLT_FCSpec 注册表 +0x10 一个字节不动。

        注册表那一段就是 AE 顶部「效果」菜单和「效果」面板的名字来源
        （IG5: FLT_FCSpec::GetEffectName_MB @0x180066b90 = return this+0x10），
        改写注册表会同时翻译菜单，因此这里仅修改实例的面板名称。
        """
        # 给第一个效果根流摆一个可换的英文名
        root0 = ROOT_BASE
        self._put_display(root0, "S_Glow")
        lp = self._live({"S_Glow": "\u8f89\u5149"})
        lp.discover_parades()

        before = self.fake.display_name_bytes(0)   # 注册表里那份英文名
        with mock.patch.object(liveparams.LiveParams, "_write", self._fake_write):
            res = lp.apply(None, expand=False)
        self.assertGreaterEqual(res["written"], 1)
        self.assertEqual(lp.read_display(root0), "\u8f89\u5149")
        self.assertEqual(self.fake.display_name_bytes(0), before,
                         "注册表被写了 —— 顶部效果菜单会跟着变中文")

    def test_root_vt_wrong_falls_back_to_name_anchor(self):
        """★ 核心回归（2026-11-07 真机）：root_vt 猜错时必须退回按名字锚定。

        现场：AE 2025 上 profile 给的 root_vtable 是错的（或校准没跑），
        needle 一个流都锚不到 → parade 找不到 → roots 为空 → walk() 返回 0 槽
        → apply() 报「效果标题已经是中文」，而面板里其实还是英文。
        这条路只依赖 display_off（校准就是用它投票的那个值），是 root_vt
        失效时唯一还能改到「效果控件」标题的路径。
        """
        # fake.effects 是 (显示名=中文, matchName=英文)；流上的名字按英文原名摆
        for index, (_display, match, _cat) in enumerate(self.fake.effects):
            self._put_display(ROOT_BASE + index * ROOT_STRIDE, match)
        lp = self._live({"S_Glow": "\u8f89\u5149"})
        lp.root_vt = 0x999999                      # 校准/适配给错的 vtable
        with mock.patch.object(liveparams.LiveParams, "_write", self._fake_write):
            res = lp.apply(["S_Glow"], expand=False)
        self.assertGreaterEqual(res["written"], 1)
        self.assertEqual(lp.read_display(ROOT_BASE), "\u8f89\u5149")
        self.assertEqual(lp.diag.get("roots_src"), "names",
                         "应当记录成「按名字锚定」这条兜底路径")

    def test_full_scan_is_throttled(self):
        """名字兜底要全量扫私有堆，0.4 秒一拍绝不能每拍都扫。"""
        lp = self._live()
        self.assertTrue(lp._may_full_scan())
        self.assertFalse(lp._may_full_scan(), "同一拍内不该连续全量扫")

    def test_translate_skips_already_chinese(self):
        lp = self._live({"S_Glow": "\u8f89\u5149"})
        self.assertEqual(lp.translate("\u8f89\u5149"), "")
        self.assertEqual(lp.translate("S_Glow"), "\u8f89\u5149")

    def test_instance_suffix_uses_known_base(self):
        lp = self._live({"CC Light Rays": "CC \u5149\u7ebf"})
        self.assertEqual(lp.translate("CC Light Rays 2"), "CC \u5149\u7ebf 2")
        # base 不在已知英文名里就不拆（"BCC Noise Map 2" 是产品名）
        self.assertEqual(lp.translate("BCC Noise Map 2"), "")


if __name__ == "__main__":
    unittest.main()
