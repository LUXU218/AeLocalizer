"""插件清单：扫描 + 分组 + 勾选状态持久化（"用户自选插件"的数据层）。

一个"插件"在界面上是一个可勾选的单元，但它可能对应：
  · 单个 .aex（多数第三方）
  · 一组 .aex（Sapphire 有几百个）
  · 一个 DLL 家族（BCC Continuum 的 4 个 DLL）

所以引入「插件组」概念：按厂商/目录/名字前缀归并，但**默认一文件一组**，
只在能明确判定同族时才合并（同目录 + 同前缀，且都带 PiPL）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .pipl import PluginInfo, find_ae_installs, scan_ae, scan_directory, COMMON_PLUGIN_DIRS

#: 三条汉化通道（用户可分别开关）
CH_EFFECT = "effect"    # 效果名（改 .aex PiPL）
CH_PARAM = "param"      # 参数名（运行时 hook）
CH_ENUM = "enum"        # 下拉值（运行时 hook）

ALL_CHANNELS = (CH_EFFECT, CH_PARAM, CH_ENUM)

#: 概览页的三个预设
MODE_EFFECT_ONLY = "effect_only"
MODE_PARAM_ONLY = "param_only"
MODE_ALL = "all"
MODE_CUSTOM = "custom"

MODE_CHANNELS = {
    MODE_EFFECT_ONLY: (CH_EFFECT,),
    MODE_PARAM_ONLY: (CH_PARAM, CH_ENUM),
    MODE_ALL: ALL_CHANNELS,
}


@dataclass
class PluginGroup:
    """界面上一个可勾选的插件单元。"""

    group_id: str
    label: str
    vendor: str = ""
    files: List[PluginInfo] = field(default_factory=list)
    selected: bool = False
    # ★ 2026-11-02:效果名通道默认【关】(参数+下拉默认开)。用户实测:
    #   「我都没有开效果名称汉化但是却汉化了」—— 分组通道默认 ALL 让
    #   效果名跟着参数一起默认生效,用户没主动开过也从不曾关过,违背直觉。
    #   效果名改 .aex PiPL 属于深度改写,应当显式选择;要开就用引擎卡
    #   效果名开关(setAllChannels 会批量写回选区并持久化)。
    channels: Tuple[str, ...] = (CH_PARAM, CH_ENUM)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def channel_effect(self) -> bool:
        return CH_EFFECT in self.channels

    @property
    def channel_param(self) -> bool:
        return CH_PARAM in self.channels

    @property
    def channel_enum(self) -> bool:
        return CH_ENUM in self.channels

    @property
    def effects(self) -> int:
        """可汉化的效果名数量（有真 PiPL name 的文件数）。"""
        return sum(1 for f in self.files if f.name_editable)

    @property
    def builtin_adobe(self) -> bool:
        """整组都是 Adobe 内置效果 -> 界面灰显（官方已汉化，碰了会冲突）。"""
        return bool(self.files) and all(f.is_builtin_adobe for f in self.files)

    @property
    def has_error(self) -> bool:
        return any(f.error for f in self.files)

    def toggle_channel(self, channel: str) -> None:
        if channel not in ALL_CHANNELS:
            return
        ch = set(self.channels)
        if channel == CH_EFFECT:
            if CH_EFFECT in ch:
                ch.discard(CH_EFFECT)
            else:
                ch.add(CH_EFFECT)
        else:
            # 参数名与下拉值是同一个“语言”通道：任一项 ON/OFF 一起切换。
            if CH_PARAM in ch or CH_ENUM in ch:
                ch.discard(CH_PARAM)
                ch.discard(CH_ENUM)
            else:
                ch.add(CH_PARAM)
                ch.add(CH_ENUM)
        self.channels = tuple(c for c in ALL_CHANNELS if c in ch)

    def to_dict(self) -> dict:
        return {
            "id": self.group_id,
            "label": self.label,
            "vendor": self.vendor,
            "selected": self.selected,
            "channels": list(self.channels),
            "files": [f.path for f in self.files],
        }


def _guess_vendor(info: PluginInfo) -> str:
    """从路径猜厂商（只做粗分类，界面可再改）。"""
    p = info.path.lower()
    table = [
        ("boris", "Boris FX"),
        ("continuum", "Boris FX"),
        ("sapphire", "Sapphire"),
        ("trapcode", "Maxon / Red Giant"),
        ("red giant", "Maxon / Red Giant"),
        ("redgiant", "Maxon / Red Giant"),
        ("universe", "Maxon / Red Giant"),
        ("magic bullet", "Maxon / Red Giant"),
        ("maxon", "Maxon / Red Giant"),
        ("videocopilot", "VideoCopilot"),
        ("video copilot", "VideoCopilot"),
        ("mocha", "Boris FX"),
        ("foundry", "Foundry"),
        ("cineware", "Maxon"),
        ("deep glow", "其它第三方"),
        ("glitchify", "其它第三方"),
    ]
    for needle, vendor in table:
        if needle in p:
            return vendor
    return "其它第三方"


def _group_id(prefix: str, files: List[PluginInfo]) -> str:
    base = prefix or (files[0].file_name if files else "unknown")
    return base.lower().replace(" ", "-")


def group_plugins(infos: Iterable[PluginInfo]) -> List[PluginGroup]:
    """把文件列表归并成插件组。

    规则（保守，宁可多分组也不误合并）：
      · 同一目录 + 相同「厂商前缀」+ 都有 PiPL -> 合成一组
      · 否则一文件一组
    """
    by_dir: Dict[str, List[PluginInfo]] = {}
    for info in infos:
        by_dir.setdefault(os.path.dirname(info.path).lower(), []).append(info)

    groups: List[PluginGroup] = []
    for _, files in by_dir.items():
        files.sort(key=lambda x: x.file_name.lower())
        # 单文件目录：直接一组
        if len(files) == 1:
            f = files[0]
            groups.append(
                PluginGroup(
                    group_id=_group_id(os.path.splitext(f.file_name)[0], files),
                    label=os.path.splitext(f.file_name)[0],
                    vendor=_guess_vendor(f),
                    files=[f],
                )
            )
            continue

        # 大目录（>8 个文件）整体作为一组，用目录名做标签 ——
        # 否则 Sapphire 287 个文件会被拆成十几个 "S (N 个效果)" 的无意义分组。
        if len(files) > 8:
            dname = os.path.basename(os.path.dirname(files[0].path)) or "插件"
            groups.append(
                PluginGroup(
                    group_id=_group_id(dname, files),
                    label="%s (%d 个效果)" % (dname, len(files)),
                    vendor=_guess_vendor(files[0]),
                    files=list(files),
                )
            )
            continue

        # 小目录：按「文件名首段」聚合
        buckets: Dict[str, List[PluginInfo]] = {}
        for f in files:
            stem = os.path.splitext(f.file_name)[0]
            head = stem.split("_")[0].split(" ")[0]
            buckets.setdefault(head.lower(), []).append(f)

        for head, fs in buckets.items():
            if len(fs) == 1:
                f = fs[0]
                groups.append(
                    PluginGroup(
                        group_id=_group_id(os.path.splitext(f.file_name)[0], fs),
                        label=os.path.splitext(f.file_name)[0],
                        vendor=_guess_vendor(f),
                        files=fs,
                    )
                )
            else:
                groups.append(
                    PluginGroup(
                        group_id=_group_id(head, fs),
                        label="%s (%d 个效果)" % (fs[0].file_name.split("_")[0], len(fs)),
                        vendor=_guess_vendor(fs[0]),
                        files=fs,
                    )
                )

    groups.sort(key=lambda g: (g.vendor, g.label.lower()))

    # ★★ group_id 去重（2026-10-21 实测 bug）
    #   同一目录下可能出现两个同名前缀的组，例如 Sapphire 目录里
    #   既有 "S"（2 个效果）又有 "S"（6 个效果）——
    #   _group_id 都返回 "s"，于是：
    #     · 勾选时 toggleGroup("s") 在第一个匹配处就 break，第二个永远选不了
    #     · 保存/加载选择状态时两组互相覆盖
    #   这里给重复的 id 加 -2/-3 后缀，保证全局唯一。
    seen = {}
    for g in groups:
        base = g.group_id
        n = seen.get(base, 0) + 1
        seen[base] = n
        if n > 1:
            g.group_id = "%s-%d" % (base, n)
    return groups


@dataclass
class Inventory:
    """一次扫描的完整结果。"""

    ae_version: str = ""
    ae_root: str = ""
    groups: List[PluginGroup] = field(default_factory=list)
    scanned_files: int = 0
    errors: int = 0

    @property
    def vendors(self) -> List[str]:
        return sorted({g.vendor for g in self.groups})

    def by_vendor(self) -> Dict[str, List[PluginGroup]]:
        out: Dict[str, List[PluginGroup]] = {}
        for g in self.groups:
            out.setdefault(g.vendor, []).append(g)
        return out

    def selected(self) -> List[PluginGroup]:
        return [g for g in self.groups if g.selected]

    def stats(self) -> Dict[str, int]:
        sel = self.selected()
        return {
            "groups": len(self.groups),
            "files": self.scanned_files,
            "errors": self.errors,
            "selected_groups": len(sel),
            "selected_files": sum(g.file_count for g in sel),
            "selected_effects": sum(g.effects for g in sel),
        }


def build_inventory(ae_root: str, ae_version: str = "") -> Inventory:
    infos = scan_ae(ae_root)
    inv = Inventory(
        ae_version=ae_version,
        ae_root=ae_root,
        groups=group_plugins(infos),
        scanned_files=len(infos),
        errors=sum(1 for i in infos if i.error),
    )
    return inv


def build_inventory_from_dirs(dirs: Iterable[str]) -> Inventory:
    infos: List[PluginInfo] = []
    for d in dirs:
        if os.path.isdir(d):
            infos.extend(scan_directory(d))
    return Inventory(
        groups=group_plugins(infos),
        scanned_files=len(infos),
        errors=sum(1 for i in infos if i.error),
    )


# ---------------------------------------------------------------- 持久化


def save_selection(path: str, inv: Inventory) -> None:
    # ★ 2026-10-22：扫描还没跑完就切版本/应用时 inv 可能是 None，
    #   原来直接 inv.ae_version 会抛 AttributeError（实测崩过一次）。
    if inv is None:
        return
    obj = {
        "schema": "aeloc-inventory/1",
        "ae_version": inv.ae_version,
        "ae_root": inv.ae_root,
        "groups": [
            {
                "id": g.group_id,
                "label": g.label,
                "vendor": g.vendor,
                "selected": g.selected,
                "channels": list(g.channels),
            }
            for g in inv.groups
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_selection(path: str, inv: Inventory) -> int:
    """把已保存的勾选状态套回 inventory；返回匹配到的组数。"""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return 0
    saved = {g["id"]: g for g in obj.get("groups") or []}
    hit = 0
    for g in inv.groups:
        s = saved.get(g.group_id)
        if not s:
            continue
        g.selected = bool(s.get("selected", False))
        # 空 channels 是用户明确“全关”，不能 or 回退成默认。
        raw = s.get("channels")
        if raw is None:
            ch = (CH_PARAM, CH_ENUM)
        else:
            ch = tuple(c for c in raw if c in ALL_CHANNELS)
        # 历史 param/enum 分裂：以 param 为主配对；全关保持全关。
        want_lang = CH_PARAM in ch
        ch = tuple(c for c in ch if c not in (CH_PARAM, CH_ENUM))
        if want_lang:
            ch = tuple(c for c in ALL_CHANNELS if c in set(ch) | {CH_PARAM, CH_ENUM})
        g.channels = ch
        hit += 1
    return hit


def apply_mode(inv: Inventory, mode: str) -> None:
    """按概览页的预设，批量设置所有组的通道开关。"""
    ch = MODE_CHANNELS.get(mode)
    if ch is None:
        return
    for g in inv.groups:
        g.channels = ch


def set_all_channels(inv: Inventory, effect: bool, param: bool, enum: bool) -> int:
    """效果名独立；param/enum 作为合并语言通道一起套用到所有组。

    ★ 2026-10-23 用户要求把插件选择并进翻译页，左栏空间只够放
      「效果名 / 参数名 / 下拉值」三个小开关，不能再像原来那样每组
      各有一组开关（三通道 × 66 组 = 198 个控件的视觉噪音）。
      改成全局一次设定，需要单独微调的组再去原「插件」Tab 里点。
    """
    ch = []
    if effect:
        ch.append(CH_EFFECT)
    # 合并后参数名/下拉值只保留一个语言 intent：任一 ON 即两者 ON；
    # 两者都 False 必须保持真正全关（旧代码会回退 ALL_CHANNELS）。
    if param or enum:
        ch.extend((CH_PARAM, CH_ENUM))
    tup = tuple(ch)
    n = 0
    for g in inv.groups:
        if g.channels != tup:
            g.channels = tup
            n += 1
    return n