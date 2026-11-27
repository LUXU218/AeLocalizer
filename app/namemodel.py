"""效果名编辑器列表模型（QAbstractListModel）。

★ 为什么用 Model 而不是 QVariantList：
  同 GroupListModel —— 1265 行如果每次编辑都重建数组，
  ListView 会整表重置、滚动条弹回顶部、输入框失焦。
  用 dataChanged(单行) 才能边打字边保持位置。

按厂商筛选和批量勾选：
  · 勾选状态放在模型里（按英文名记，切筛选/切插件不丢）
  · 支持按厂商（vendor）筛选：只看 BCC / Sapphire / …
  · 支持"全选当前筛选结果"，再配合批量操作（清空 / AI 重译）
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from core.nameeditor import NameRow

ROLES = {
    "en": Qt.UserRole + 1,
    "zh": Qt.UserRole + 2,
    "slot": Qt.UserRole + 3,
    "file": Qt.UserRole + 4,
    "vendor": Qt.UserRole + 5,
    "status": Qt.UserRole + 6,
    "statusText": Qt.UserRole + 7,
    "gbkLen": Qt.UserRole + 8,
    "extraFiles": Qt.UserRole + 9,
    #: 是否被勾选（批量操作用）
    "selected": Qt.UserRole + 10,
    #: 插件套装名（列表按它做分区表头 + 分区全选）
    "suite": Qt.UserRole + 11,
}
_ROLE_NAMES = {v: k for k, v in ROLES.items()}


class NameListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: List[NameRow] = []
        self._rows: List[NameRow] = []
        self._kw = ""
        self._onlyZh = False
        self._vendor = ""
        #: 勾选的英文名集合（用 en 做主键：词典就是按 en 存的）
        self._selected: set = set()
        #: Shift 连选的锚点（上一次普通点击的那一行）
        self._anchor: str = ""

    # ---------- Qt 接口 ----------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return {v: k.encode() for k, v in ROLES.items()}

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        r = self._rows[index.row()]
        name = _ROLE_NAMES.get(role)
        if name == "en":
            return r.en
        if name == "zh":
            return r.zh
        if name == "slot":
            return r.slot
        if name == "file":
            return r.file
        if name == "vendor":
            return r.vendor
        if name == "status":
            return r.status
        if name == "statusText":
            return r.status_text
        if name == "gbkLen":
            return r.gbk_len
        if name == "extraFiles":
            # 同名但来自其他 aex 文件的数量（BCC 的普通版 + PrTr 转场版）
            return r.extra_files
        if name == "selected":
            return r.en in self._selected
        if name == "suite":
            return r.suite
        return None

    def setData(self, index, value, role=Qt.EditRole):
        """QML 里直接写表单项时走这里（本程序主要用 backend 槽，留作兜底）。"""
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return False
        if _ROLE_NAMES.get(role) != "zh":
            return False
        self._rows[index.row()].zh = str(value or "")
        self.dataChanged.emit(index, index)
        return True

    # ---------- 数据 ----------

    def setRows(self, rows: List[NameRow]):
        # 切页时若数据和筛选结果均未变化，直接保留列表，避免重建所有行和分组标题。
        if self._all == list(rows) and self._rows == self._filtered():
            return
        self.beginResetModel()
        self._all = list(rows)
        # 只保留还存在的勾选（刷新后不丢用户的选择）
        alive = {r.en for r in self._all}
        self._selected &= alive
        if self._anchor and self._anchor not in alive:
            self._anchor = ""
        self._rows = self._filtered()
        self.endResetModel()

    def _filtered(self) -> List[NameRow]:
        kw = self._kw.strip().lower()
        out = []
        for r in self._all:
            if self._onlyZh and r.status == "empty":
                continue
            if self._vendor and r.vendor != self._vendor:
                continue
            if kw and kw not in r.en.lower() and kw not in (r.zh or "").lower():
                continue
            out.append(r)
        return out

    def setFilter(self, keyword: str = "", only_zh: bool = False,
                  vendor: str = ""):
        kw = keyword or ""
        oz = bool(only_zh)
        vd = (vendor or "").strip()
        if kw == self._kw and oz == self._onlyZh and vd == self._vendor:
            return                      # 过滤条件没变，不整表 reset
        self.beginResetModel()
        self._kw = kw
        self._onlyZh = oz
        self._vendor = vd
        self._rows = self._filtered()
        self.endResetModel()

    def rowAt(self, i: int) -> Optional[NameRow]:
        if 0 <= i < len(self._rows):
            return self._rows[i]
        return None

    def updateByEn(self, en: str, zh: str):
        """改名后刷新所有同 en 的行（同一效果可能在多个 aex 里）。

        ★ 2026-10-26 修「写入成功但显示没变化」：
          原来只遍历 _rows（当前筛选后的可见行）。被搜索框/插件筛选
          藏起来的行不在 _rows 里，批量写入后就留着旧译文，
          取消筛选一看 —— 词典里明明写进去了，界面上却是旧值。
          现在 _all（全量）和 _rows（可见）都更新，可见行发 dataChanged。
        """
        hit = False
        for r in self._all:
            if r.en == en and r.zh != zh:
                r.zh = zh
                hit = True
        for i, r in enumerate(self._rows):
            if r.en == en:
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, list(ROLES.values()))
        return hit

    def indexOfEn(self, en: str) -> int:
        for i, r in enumerate(self._rows):
            if r.en == en:
                return i
        return -1

    # ---------- 勾选（2026-10-23 批量操作） ----------

    @property
    def selectedCount(self) -> int:
        return len(self._selected)

    def selectedEn(self) -> List[str]:
        return sorted(self._selected)

    def selectedEnOrdered(self) -> List[str]:
        """按当前列表显示顺序取勾选行。

        ★ 批量复制/批量写入必须用它：用户从上往下勾的行、粘回来的纯中文
          也要按同样的顺序写。被筛选藏起来的勾选行排在最后（字母序），不丢。
        """
        out = [r.en for r in self._rows if r.en in self._selected]
        hidden = sorted(self._selected - {r.en for r in self._rows})
        return out + hidden

    def zhMap(self) -> Dict[str, str]:
        """全部行的 en -> zh 映射（批量复制「中英」时取译文用）。"""
        return {r.en: r.zh for r in self._all}

    def isSelected(self, en: str) -> bool:
        return en in self._selected

    def setSelectedByEn(self, en: str, on: bool) -> bool:
        """切换某一行勾选；只给可见的那一行发 dataChanged。"""
        changed = False
        if on:
            if en not in self._selected:
                self._selected.add(en)
                changed = True
        else:
            if en in self._selected:
                self._selected.discard(en)
                changed = True
        if not changed:
            return False
        row = self.indexOfEn(en)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, list(ROLES.values()))
        return True

    def toggleSelectedByEn(self, en: str, shift: bool = False) -> bool:
        """点击勾选：普通点击 = 切换 + 记锚点；Shift 点击 = 从锚点连选到这一行。

        区间选择沿用资源管理器的操作方式：
            · 普通点击：切换这一行，并把这一行记成锚点
            · Shift+点击：把锚点到这一行之间的所有可见行，统一设成
              「这一行点击后的状态」（原本没选就都选上，原本已选就都取消）
        """
        on = en not in self._selected
        row = self.indexOfEn(en)

        if shift and self._anchor:
            anchor_row = self.indexOfEn(self._anchor)
            if anchor_row >= 0 and row >= 0:
                lo, hi = min(anchor_row, row), max(anchor_row, row)
                for r in self._rows[lo:hi + 1]:
                    if on:
                        self._selected.add(r.en)
                    else:
                        self._selected.discard(r.en)
                self._emit_all()
                return True

        # 普通点击（或锚点已经被筛掉）：只切这一行
        if on:
            self._selected.add(en)
        else:
            self._selected.discard(en)
        self._anchor = en
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, list(ROLES.values()))
        return True

    def setRangeByIndex(self, i0: int, i1: int, on: bool) -> int:
        """把 [min,max] 区间的可见行统一勾上/取消（按住左键拖选）。

        返回实际变化的行数。只动 _rows 里的可见行 —— 拖选本来就是在
        屏幕上划过去的行，被筛选藏起来的行不碰。

        ★ 2026-10-27 性能修复：原来每次都 _emit_all()（全模型 × 全部角色
        的 dataChanged），拖动时每跨一行就发一次，列表一卡一卡。
        现在只对 [lo,hi] 区间发、只带 selected 角色。
        """
        lo = max(0, min(int(i0), int(i1)))
        hi = min(len(self._rows) - 1, max(int(i0), int(i1)))
        if lo > hi:
            return 0
        n = 0
        for r in self._rows[lo:hi + 1]:
            if on:
                if r.en not in self._selected:
                    self._selected.add(r.en)
                    n += 1
            else:
                if r.en in self._selected:
                    self._selected.discard(r.en)
                    n += 1
        if n:
            self.dataChanged.emit(self.index(lo, 0), self.index(hi, 0),
                                  [ROLES["selected"]])
        return n

    def setSweepRange(self, anchor: int, cur: int) -> int:
        """★ 拖动框选专用：让勾选恰好等于 [anchor..cur]（可见行内），替换式。

        和 setRangeByIndex（纯加选）不同：往下拖过头再拖回来，多框的那段
        要跟着退掉，不然选中的和屏幕上框到的对不上。
        同样只对变化了的区间发 dataChanged（只带 selected 角色）。
        """
        n_rows = len(self._rows)
        if n_rows == 0 or int(anchor) < 0 or int(cur) < 0:
            return 0
        lo = max(0, min(int(anchor), int(cur)))
        hi = min(n_rows - 1, max(int(anchor), int(cur)))
        changed_lo = None
        changed_hi = None
        n = 0
        for i, r in enumerate(self._rows):
            want = lo <= i <= hi
            has = r.en in self._selected
            if has == want:
                continue
            if want:
                self._selected.add(r.en)
            else:
                self._selected.discard(r.en)
            n += 1
            if changed_lo is None:
                changed_lo = i
            changed_hi = i
        if n:
            self.dataChanged.emit(self.index(changed_lo, 0),
                                  self.index(changed_hi, 0),
                                  [ROLES["selected"]])
        return n

    def selectSuite(self, suite: str, on: bool = True) -> int:
        """把某个套装（当前可见的）整块选中/取消。返回变化的行数。"""
        suite = (suite or "").strip()
        if not suite:
            return 0
        changed = 0
        for r in self._rows:
            if r.suite != suite:
                continue
            if on and r.en not in self._selected:
                self._selected.add(r.en)
                changed += 1
            elif not on and r.en in self._selected:
                self._selected.discard(r.en)
                changed += 1
        if changed:
            self._emit_all()
        return changed

    def suiteCounts(self) -> Dict[str, int]:
        """当前筛选结果里每个套装的条数（给表头显示 "Continuum · 478 条"）。"""
        out: Dict[str, int] = {}
        for r in self._rows:
            out[r.suite] = out.get(r.suite, 0) + 1
        return out

    def _emit_all(self) -> None:
        if not self._rows:
            return
        a = self.index(0, 0)
        b = self.index(len(self._rows) - 1, 0)
        self.dataChanged.emit(a, b, list(ROLES.values()))

    def setAllVisible(self, on: bool) -> int:
        """全选 / 取消全选【当前筛选结果】。返回实际变化的行数。"""
        before = len(self._selected)
        if on:
            self._selected.update(r.en for r in self._rows)
            self._anchor = ""
        else:
            for r in self._rows:
                self._selected.discard(r.en)
        self._emit_all()
        return abs(len(self._selected) - before)

    def clearSelection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self._anchor = ""
        self._emit_all()

    def allEn(self) -> List[str]:
        """全量数据的英文名（批量写入「名单勾选」的匹配范围）。"""
        return [r.en for r in self._all]

    def selectByEnList(self, ens: List[str]):
        """按英文名单勾选（替换当前勾选）。返回 (匹配数, 未匹配名单)。

        只粘贴英文名单时先按名称勾选，便于批量操作或接着粘贴中文译名。
        """
        alive = {r.en for r in self._all}
        new_sel = {en for en in ens if en in alive}
        missing = [en for en in ens if en not in alive]
        changed = new_sel != self._selected
        self._selected = new_sel
        self._anchor = ""
        if changed:
            self._emit_all()
        return len(new_sel), missing

    def pruneSelection(self, keep: set) -> None:
        """批量删除/替换后把已经不存在的勾选清掉。"""
        self._selected &= set(keep)

    # ---------- 厂商列表（「按插件名称」下拉框用） ----------

    def vendors(self) -> List[Dict[str, object]]:
        """当前全量数据里出现过的厂商 + 条目数（按条目数降序）。"""
        cnt: Dict[str, int] = {}
        for r in self._all:
            v = (r.vendor or "").strip()
            if not v:
                continue
            cnt[v] = cnt.get(v, 0) + 1
        return [{"value": k, "count": v}
                for k, v in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]
