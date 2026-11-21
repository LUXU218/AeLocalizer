"""效果名编辑器：列出所有插件的效果名 + 当前译文，供用户直接改。

★★ 2026-10-22 用户需求：
  「加一个能让用户实时修改所有插件名称的界面，可以直接在 AI 翻译之后
   也能进行修改，也可以用户自行修改」

  为什么必须要有：早期一批 AI 翻译质量很差，例如
      BCC Primatte Studio  ->  BCC 镜数幕
      BCC 2D Particles     ->  BCC 镜引粒子
      BCC Brightness-Contrast -> BCC 镜对
  机械修前缀救不了译文本身，只能让用户在这个表里直接改。

数据来源：
  · 插件清单  -> Inventory（当前 AE 版本扫描结果）
  · 当前译文  -> DictStore.resolve()
  · 槽位长度  -> PiPL name 字段（决定译文能不能放下）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class NameRow:
    """编辑器里的一行。"""

    en: str = ""            # 英文原名（PiPL name）
    zh: str = ""            # 当前译文（空 = 没译）
    slot: int = 0           # PiPL 槽位字节数（0 = 未知）
    file: str = ""          # 文件名
    path: str = ""          # 完整路径
    vendor: str = ""        # 厂商分组
    #: 插件套装名（2026-10-23 用户要求「随插件套装名分隔开」）。
    #:
    #: 取值来自 Inventory 的组 label（去掉 " (478 个效果)" 这种计数尾巴），
    #: 例如 Continuum / MochaPro / Red Giant Universe / Particular / Twirl。
    #: Sapphire 的 10 个组是【分类】不是套装，统一归成 "Sapphire"。
    suite: str = ""
    match_name: str = ""
    locked: bool = False     # 用户锁定过（不再被 AI 覆盖）
    #: 同名的其他文件数（0 = 只有这一个文件）。
    #:
    #: ★ 2026-10-23 用户问「为什么有两个完全一样的」：
    #:   BCC 有 51 个效果存在「普通版 + PrTr 转场预设版」两套 aex，
    #:   例如 BCCBlobsWipe.aex 和 BCCBlobsWipePrTr.aex，
    #:   两者的 PiPL display_name 完全相同。
    #:   原来去重键是 (en, path)，于是编辑器里出现两行一模一样。
    #:   现在按 en 去重（改一条 = 全部同名生效，因为词典就是按 en 存的），
    #:   多出来的文件用 file_count 标注，信息不丢。
    extra_files: int = 0

    @property
    def gbk_len(self) -> int:
        try:
            return len(self.zh.encode("gbk"))
        except UnicodeEncodeError:
            return 999999

    @property
    def status(self) -> str:
        """ok / empty / long / same"""
        if not self.zh.strip():
            return "empty"
        if self.zh.strip() == self.en.strip():
            return "same"
        if self.slot and self.gbk_len > self.slot:
            return "long"
        return "ok"

    @property
    def status_text(self) -> str:
        return {
            "ok": "正常",
            "empty": "未翻译",
            "long": "超长",
            "same": "同英文",
        }.get(self.status, "")


_COUNT_SUFFIX = re.compile(r"\s*\(\s*\d+\s*个效果\s*\)\s*$")


def suite_of(group) -> str:
    """从 Inventory 组里取「插件套装名」。

    ★ 2026-10-23 用户要求「按插件套装名分隔开」：
      · label 尾巴上的 " (478 个效果)" 是统计信息，去掉
      · Sapphire 的 10 个组是【分类】（Blur+Sharpen / Distort / Lighting…），
        不是不同套装 —— 统一归成 "Sapphire"，否则同一个套装会被拆成 10 块
      · Adobe 自带的效果在一个叫 "Effects" 的目录里，不是第三方套装，
        统一标成 "Adobe 内置"，免得 218 个自带效果混成一块看不懂的 "Effects"
      · 其它第三方每个 .aex 就是一个插件，用它的组名（≈文件名）刚好
    """
    vendor = (getattr(group, "vendor", "") or "").strip()
    label = _COUNT_SUFFIX.sub("", (getattr(group, "label", "") or "").strip())
    if getattr(group, "builtin_adobe", False):
        return "Adobe 内置"
    if vendor == "Sapphire":
        return "Sapphire"
    return label or vendor or "未分组"


def collect_rows(inv, store, *, only_zh: bool = False) -> List[NameRow]:
    """从插件清单 + 字典生成编辑器行。

    only_zh=True 时只返回"当前译文是中文"的行（用户想改的时候通常只看这些）。
    """
    try:
        mapping = {k: r.zh for k, r in store.resolve().items() if r.zh}
    except Exception:
        mapping = {}

    if inv is None:
        return []
    # ★ 按英文名分组（同一效果可能有多个 aex 文件）
    buckets: Dict[str, List] = {}
    for g in inv.groups:
        for f in g.files:
            en = (f.display_name or "").strip()
            if not en:
                continue
            buckets.setdefault(en, []).append((g, f))

    rows: List[NameRow] = []
    for en, items in buckets.items():
        g, f = items[0]
        # PiPL name 槽位长度：扫描时已随 PluginInfo.strings 解析进内存。
        # ★ 2026-11-01 性能：原来这里对每个唯一英文名重读整个 .aex 并全字节
        #   扫 PiPL（~1220 个文件，数百 MB IO），是每次切页卡顿的主因 ——
        #   库存对象里本来就有，直接读内存，零磁盘 IO。
        slot = 0
        for s in (f.strings or ()):
            if s.key == "name":
                slot = s.length
                break
        zh = mapping.get(en) or mapping.get(en.strip()) or ""
        if only_zh and not _CJK.search(zh or ""):
            continue
        rows.append(NameRow(
            en=en, zh=zh, slot=slot,
            file=f.file_name, path=f.path,
            vendor=g.vendor or g.label,
            suite=suite_of(g),
            match_name=f.match_name or "",
            extra_files=max(0, len(items) - 1),
        ))
    # 先按厂商，再按套装，套装内部按英文名 —— 这样列表里同一个套装是连续的一段，
    # ListView 的 section（按 suite 分组）才能正确显示成一块一块的。
    rows.sort(key=lambda r: (r.vendor.lower(), r.suite.lower(), r.en.lower()))
    return rows


def stats(rows: List[NameRow]) -> Dict[str, int]:
    out = {"total": len(rows), "ok": 0, "empty": 0, "long": 0, "same": 0}
    for r in rows:
        s = r.status
        out[s] = out.get(s, 0) + 1
    return out