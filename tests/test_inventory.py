"""插件清单测试：分组、通道开关、预设、持久化。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.inventory import (  # noqa: E402
    ALL_CHANNELS, CH_EFFECT, CH_ENUM, CH_PARAM,
    MODE_ALL, MODE_EFFECT_ONLY, MODE_PARAM_ONLY,
    Inventory, PluginGroup, apply_mode, group_plugins, load_selection,
    save_selection,
)
from core.pipl import PluginInfo  # noqa: E402


def _info(path, name="X", match="", origin="pipl"):
    return PluginInfo(
        path=path, file_name=os.path.basename(path), display_name=name,
        match_name=match, name_origin=origin,
    )


class TestGrouping(unittest.TestCase):
    def test_single_file_becomes_group(self):
        groups = group_plugins([_info(r"C:\p\Sapphire\S_Glow.aex", "S_Glow")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].label, "S_Glow")

    def test_same_dir_same_prefix_merged(self):
        infos = [
            _info(r"C:\p\S\S_Glow.aex", "S_Glow"),
            _info(r"C:\p\S\S_Blur.aex", "S_Blur"),
        ]
        groups = group_plugins(infos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].file_count, 2)

    def test_different_prefix_not_merged(self):
        infos = [
            _info(r"C:\p\S\S_Glow.aex", "S_Glow"),
            _info(r"C:\p\S\Other.aex", "Other"),
        ]
        groups = group_plugins(infos)
        self.assertEqual(len(groups), 2)

    def test_different_dir_not_merged(self):
        infos = [
            _info(r"C:\p\A\S_Glow.aex", "S_Glow"),
            _info(r"C:\p\B\S_Blur.aex", "S_Blur"),
        ]
        self.assertEqual(len(group_plugins(infos)), 2)

    def test_effects_counts_only_pipl(self):
        g = PluginGroup(
            group_id="g", label="g",
            files=[
                _info(r"C:\p\a.aex", origin="pipl"),
                _info(r"C:\p\b.aex", origin="filename"),
            ],
        )
        self.assertEqual(g.effects, 1)

    def test_vendor_guess(self):
        g = group_plugins([_info(r"C:\MediaCore\BorisFX\BCC_Blur.aex", "BCC_Blur")])[0]
        self.assertEqual(g.vendor, "Boris FX")

    def test_builtin_group_flag(self):
        g = PluginGroup(
            group_id="g", label="g",
            files=[PluginInfo(path=r"C:\AE\Plug-ins\Effects\x.aex", file_name="x.aex",
                              match_name="ADBE X")],
        )
        self.assertTrue(g.builtin_adobe)


class TestChannels(unittest.TestCase):
    def test_default(self):
        """新分组默认开启参数和下拉，效果名写入需显式开启。"""
        g = PluginGroup(group_id="g", label="g")
        self.assertEqual(g.channels, (CH_PARAM, CH_ENUM))
        self.assertFalse(g.channel_effect)
        self.assertTrue(g.channel_param)
        self.assertTrue(g.channel_enum)

    def test_toggle(self):
        g = PluginGroup(group_id="g", label="g")
        g.toggle_channel(CH_EFFECT)
        self.assertTrue(g.channel_effect)
        self.assertTrue(g.channel_param)
        g.toggle_channel(CH_EFFECT)
        self.assertFalse(g.channel_effect)

    def test_toggle_unknown_ignored(self):
        g = PluginGroup(group_id="g", label="g")
        g.toggle_channel("bogus")
        self.assertEqual(g.channels, (CH_PARAM, CH_ENUM))

    def test_mode_effect_only(self):
        inv = Inventory(groups=[PluginGroup(group_id="a", label="a")])
        apply_mode(inv, MODE_EFFECT_ONLY)
        self.assertEqual(inv.groups[0].channels, (CH_EFFECT,))

    def test_mode_param_only(self):
        inv = Inventory(groups=[PluginGroup(group_id="a", label="a")])
        apply_mode(inv, MODE_PARAM_ONLY)
        self.assertEqual(set(inv.groups[0].channels), {CH_PARAM, CH_ENUM})
        self.assertNotIn(CH_EFFECT, inv.groups[0].channels)

    def test_mode_all(self):
        inv = Inventory(groups=[PluginGroup(group_id="a", label="a")])
        apply_mode(inv, MODE_EFFECT_ONLY)
        apply_mode(inv, MODE_ALL)
        self.assertEqual(inv.groups[0].channels, ALL_CHANNELS)


class TestSelectionPersistence(unittest.TestCase):
    def test_roundtrip(self):
        inv = Inventory(
            ae_version="2026", ae_root=r"C:\AE",
            groups=[
                PluginGroup(group_id="a", label="A", vendor="V", selected=True,
                            channels=(CH_EFFECT,)),
                PluginGroup(group_id="b", label="B", vendor="V"),
            ],
        )
        tmp = os.path.join(tempfile.mkdtemp(), "sel.json")
        save_selection(tmp, inv)

        inv2 = Inventory(
            ae_version="2026", ae_root=r"C:\AE",
            groups=[PluginGroup(group_id="a", label="A"), PluginGroup(group_id="b", label="B")],
        )
        hit = load_selection(tmp, inv2)
        self.assertEqual(hit, 2)
        self.assertTrue(inv2.groups[0].selected)
        self.assertEqual(inv2.groups[0].channels, (CH_EFFECT,))
        self.assertFalse(inv2.groups[1].selected)

    def test_missing_file(self):
        inv = Inventory(groups=[PluginGroup(group_id="a", label="A")])
        self.assertEqual(load_selection(os.path.join(tempfile.mkdtemp(), "nope.json"), inv), 0)

    def test_stats(self):
        inv = Inventory(
            scanned_files=10,
            groups=[
                PluginGroup(group_id="a", label="A", selected=True,
                            files=[_info(r"C:\p\a.aex")]),
                PluginGroup(group_id="b", label="B",
                            files=[_info(r"C:\p\b.aex")]),
            ],
        )
        s = inv.stats()
        self.assertEqual(s["groups"], 2)
        self.assertEqual(s["selected_groups"], 1)
        self.assertEqual(s["selected_files"], 1)
        self.assertEqual(s["selected_effects"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
