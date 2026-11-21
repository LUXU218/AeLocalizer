"""效果名列表模型测试：按插件套装分组 + 批量勾选（2026-10-23）。

覆盖：
  · 厂商 / 关键词 / 套装 三层筛选
  · 「全选当前筛选」
  · 套装分区「全选本组」
  · Shift+点击 连选（资源管理器式）
  · 选择跨筛选保持、刷新剪枝
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.namemodel import ROLES, NameListModel  # noqa: E402
from core.nameeditor import NameRow  # noqa: E402


def _rows():
    return [
        NameRow(en="BCC Alpha", zh="BCC 甲", slot=12, vendor="Boris FX",
                suite="Continuum"),
        NameRow(en="BCC Beta", zh="", slot=12, vendor="Boris FX",
                suite="Continuum"),
        NameRow(en="S_Glow", zh="辉光", slot=10, vendor="Sapphire",
                suite="Sapphire"),
        NameRow(en="S_Blur", zh="", slot=10, vendor="Sapphire",
                suite="Sapphire"),
        NameRow(en="uni.Foo", zh="", slot=10, vendor="其它第三方",
                suite="Universe"),
    ]


class TestNameModel(unittest.TestCase):
    def setUp(self):
        self.m = NameListModel()
        self.m.setRows(_rows())

    def test_row_count(self):
        self.assertEqual(self.m.rowCount(), 5)

    def test_vendor_filter(self):
        self.m.setFilter("", False, "Sapphire")
        self.assertEqual(self.m.rowCount(), 2)
        self.assertEqual(self.m.rowAt(0).en, "S_Glow")

    def test_keyword_plus_vendor_filter(self):
        self.m.setFilter("BCC", False, "Boris FX")
        self.assertEqual(self.m.rowCount(), 2)
        self.m.setFilter("BCC", False, "Sapphire")
        self.assertEqual(self.m.rowCount(), 0)

    def test_select_all_only_affects_visible(self):
        self.m.setFilter("", False, "Boris FX")
        changed = self.m.setAllVisible(True)
        self.assertEqual(changed, 2)
        self.assertEqual(self.m.selectedCount, 2)
        self.assertEqual(self.m.selectedEn(), ["BCC Alpha", "BCC Beta"])

    # ---------- 套装分区 ----------

    def test_suite_role_and_order(self):
        # 第 0/1 行是 Continuum，第 2/3 行是 Sapphire —— 同套装必须是连续的
        self.assertEqual(self.m.data(self.m.index(0, 0), ROLES["suite"]), "Continuum")
        self.assertEqual(self.m.data(self.m.index(2, 0), ROLES["suite"]), "Sapphire")

    def test_suite_counts(self):
        counts = self.m.suiteCounts()
        self.assertEqual(counts.get("Continuum"), 2)
        self.assertEqual(counts.get("Sapphire"), 2)
        self.assertEqual(counts.get("Universe"), 1)
        # 筛选后只数可见的
        self.m.setFilter("BCC", False, "")
        self.assertEqual(self.m.suiteCounts().get("Continuum"), 2)
        self.assertNotIn("Sapphire", self.m.suiteCounts())

    def test_select_suite_only_that_suite(self):
        n = self.m.selectSuite("Sapphire", True)
        self.assertEqual(n, 2)
        self.assertEqual(self.m.selectedEn(), ["S_Blur", "S_Glow"])
        # 再取消
        self.assertEqual(self.m.selectSuite("Sapphire", False), 2)
        self.assertEqual(self.m.selectedCount, 0)

    def test_select_suite_respects_filter(self):
        self.m.setFilter("", False, "Sapphire")
        self.assertEqual(self.m.selectSuite("Sapphire", True), 2)
        self.assertEqual(self.m.selectSuite("Continuum", True), 0)  # 被筛掉了

    # ---------- Shift 连选 ----------

    def test_shift_click_selects_range(self):
        self.m.toggleSelectedByEn("BCC Alpha", False)      # 普通点击，定锚点
        self.assertEqual(self.m.selectedCount, 1)
        self.m.toggleSelectedByEn("S_Glow", True)          # Shift 点第 3 行
        self.assertEqual(self.m.selectedEn(),
                         ["BCC Alpha", "BCC Beta", "S_Glow"])

    def test_shift_click_deselects_range(self):
        self.m.setAllVisible(True)
        self.assertEqual(self.m.selectedCount, 5)
        self.m.toggleSelectedByEn("BCC Alpha", False)      # 取消并定锚点
        self.m.toggleSelectedByEn("S_Blur", True)          # Shift 点：这段都取消
        self.assertEqual(self.m.selectedEn(), ["uni.Foo"])

    def test_shift_without_anchor_falls_back(self):
        self.m.toggleSelectedByEn("S_Glow", True)          # 没有锚点 -> 当普通点击
        self.assertEqual(self.m.selectedEn(), ["S_Glow"])

    def test_anchor_survives_filter_change(self):
        self.m.toggleSelectedByEn("BCC Alpha", False)
        self.m.setFilter("S", False, "")                   # BCC 被筛掉，锚点不可见
        self.m.toggleSelectedByEn("S_Blur", True)          # 锚点不可见 -> 普通点击
        self.assertEqual(self.m.selectedEn(), ["BCC Alpha", "S_Blur"])

    # ---------- 其它 ----------

    def test_selection_survives_filter_change(self):
        self.m.setSelectedByEn("S_Glow", True)
        self.m.setFilter("BCC", False, "")
        self.assertEqual(self.m.rowCount(), 2)
        self.assertEqual(self.m.selectedCount, 1)
        self.m.setFilter("", False, "")
        self.assertEqual(self.m.selectedCount, 1)

    def test_toggle_and_clear(self):
        self.m.setSelectedByEn("BCC Alpha", True)
        self.m.setSelectedByEn("BCC Alpha", False)
        self.assertEqual(self.m.selectedCount, 0)
        self.m.setAllVisible(True)
        self.m.clearSelection()
        self.assertEqual(self.m.selectedCount, 0)

    def test_selected_role_for_qml(self):
        self.m.setSelectedByEn("BCC Alpha", True)
        idx = self.m.index(0, 0)
        self.assertTrue(self.m.data(idx, ROLES["selected"]))
        idx2 = self.m.index(1, 0)
        self.assertFalse(self.m.data(idx2, ROLES["selected"]))

    def test_setrows_prunes_dead_selection(self):
        self.m.setSelectedByEn("uni.Foo", True)
        self.m.setRows(_rows()[:2])
        self.assertEqual(self.m.selectedCount, 0)

    def test_vendors_counted(self):
        vs = {v["value"]: v["count"] for v in self.m.vendors()}
        self.assertEqual(vs.get("Boris FX"), 2)
        self.assertEqual(vs.get("Sapphire"), 2)
        self.assertEqual(self.m.vendors()[0]["value"], "Boris FX")


if __name__ == "__main__":
    unittest.main(verbosity=2)
