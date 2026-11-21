# -*- coding: utf-8 -*-
"""合并 language 通道：tuple 语义、空选区、旧分裂迁移。"""
import json
import os
import tempfile
import unittest

from core.inventory import (ALL_CHANNELS, CH_EFFECT, CH_ENUM, CH_PARAM,
                            Inventory, PluginGroup, load_selection,
                            set_all_channels)


def _inv(*channels):
    return Inventory(groups=[PluginGroup("g%d" % i, "G", channels=tuple(c))
                             for i, c in enumerate(channels)])


class InventoryChannelTest(unittest.TestCase):
    def test_toggle_param_and_enum_together(self):
        g = PluginGroup("g", "G")
        g.channels = ()
        g.toggle_channel("param")
        self.assertEqual(set(g.channels), {CH_PARAM, CH_ENUM})
        g.toggle_channel("enum")
        self.assertEqual(g.channels, ())
        g.toggle_channel("effect")
        self.assertEqual(g.channels, (CH_EFFECT,))
        g.toggle_channel("effect")
        self.assertEqual(g.channels, ())

    def test_all_off_stays_off(self):
        inv = _inv((CH_PARAM, CH_ENUM), (CH_EFFECT,))
        set_all_channels(inv, False, False, False)
        self.assertTrue(all(g.channels == () for g in inv.groups))
        set_all_channels(inv, False, True, False)   # 旧入口：语言 ON 即配对
        self.assertTrue(all(set(g.channels) == {CH_PARAM, CH_ENUM}
                            for g in inv.groups))

    def test_load_selection_empty_and_split(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_invchan_")
        p = os.path.join(tmp, "sel.json")
        inv = _inv((), (CH_EFFECT,), (CH_ENUM,), (CH_PARAM,))
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"groups": [
                {"id": "g0", "selected": True, "channels": []},
                {"id": "g1", "selected": True, "channels": ["effect"]},
                {"id": "g2", "selected": True, "channels": ["enum"]},
                {"id": "g3", "selected": True, "channels": ["param"]},
            ]}, f)
        load_selection(p, inv)
        self.assertEqual(inv.groups[0].channels, ())
        self.assertEqual(inv.groups[1].channels, (CH_EFFECT,))
        # 旧分裂以 param 为主：只有 enum 的历史组迁移为全关
        self.assertEqual(inv.groups[2].channels, ())
        self.assertEqual(set(inv.groups[3].channels), {CH_PARAM, CH_ENUM})
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
