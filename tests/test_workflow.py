"""预检与效果名映射测试（全部在临时目录，不碰真 AE 文件）。

★ 2026-11-15：静态写盘（改 .aex）整层删除，对应的 aexwrite 测试一并移除。
  现在只覆盖：效果名解析、效果名映射、preflight 清单形状与阻塞/警告语义。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dictstore import DictStore  # noqa: E402
from core.effects import load_effect_names, resolve_effect_name  # noqa: E402
from core.inventory import Inventory, PluginGroup  # noqa: E402
from core.pipl import PluginInfo, read_plugin  # noqa: E402
from core.workflow import build_effect_mapping, preflight  # noqa: E402


def make_aex(name: str, match: str, pad: int = 0) -> bytes:
    """构造一个带 PiPL 的最小 .aex（仅用于预检的插件枚举）。"""
    def item(attr: str, value: bytes) -> bytes:
        body = bytes([len(value)]) + value + b"\x00" * pad
        return (b"MIB8" + attr.encode("ascii") + b"\x00\x00\x00\x00"
                + len(body).to_bytes(4, "little") + body)

    return b"FAKE" + item("eman", name.encode()) + item("ANMe", match.encode()) + b"TAIL"


class TestEffectNames(unittest.TestCase):
    def test_resolve_direct(self):
        self.assertEqual(resolve_effect_name("Blur", "", {"Blur": "模糊"}), "模糊")

    def test_resolve_strips_prefix_when_looking_up(self):
        """查表时去前缀，但【写回时补回】—— 见 test_resolve_keeps_vendor_prefix。"""
        self.assertEqual(resolve_effect_name("S_Blur", "", {"Blur": "模糊"}), "S_模糊")

    def test_resolve_via_match_name(self):
        self.assertEqual(
            resolve_effect_name("随便什么", "S_Blur", {"Blur": "模糊"}), "模糊"
        )

    def test_resolve_keeps_vendor_prefix(self):
        """★★ 回归：Sapphire 的前缀 S_ 必须保留。

        效果名表的键是 "ChannelSwitcher"（无前缀），
        但显示名是 "S_ChannelSwitcher"。旧版汉化显示 "S_通道切换"，
        若不补回前缀就变成 "通道切换" —— 用户一眼能看出不对。
        """
        self.assertEqual(
            resolve_effect_name("S_ChannelSwitcher", "S_ChannelSwitcher",
                                {"ChannelSwitcher": "通道切换"}),
            "S_通道切换",
        )
        self.assertEqual(
            resolve_effect_name("S_Blur", "S_Blur", {"Blur": "模糊"}),
            "S_模糊",
        )

    def test_resolve_does_not_double_prefix(self):
        """表里已经带前缀时不能重复加。"""
        self.assertEqual(
            resolve_effect_name("S_Blur", "S_Blur", {"Blur": "S_模糊"}),
            "S_模糊",
        )

    def test_non_sapphire_no_prefix(self):
        """没有厂商前缀的插件不受影响。"""
        self.assertEqual(
            resolve_effect_name("Glow", "Glow", {"Glow": "发光"}), "发光"
        )

    def test_resolve_miss(self):
        self.assertIsNone(resolve_effect_name("Nope", "", {"Blur": "模糊"}))

    def test_load_missing_file(self):
        self.assertEqual(load_effect_names(r"C:\nope.json"), {})


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bak = os.path.join(self.tmp, "bak")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inv(self, n=2):
        files = []
        for i in range(n):
            p = os.path.join(self.tmp, "S_Effect%d.aex" % i)
            with open(p, "wb") as f:
                f.write(make_aex("S_Effect%d" % i, "S_Effect%d" % i, pad=12))
            files.append(read_plugin(p))
        # 效果名写入端到端测试:显式开全通道(2026-11-02 起新分组默认效果名关)
        from core.inventory import ALL_CHANNELS

        return Inventory(
            ae_version="2026", ae_root=self.tmp,
            groups=[PluginGroup(group_id="g", label="G", files=files,
                                selected=True, channels=ALL_CHANNELS)],
            scanned_files=n,
        )

    def test_preflight_blocks_without_selection(self):
        inv = self._inv(1)
        inv.groups[0].selected = False
        store = DictStore()
        store.set_builtin({"S_Effect0": "效果零"})
        pf = preflight(inv, store)
        self.assertFalse(pf.ok)

    def test_preflight_passes_with_selection(self):
        # 2026-11-15：静态写盘删除后，preflight 只剩「引擎相关」的检查。
        # AE 是否运行不再是预检项（运行时通道恰恰需要 AE 开着）。
        from unittest import mock

        import core.compat
        inv = self._inv(1)
        store = DictStore()
        store.set_builtin({"S_Effect0": "效果零"})
        with mock.patch.object(core.compat, "check_plugin_dir", return_value=[]):
            pf = preflight(inv, store)
        self.assertTrue(pf.ok)
        labels = [i.label for i in pf.items]
        for gone in ("AE 未运行", "写入权限", "需要写盘的文件", "长度闸",
                     "备份目录可写"):
            self.assertNotIn(gone, labels)
        self.assertIn("已勾选插件", labels)
        self.assertIn("UTF-8 修复层", labels)

    def test_build_effect_mapping(self):
        store = DictStore()
        store.set_builtin({"Amount": "数量"})
        m = build_effect_mapping(store)
        self.assertEqual(m.get("Amount"), "数量")


if __name__ == "__main__":
    unittest.main(verbosity=2)