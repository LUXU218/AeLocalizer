"""把旧版部署词典导出成可分发的 .aeloc 词典包。

做三件旧工具没做的事：
  1. **按引擎真实优先级合并**（LAYER_ORDER + setdefault 语义），结果与 AE 里实际生效的一致
  2. **质量分层**：identity（译名 == 原文）/ 无中文 / 空值 一律剔除，并出统计报告
  3. **长度闸预判**：按 GBK 与原英文 ASCII 长度比较，标出"写不进去"的条目

导出物：
  · <name>.aeloc      可直接分发（别人双击/拖入即可导入）
  · <name>.report.md  本次导出的统计与问题清单（给自己看，也可随包分发）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .dictpack import build_pack, write_pack
from .shrink import shortest_fitting
from .legacy.reader import (
    ENUM_LAYERS,
    EXTRA_LAYERS,
    LAYER_ORDER,
    LegacyDictError,
    load_legacy_layer,
)

CJK = re.compile(r"[\u4e00-\u9fff]")
_CJK_ONLY_NAME = re.compile(r"[\u4e00-\u9fff]")

#: 已知的错译黑名单（来自历史踩坑记录），导出时剔除
BAD_TRANSLATIONS: Dict[str, str] = {
    "Right": "正确",
    "Highlights": "重点说明",
    "Fog Color": "牙齿颜色",
    "Spacing": "空格",
}


@dataclass
class EntryStat:
    key: str
    zh: str
    layer: str
    reason: str = ""


@dataclass
class ExportReport:
    total_raw: int = 0
    kept: int = 0
    dropped_identity: List[EntryStat] = field(default_factory=list)
    dropped_no_cjk: List[EntryStat] = field(default_factory=list)
    dropped_empty: List[EntryStat] = field(default_factory=list)
    dropped_bad: List[EntryStat] = field(default_factory=list)
    dropped_cjk_key: List[EntryStat] = field(default_factory=list)
    dropped_blacklist: List[EntryStat] = field(default_factory=list)
    too_long: List[EntryStat] = field(default_factory=list)
    shortened: List[EntryStat] = field(default_factory=list)
    per_layer: Dict[str, int] = field(default_factory=dict)

    def to_markdown(self, pack_name: str, pack_version: str) -> str:
        lines = [
            f"# 导出报告 · {pack_name} v{pack_version}",
            "",
            f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
            "",
            "## 汇总",
            "",
            "| 项 | 数量 |",
            "|---|---|",
            f"| 原始条目 | {self.total_raw} |",
            f"| **保留** | **{self.kept}** |",
            f"| 剔除：译名==原文 | {len(self.dropped_identity)} |",
            f"| 剔除：不含中文 | {len(self.dropped_no_cjk)} |",
            f"| 剔除：空值 | {len(self.dropped_empty)} |",
            f"| 剔除：错译黑名单 | {len(self.dropped_blacklist)} |",
            f"| 剔除：键含中文 | {len(self.dropped_cjk_key)} |",
            f"| 警告：GBK 超长但可缩短 | {len(self.shortened)} |",
            f"| 警告：GBK 超长且无可用短译（会被跳过） | {len(self.too_long)} |",
            "",
            "## 各层贡献",
            "",
            "| 词典层 | 条目 |",
            "|---|---|",
        ]
        for k, v in self.per_layer.items():
            lines.append(f"| {k} | {v} |")

        def section(title: str, items: List[EntryStat], limit: int = 40) -> None:
            if not items:
                return
            lines.extend(["", f"## {title}", "", "| 英文 | 译名 | 来源层 |", "|---|---|---|"])
            for it in items[:limit]:
                lines.append(f"| {it.key} | {it.zh} | {it.layer} |")
            if len(items) > limit:
                lines.append(f"| … | 共 {len(items)} 条 | |")

        section("GBK 超长且无可用短译（会被引擎跳过）", self.too_long)
        section("GBK 超长但可自动缩短", self.shortened)
        section("错译黑名单命中", self.dropped_blacklist)
        section("译名与原文相同", self.dropped_identity)
        return "\n".join(lines) + "\n"


def _gbk_len(s: str) -> Optional[int]:
    try:
        return len(s.encode("gbk"))
    except Exception:
        return None


def _ascii_len(s: str) -> int:
    return len(s.encode("utf-8", "replace"))


def collect_layers(dict_dir: str) -> Tuple[Dict[str, Dict[str, str]], Dict[str, int]]:
    """按引擎优先级合并各层，返回 ({英文: {zh, layer}}, 每层条数)。"""
    merged: Dict[str, Dict[str, str]] = {}
    counts: Dict[str, int] = {}
    for layer in LAYER_ORDER:
        data = load_legacy_layer(dict_dir, layer)
        counts[layer] = len(data)
        for k, v in data.items():
            if k.startswith("_"):
                continue
            merged.setdefault(k, {"zh": v, "layer": layer})
    for layer in ENUM_LAYERS:
        data = load_legacy_layer(dict_dir, layer)
        counts[layer] = len(data)
        for k, v in data.items():
            if k.startswith("_"):
                continue
            merged.setdefault(k, {"zh": v, "layer": layer})
    for layer in EXTRA_LAYERS:
        data = load_legacy_layer(dict_dir, layer)
        counts[layer] = len(data)
    return merged, counts


def export_pack(
    dict_dir: str,
    out_path: str,
    *,
    pack_id: str = "com.aelocalizer.legacy-sapphire",
    name: str = "Sapphire 全量汉化词典",
    version: str = "1.0.0",
    author: str = "",
    license_text: str = "",
    license_id: str = "",
    keep_identity: bool = False,
    include_enum: bool = True,
    report_path: Optional[str] = None,
) -> ExportReport:
    """执行导出，返回报告。"""
    merged, counts = collect_layers(dict_dir)
    report = ExportReport(total_raw=len(merged), per_layer=dict(counts))

    entries: Dict[str, Dict[str, str]] = {}
    for key, info in merged.items():
        zh, layer = info["zh"], info["layer"]
        if not key or not zh:
            report.dropped_empty.append(EntryStat(key, zh, layer))
            continue
        if _CJK_ONLY_NAME.search(key):
            report.dropped_cjk_key.append(EntryStat(key, zh, layer))
            continue
        if key == zh and not keep_identity:
            report.dropped_identity.append(EntryStat(key, zh, layer))
            continue
        if not CJK.search(zh):
            report.dropped_no_cjk.append(EntryStat(key, zh, layer))
            continue
        bad = BAD_TRANSLATIONS.get(key)
        if bad and zh == bad:
            report.dropped_blacklist.append(EntryStat(key, zh, layer, "命中错译黑名单"))
            continue
        if not include_enum and layer in ENUM_LAYERS:
            continue

        avail = _ascii_len(key)
        gl = _gbk_len(zh)
        if gl is None:
            report.dropped_no_cjk.append(EntryStat(key, zh, layer, "含 GBK 无法表示的字符"))
            continue
        if gl > avail:
            fit = shortest_fitting(zh, avail)
            if fit is None:
                report.too_long.append(EntryStat(key, zh, layer, f"GBK {gl} > 可用 {avail}，无可用短译"))
            else:
                report.shortened.append(
                    EntryStat(key, zh, layer, f"GBK {gl} > {avail}，可缩短为「{fit}」")
                )

        entries[key] = {"zh": zh, "priority": 50, "note": f"来源:{layer}"}

    report.kept = len(entries)

    applies = {
        "aeVersions": ["2021", "2022", "2023", "2024", "2025", "2026"],
        "plugins": [],
        "encoding": "auto",
    }
    data = build_pack(
        pack_id=pack_id,
        name=name,
        version=version,
        author=author,
        entries=entries,
        license_text=license_text,
        applies_to=applies,
        license_id=license_id,
        description=f"由旧版部署词典导出的全量汉化词典，共 {len(entries):,} 条",
        created=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    write_pack(data, out_path)

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.to_markdown(name, version))
    return report