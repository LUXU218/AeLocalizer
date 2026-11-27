"""词典浏览/编辑（DictListModel + AppBackend 词典槽）测试。

★★ 沙盒纪律（2026-10-23 事故后）：
  涉及 AppBackend 的用例必须把 backend.DATA_ROOT 换成临时目录，
  绝不允许落到真实 %LOCALAPPDATA%\\AeLocalizer 上。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


def _sample_rows():
    return [
        {"en": "Gaussian Blur", "zh": "高斯模糊", "layer": "builtin", "gauge": "g1"},
        {"en": "BCC 2D Particles", "zh": "BCC 二维粒子", "layer": "pack", "gauge": "g2"},
        {"en": "Auto", "zh": "自动", "layer": "user_locked", "gauge": "g3"},
    ]


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestDictListModel(unittest.TestCase):
    """纯模型测试：过滤 / 单行更新 / 删除。"""

    def _model(self):
        from app.dictmodel import DictListModel

        m = DictListModel()
        m.setRows(_sample_rows())
        return m

    def test_rowcount_and_layer_label(self):
        from app.dictmodel import ROLES

        m = self._model()
        self.assertEqual(m.rowCount(), 3)
        self.assertEqual(m.data(m.index(0, 0), ROLES["en"]), "Gaussian Blur")
        self.assertEqual(m.data(m.index(0, 0), ROLES["layerLabel"]), "内置")
        self.assertEqual(m.data(m.index(1, 0), ROLES["layerLabel"]), "词典包")
        self.assertEqual(m.data(m.index(2, 0), ROLES["layerLabel"]), "已锁定")

    def test_filter_keyword_en_and_zh(self):
        m = self._model()
        m.setFilter("bcc")
        self.assertEqual(m.rowCount(), 1)
        m.setFilter("粒子")  # 中文也匹配
        self.assertEqual(m.rowCount(), 1)
        m.setFilter("不存在的词")
        self.assertEqual(m.rowCount(), 0)
        m.setFilter("")
        self.assertEqual(m.rowCount(), 3)

    def test_filter_survives_setrows(self):
        m = self._model()
        m.setFilter("bcc")
        m.setRows(_sample_rows())
        self.assertEqual(m.rowCount(), 1)

    def test_update_entry(self):
        from app.dictmodel import ROLES

        m = self._model()
        m.setFilter("gauss")  # Auto 被过滤藏起来
        self.assertEqual(m.rowCount(), 1)
        self.assertTrue(m.updateEntry("Auto", "自动2", "user_locked", "g9"))
        self.assertEqual(m.rowCount(), 1)  # 可见行数不变
        m.setFilter("")
        self.assertEqual(m.data(m.index(2, 0), ROLES["zh"]), "自动2")
        self.assertEqual(m.data(m.index(2, 0), ROLES["gauge"]), "g9")

    def test_remove_entry(self):
        m = self._model()
        self.assertTrue(m.removeEntry("Auto"))
        self.assertEqual(m.rowCount(), 2)
        self.assertFalse(m.removeEntry("不存在的键"))
        self.assertEqual(m.rowCount(), 2)


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestBackendDict(unittest.TestCase):
    """AppBackend 词典槽：保存 / 新增 / 清空回退 / 批量复制。"""

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile

        import app.backend as backend_mod

        cls._tmpdir = tempfile.mkdtemp(prefix="aeloc_test_dict_")
        cls._old_root = getattr(backend_mod, "DATA_ROOT", "")
        backend_mod.DATA_ROOT = cls._tmpdir

        from app.backend import AppBackend

        cls.b = AppBackend()

    @classmethod
    def tearDownClass(cls):
        import shutil

        import app.backend as backend_mod

        if cls._old_root:
            backend_mod.DATA_ROOT = cls._old_root
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _row(self, en):
        m = self.b.dictModel
        i = m.indexOfEn(en)
        return None if i < 0 else m.entryAt(i)

    def _refresh(self):
        """模拟用户切到词典子页（此时才全量建模，编辑时靠单行更新）。"""
        self.b.refreshDict()

    def test_save_new_entry_writes_locked_layer(self):
        self._refresh()
        msg = self.b.saveDictEntry("Foo Bar", "福 吧")
        self.assertTrue(msg.startswith("已保存"), msg)
        # 新词条不在当前模型里（新增走 addDictEntry 后整表重载），刷新后可见
        self._refresh()
        e = self._row("Foo Bar")
        self.assertIsNotNone(e)
        self.assertEqual(e.zh, "福 吧")
        self.assertEqual(e.layer, "user_locked")
        # 落盘校验
        from core.dictstore import DictStore

        st = DictStore.load(os.path.join(self._tmpdir, "store.json"))
        self.assertEqual(st.lookup("Foo Bar").zh, "福 吧")

    def test_edit_then_clear_reverts_to_lower_layer(self):
        self.b._store.set_builtin({"Blur Amount": "模糊量"})
        self.b.refreshDict()
        self.b.saveDictEntry("Blur Amount", "模糊数量")
        e = self._row("Blur Amount")
        self.assertEqual(e.zh, "模糊数量")
        self.assertEqual(e.layer, "user_locked")
        # 清空 = 恢复词典原值（builtin 层）
        self.b.saveDictEntry("Blur Amount", "")
        e = self._row("Blur Amount")
        self.assertIsNotNone(e)
        self.assertEqual(e.zh, "模糊量")
        self.assertEqual(e.layer, "builtin")

    def test_release_dictionary_rebuilds_current_rows_and_preserves_saved_edits(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        import app.backend as backend_mod
        from app.dictmodel import ROLES
        from core.dictstore import DictStore

        with (TemporaryDirectory() as tmp,
              patch.object(backend_mod, "DATA_ROOT", tmp),
              patch.object(backend_mod, "find_ae_installs", return_value=[]),
              patch.object(backend_mod.engine, "check", return_value=backend_mod.engine.EngineStatus()),
              patch.object(backend_mod.engine, "background_language_session", return_value=None)):
            backend = backend_mod.AppBackend()
            backend._store.set_builtin({"Blur Radius": "模糊半径", "Display Mode": "显示模式"})
            model = backend.dictModel
            backend.refreshDict()
            backend.filterDict("Radius")
            self.assertEqual(model.rowCount(), 1)
            with patch.object(backend, "_hot_reload_notify"):
                self.assertTrue(backend.saveDictEntry("Blur Radius", "模糊距离").startswith("已保存"))
            saved_path = Path(tmp, "store.json")
            saved_bytes = saved_path.read_bytes()
            revision = backend._store.revision

            backend.release_workspace_dictionary()
            self.assertEqual(model.rowCount(), 0)
            self.assertIsNone(backend._store._resolution_cache)
            self.assertEqual(backend._store.revision, revision)
            self.assertEqual(backend._store.lookup("Blur Radius").zh, "模糊距离")
            self.assertEqual(model.rowCount(), 0)

            self.assertIn("共 2 条", backend.refreshDict())
            self.assertEqual(model.rowCount(), 2)
            row = model.index(model.indexOfEn("Blur Radius"), 0)
            self.assertEqual(model.data(row, ROLES["zh"]), "模糊距离")
            self.assertEqual(model.data(row, ROLES["layer"]), "user_locked")
            self.assertEqual(saved_path.read_bytes(), saved_bytes)
            self.assertEqual(DictStore.load(str(saved_path)).lookup("Blur Radius").zh, "模糊距离")

    def test_clear_user_created_entry_removes_row(self):
        self.b.saveDictEntry("Only Mine", "我的词条")
        self._refresh()
        self.assertIsNotNone(self._row("Only Mine"))
        self.b.saveDictEntry("Only Mine", "")
        self.assertIsNone(self._row("Only Mine"))  # 用户自建词条，清空即消失

    def test_add_entry_validation(self):
        self.assertTrue(self.b.addDictEntry("", "x").startswith("请填写英文原名"))
        self.assertTrue(self.b.addDictEntry("New Key", "").startswith("请填写中文译名"))
        msg = self.b.addDictEntry("New Key", "新词条")
        self.assertTrue(msg.startswith("已保存"), msg)
        self._refresh()
        e = self._row("New Key")
        self.assertIsNotNone(e)
        self.assertEqual(e.zh, "新词条")

    def test_save_missing_en_rejected(self):
        self.assertTrue(self.b.saveDictEntry("", "x").startswith("缺少英文原名"))

    def test_dict_changed_signal_fired(self):
        hits = []
        conn = self.b.dictChanged.connect(lambda: hits.append(1))
        try:
            self.b.saveDictEntry("Signal Probe", "信号")
            self.assertGreaterEqual(len(hits), 1)
        finally:
            self.b.dictChanged.disconnect(conn)

    # ---------------- 批量复制 ----------------

    def test_copy_selected_names_and_pairs(self):
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh="一"),
            NameRow(en="B Two", zh="二"),
        ])
        self.b.toggleNameSelected("A One", False)
        self.b.toggleNameSelected("B Two", False)

        text = self.b.copySelectedNames()
        self.assertEqual(text, "A One\nB Two")

        pairs = self.b.copySelectedNamePairs()
        self.assertEqual(pairs, "A One\t一\nB Two\t二")

        self.b.clearNameSelection()
        self.assertEqual(self.b.copySelectedNames(), "")
        self.assertEqual(self.b.copySelectedNamePairs(), "")

    def test_copy_selected_translations(self):
        """译名列框选复制：一行一个中文（2026-10-27 用户要求）。"""
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh="一"),
            NameRow(en="B Two", zh="二"),
        ])
        self.b.toggleNameSelected("A One", False)
        self.b.toggleNameSelected("B Two", False)
        try:
            self.assertEqual(self.b.copySelectedTranslations(), "一\n二")
            # 没选时拒绝
            self.b.clearNameSelection()
            self.assertEqual(self.b.copySelectedTranslations(), "")
        finally:
            self.b.clearNameSelection()

    def test_copy_pairs_falls_back_to_store(self):
        """未翻译的行复制出来的译文就是空串（不会瞎编）。"""
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([NameRow(en="C Three", zh="")])
        self.b.toggleNameSelected("C Three", False)
        try:
            pairs = self.b.copySelectedNamePairs()
            self.assertEqual(pairs, "C Three\t")
        finally:
            self.b.clearNameSelection()

    def test_copy_hidden_selected_row_not_lost(self):
        """被筛选藏起来的勾选行不能丢（排在复制结果最后）。"""
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh="一"),
            NameRow(en="B Two", zh="二"),
        ])
        self.b.toggleNameSelected("A One", False)
        self.b.toggleNameSelected("B Two", False)
        try:
            self.b._nameModel.setFilter("B")   # A One 被藏起来
            self.assertEqual(self.b.copySelectedNames(), "B Two\nA One")
        finally:
            self.b._nameModel.setFilter("")
            self.b.clearNameSelection()

    # ---------------- 批量写入（2026-10-25 用户要求） ----------------

    def _two_rows(self):
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh=""),
            NameRow(en="B Two", zh=""),
        ])

    def test_batch_write_tab_pairs_no_selection_needed(self):
        self._two_rows()
        try:
            msg = self.b.batchWriteNames("A One\t新一\nB Two\t新二")
            self.assertTrue(msg.startswith("已写入 2"), msg)
            zm = self.b._nameModel.zhMap()
            self.assertEqual(zm["A One"], "新一")
            self.assertEqual(zm["B Two"], "新二")
            from core.dictstore import DictStore

            st = DictStore.load(os.path.join(self._tmpdir, "store.json"))
            r = st.lookup("A One")
            self.assertEqual(r.zh, "新一")
            self.assertTrue(r.locked)
        finally:
            self.b.clearNameSelection()

    def test_batch_write_zh_only_follows_selection_order(self):
        self._two_rows()
        self.b.toggleNameSelected("A One", False)
        self.b.toggleNameSelected("B Two", False)
        try:
            msg = self.b.batchWriteNames("甲\n乙")
            self.assertTrue(msg.startswith("已写入 2"), msg)
            zm = self.b._nameModel.zhMap()
            self.assertEqual(zm["A One"], "甲")   # 按显示顺序第一行
            self.assertEqual(zm["B Two"], "乙")
        finally:
            self.b.clearNameSelection()

    def test_batch_write_pure_english_becomes_selection(self):
        """纯英文名单不再被拒绝 —— 转成按名单勾选（2026-10-25 用户反馈）。"""
        self._two_rows()
        self.b.clearNameSelection()
        msg = self.b.batchWriteNames("A One\nB Two")
        self.assertIn("按名单勾选 2 行", msg)
        self.assertEqual(self.b._nameModel.selectedEnOrdered(),
                         ["A One", "B Two"])
        self.b.clearNameSelection()

    def test_batch_write_english_list_then_zh_in_one_paste(self):
        """名单 + 纯中文一锅粘：先按名单勾选，中文再按显示顺序写入。"""
        self._two_rows()
        self.b.clearNameSelection()
        msg = self.b.batchWriteNames("A One\nB Two\n甲\n乙")
        self.assertTrue(msg.startswith("已写入 2"), msg)
        self.assertIn("按名单勾选 2 行", msg)
        zm = self.b._nameModel.zhMap()
        self.assertEqual(zm["A One"], "甲")
        self.assertEqual(zm["B Two"], "乙")
        self.b.clearNameSelection()

    def test_batch_write_unmatched_names_reported(self):
        self._two_rows()
        self.b.clearNameSelection()
        msg = self.b.batchWriteNames("NoSuch Effect")
        self.assertIn("没有能在效果名编辑器中匹配的行", msg)
        self.assertEqual(self.b._nameModel.selectedCount, 0)

    def test_batch_write_empty(self):
        msg = self.b.batchWriteNames("  \n \n")
        self.assertIn("没有可写入", msg)

    def test_preview_batch_write_counts(self):
        self._two_rows()
        self.b.clearNameSelection()
        self.b.toggleNameSelected("A One", False)
        try:
            pv = self.b.previewBatchWrite(
                "A One\nB Two",
                "A One\t新一\n甲\nBlur Radius\t模糊\nGibberish")
            self.assertEqual(pv["left"], 2)
            # 「A One\t新一」「Blur Radius\t模糊」自带英文名（后者不在列表里）
            self.assertEqual(pv["pairs"], 2)
            self.assertEqual(pv["pairsUnknown"], 1)
            self.assertEqual(pv["zhMapped"], 1)    # 甲 -> 左栏第 1 行
            self.assertEqual(pv["zhSkipped"], 0)
            # Gibberish 是纯英文行 -> 右栏里算未识别
            self.assertEqual(pv["unknown"], 1)
            pv_empty = self.b.previewBatchWrite("", "")
            self.assertEqual(pv_empty["pairs"], 0)
        finally:
            self.b.clearNameSelection()

    # ---------------- 两栏批量写入（2026-10-26 弹层改版） ----------------

    def test_write_popup_left_anchor_mapping(self):
        self._two_rows()
        self.b.clearNameSelection()
        msg = self.b.writeNamesFromPopup("A One\nB Two", "甲\n乙")
        self.assertTrue(msg.startswith("已写入 2"), msg)
        zm = self.b._nameModel.zhMap()
        self.assertEqual(zm["A One"], "甲")
        self.assertEqual(zm["B Two"], "乙")
        self.b.clearNameSelection()

    def test_write_popup_tab_pairs_in_right_column(self):
        self._two_rows()
        msg = self.b.writeNamesFromPopup("A One\nB Two", "B Two\t新二")
        self.assertTrue(msg.startswith("已写入 1"), msg)
        self.assertEqual(self.b._nameModel.zhMap()["B Two"], "新二")

    def test_write_popup_space_separated_pair(self):
        """Tab 经 QQ 中转丢成空格：「英文名 中文」连一行也要能拆开写。"""
        self._two_rows()
        msg = self.b.writeNamesFromPopup("A One\nB Two", "A One 新一")
        self.assertTrue(msg.startswith("已写入 1"), msg)
        self.assertEqual(self.b._nameModel.zhMap()["A One"], "新一")

    def test_write_popup_equals_separator(self):
        self._two_rows()
        msg = self.b.writeNamesFromPopup("A One", "A One = 新一")
        self.assertTrue(msg.startswith("已写入 1"), msg)
        self.assertEqual(self.b._nameModel.zhMap()["A One"], "新一")

    def test_write_popup_more_zh_than_left(self):
        self._two_rows()
        msg = self.b.writeNamesFromPopup("A One", "甲\n乙")
        self.assertTrue(msg.startswith("已写入 1"), msg)
        self.assertIn("多 1 行没对应上左栏", msg)

    def test_write_popup_empty_right(self):
        self._two_rows()
        msg = self.b.writeNamesFromPopup("A One\nB Two", "  \n ")
        self.assertIn("右栏没有可写入的内容", msg)

    # ---------------- 列表拖选 + 显示同步修复 ----------------

    def test_sweep_range_selects_and_deselects(self):
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh=""),
            NameRow(en="B Two", zh=""),
            NameRow(en="C Three", zh=""),
        ])
        self.b.clearNameSelection()
        self.b.sweepNameRange(0, 2, True)
        self.assertEqual(self.b._nameModel.selectedEnOrdered(),
                         ["A One", "B Two", "C Three"])
        self.b.sweepNameRange(2, 2, False)
        self.assertEqual(self.b._nameModel.selectedEnOrdered(),
                         ["A One", "B Two"])
        # 区间越界要安全
        self.b.sweepNameRange(-5, 99, True)
        self.assertEqual(self.b._nameModel.selectedCount, 3)
        self.b.clearNameSelection()

    def test_sweep_replace_drag_semantics(self):
        """★ 拖选是替换式：拖过头再拖回来，多框的行要跟着退掉。

        （2026-10-27 修"往下拖卡住"时一并修的逻辑问题：
          原来是纯加选，选中的和屏幕上框到的对不上。）
        """
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh=""),
            NameRow(en="B Two", zh=""),
            NameRow(en="C Three", zh=""),
            NameRow(en="D Four", zh=""),
        ])
        self.b.clearNameSelection()
        self.b.sweepNameReplace(0, 2)
        self.assertEqual(self.b._nameModel.selectedEnOrdered(),
                         ["A One", "B Two", "C Three"])
        # 拖回到第 0 行：B、C 必须退掉
        self.b.sweepNameReplace(0, 0)
        self.assertEqual(self.b._nameModel.selectedEnOrdered(), ["A One"])
        # 越界安全（QML 里锚点不会是负数，这里用大数验证收拢）
        self.b.sweepNameReplace(0, 99)
        self.assertEqual(self.b._nameModel.selectedCount, 4)
        self.b.clearNameSelection()

    def test_cursor_and_button_slots(self):
        """拖选自动滚动的新兜底槽：全局光标 + 左键状态。"""
        p = self.b.cursorPos()
        self.assertEqual(len(p), 2)
        self.assertIsInstance(p[0], (int, float))
        self.assertIsInstance(self.b.leftButtonDown(), bool)

    def test_selected_name_en_list_for_popup(self):
        self._two_rows()
        self.b.clearNameSelection()
        self.b.toggleNameSelected("B Two", False)
        self.b.toggleNameSelected("A One", False)
        try:
            self.assertEqual(self.b.selectedNameEnList(), ["A One", "B Two"])
        finally:
            self.b.clearNameSelection()

    def test_update_by_en_refreshes_filtered_out_rows(self):
        """★ 2026-10-26 修「写入成功但显示没变化」：被筛选藏起来的行也要更新。"""
        from app.namemodel import ROLES
        from core.nameeditor import NameRow

        self.b._nameModel.setRows([
            NameRow(en="A One", zh="旧"),
            NameRow(en="B Two", zh="旧"),
        ])
        self.b._nameModel.setFilter("B")     # A One 被藏起来
        try:
            self.assertTrue(self.b._nameModel.updateByEn("A One", "新"))
            i = self.b._nameModel.indexOfEn("B Two")
            # 可见行照常能读
            self.assertEqual(self.b._nameModel.data(
                self.b._nameModel.index(i, 0), ROLES["zh"]), "旧")
        finally:
            self.b._nameModel.setFilter("")
        # 取消筛选后 A One 必须是新值（以前这里还是旧值 -> 用户看到"没变化"）
        zm = self.b._nameModel.zhMap()
        self.assertEqual(zm["A One"], "新")
        self.assertEqual(zm["B Two"], "旧")

    def test_batch_write_more_zh_than_selected(self):
        self._two_rows()
        self.b.toggleNameSelected("A One", False)   # 只勾 1 行
        try:
            msg = self.b.batchWriteNames("甲\n乙")
            self.assertTrue(msg.startswith("已写入 1"), msg)
            self.assertIn("还有 1 行没写", msg)
        finally:
            self.b.clearNameSelection()

    def test_batch_write_tab_pairs_update_dict_model(self):
        """词典模型里已有的词条，批量写入后行内同步（不用整表刷新）。"""
        from app.dictmodel import ROLES

        self.b._store.set_builtin({"Blur Radius": "模糊半径"})
        self.b.refreshDict()
        msg = self.b.batchWriteNames("Blur Radius\t模糊程半径")
        self.assertTrue(msg.startswith("已写入 1"), msg)
        e = self._row("Blur Radius")
        self.assertEqual(e.zh, "模糊程半径")
        self.assertEqual(e.layer, "user_locked")


if __name__ == "__main__":
    unittest.main()
