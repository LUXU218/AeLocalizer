"""插件组列表模型（QAbstractListModel）。

★ 为什么需要它（2026-10-21 实测）：
  QML 里原来写 model: backend.groups，而 groups 是 QVariantList（值类型）。
  每次勾选插件或切换「效果名/参数名/下拉值」都要 emit inventoryChanged，
  groups 重新算出一个全新数组 -> ListView 认为整个列表换掉了 ->
  滚动条弹回顶部。用户点第 30 个插件就被弹回顶上，没法连续操作。

  QAbstractListModel 是增量更新模型：
    勾选只发 dataChanged(那一行) -> ListView 保持滚动位置和 delegate。

  搜索过滤也在这里做（filter），避免 QML 侧重建数组。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Qt,
    Signal,
)

#: 角色名 -> 角色号
ROLES = {
    "id": Qt.UserRole + 1,
    "label": Qt.UserRole + 2,
    "vendor": Qt.UserRole + 3,
    "files": Qt.UserRole + 4,
    "effects": Qt.UserRole + 5,
    "selected": Qt.UserRole + 6,
    "builtin": Qt.UserRole + 7,
    "chEffect": Qt.UserRole + 8,
    "chParam": Qt.UserRole + 9,
    "chEnum": Qt.UserRole + 10,
}

#: 角色号 -> 角色名
_ROLE_NAMES = {v: k for k, v in ROLES.items()}


class GroupListModel(QAbstractListModel):
    """插件组列表（支持搜索过滤 + 增量更新）。"""

    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        #: 全量数据（dict 列表）
        self._all: List[dict] = []
        #: 过滤后的下标（存 _all 的位置）
        self._view: List[int] = []
        #: 过滤词（小写）
        self._filter: str = ""

    # ---------------------------------------------------------- 基础

    def roleNames(self):
        return {v: k.encode("utf-8") for k, v in ROLES.items()}

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._view)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        if r < 0 or r >= len(self._view):
            return None
        item = self._all[self._view[r]]
        name = _ROLE_NAMES.get(role)
        if name is None:
            return None
        return item.get(name)

    def roleAt(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._view):
            return self._all[self._view[row]].get("id")
        return None

    # ---------------------------------------------------------- 数据装载

    def setGroups(self, groups: List[dict]) -> None:
        """整体替换（扫描后调用）。"""
        self.beginResetModel()
        self._all = list(groups)
        self._rebuild()
        self.endResetModel()
        self.countChanged.emit()

    def _rebuild(self) -> None:
        """按过滤词重建 _view。"""
        q = self._filter
        if not q:
            self._view = list(range(len(self._all)))
            return
        out = []
        for i, g in enumerate(self._all):
            label = str(g.get("label", "")).lower()
            vendor = str(g.get("vendor", "")).lower()
            if q in label or q in vendor:
                out.append(i)
        self._view = out

    # ---------------------------------------------------------- 过滤

    def setFilter(self, text: str) -> None:
        """设置搜索词（会重建行，但只在这一刻）。"""
        q = (text or "").strip().lower()
        if q == self._filter:
            return
        self.beginResetModel()
        self._filter = q
        self._rebuild()
        self.endResetModel()
        self.countChanged.emit()

    # ---------------------------------------------------------- 增量更新

    def updateById(self, group_id: str) -> bool:
        """某个组的字段变了 -> 只通知那一行（不重置滚动）。"""
        for row, src in enumerate(self._view):
            item = self._all[src]
            if item.get("id") != group_id:
                continue
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, list(ROLES.values()))
            return True
        return False

    def updateAll(self) -> None:
        """所有行都可能变了（如全选/清空/套用模式）。"""
        if not self._view:
            return
        a = self.index(0, 0)
        b = self.index(len(self._view) - 1, 0)
        self.dataChanged.emit(a, b, list(ROLES.values()))

    def refreshFrom(self, groups: List[dict]) -> None:
        """数据源变了但行结构没变 -> 原地更新，保持滚动。"""
        if len(groups) == len(self._all):
            self._all = list(groups)
            self.updateAll()
            return
        self.setGroups(groups)
