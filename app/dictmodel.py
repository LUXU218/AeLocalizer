"""词典浏览/编辑列表模型（QAbstractListModel）。

★ 2026-10-25 用户需求：
  「把词典这个页面改成用户能自己修改里面的参数汉化词典，并将这个页面
    合并到效果名中」—— 词典从"只读前 200 条预览"变成可搜索、可编辑的全量表。

★ 为什么用 QAbstractListModel 而不是 QVariantList：
  同 GroupListModel / NameListModel —— 2 万多条如果每次编辑都重建数组，
  ListView 会整表重置、滚动条弹回顶部、输入框失焦。
  改一条只发 dataChanged(单行)，滚动位置和输入焦点都能保持。

行数据来自 DictStore.resolve()（最终裁决结果），每行带：
  · en     英文原名（词典主键）
  · zh     当前生效译名
  · layer  来源层（builtin / pack / user / user_locked）
  · gauge  长度闸摘要（encoder.describe 的输出）
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

ROLES = {
    "en": Qt.UserRole + 1,
    "zh": Qt.UserRole + 2,
    "layer": Qt.UserRole + 3,
    #: 来源层的中文显示名（QML 直接用，不写业务逻辑）
    "layerLabel": Qt.UserRole + 4,
    "gauge": Qt.UserRole + 5,
}
_ROLE_NAMES = {v: k for k, v in ROLES.items()}

_LAYER_LABELS = {
    "builtin": "内置",
    "pack": "词典包",
    "user": "用户",
    "user_locked": "已锁定",
}


class _Entry:
    """一行词条。__slots__：2 万行 × 普通对象属性字典太占内存。"""

    __slots__ = ("en", "zh", "layer", "gauge")

    def __init__(self, en: str, zh: str, layer: str, gauge: str) -> None:
        self.en = en
        self.zh = zh
        self.layer = layer
        self.gauge = gauge


class DictListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: List[_Entry] = []
        self._rows: List[_Entry] = []
        self._kw = ""

    # ---------- Qt 接口 ----------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return {v: k.encode() for k, v in ROLES.items()}

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        e = self._rows[index.row()]
        name = _ROLE_NAMES.get(role)
        if name == "en":
            return e.en
        if name == "zh":
            return e.zh
        if name == "layer":
            return e.layer
        if name == "layerLabel":
            return _LAYER_LABELS.get(e.layer, e.layer)
        if name == "gauge":
            return e.gauge
        return None

    # ---------- 数据 ----------

    def setRows(self, rows: Iterable[dict]) -> int:
        """整体重建（扫描词典库后 / 载入词典包后调用）。

        rows: [{"en", "zh", "layer", "gauge"}, …]，保持传入顺序。
        """
        entries = [
            _Entry(str(d.get("en", "")), str(d.get("zh", "")),
                   str(d.get("layer", "")), str(d.get("gauge", "")))
            for d in rows
        ]
        self.beginResetModel()
        self._all = entries
        self._rows = self._filtered()
        self.endResetModel()
        return len(entries)

    def clear(self) -> None:
        if not self._all and not self._kw:
            return
        self.beginResetModel()
        self._all = []
        self._rows = []
        self._kw = ""
        self.endResetModel()

    def _filtered(self) -> List[_Entry]:
        kw = self._kw.strip().lower()
        if not kw:
            return list(self._all)
        return [
            e for e in self._all
            if kw in e.en.lower() or kw in e.zh.lower()
        ]

    def setFilter(self, keyword: str):
        kw = (keyword or "").strip()
        if kw == self._kw:
            return
        self.beginResetModel()
        self._kw = kw
        self._rows = self._filtered()
        self.endResetModel()

    # ---------- 单行更新（编辑词条后避免整表重建） ----------

    def _indexOfEnInAll(self, en: str) -> int:
        for i, e in enumerate(self._all):
            if e.en == en:
                return i
        return -1

    def updateEntry(self, en: str, zh: str, layer: str, gauge: str) -> bool:
        """更新一条词条的所有视图（包括被过滤藏起来的那份）。"""
        i = self._indexOfEnInAll(en)
        if i < 0:
            return False
        e = self._all[i]
        e.zh = zh
        e.layer = layer
        e.gauge = gauge
        # 可见列表里可能没有它（被过滤掉），也可能有多份？——en 是主键，只有一份
        for j, v in enumerate(self._rows):
            if v.en == en:
                self._rows[j] = e
                idx = self.index(j, 0)
                self.dataChanged.emit(idx, idx, list(ROLES.values()))
                break
        return True

    def removeEntry(self, en: str) -> bool:
        """词条被删掉（用户层删除后词典里不再有它）→ 从两个列表里移除。"""
        i = self._indexOfEnInAll(en)
        if i < 0:
            return False
        del self._all[i]
        for j, v in enumerate(self._rows):
            if v.en == en:
                self.beginRemoveRows(QModelIndex(), j, j)
                del self._rows[j]
                self.endRemoveRows()
                break
        return True

    def indexOfEn(self, en: str) -> int:
        for i, e in enumerate(self._rows):
            if e.en == en:
                return i
        return -1

    def entryAt(self, i: int) -> Optional[_Entry]:
        if 0 <= i < len(self._rows):
            return self._rows[i]
        return None
