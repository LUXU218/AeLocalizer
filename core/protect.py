"""禁止改名的插件黑名单（实测会崩）。

★★ 2026-10-22 惨痛实测：
  Newton.aex 的 PiPL name 只要动一个字节，AE 启动就会在 6 秒时崩溃
  （exit code 1，无窗口、无错误日志）。

  试过的两种改法都会崩：
    "MB Newton" -> "牛顿动力"   （全中文）
    "MB Newton" -> "MB 牛顿"    （保留 MB 前缀）
  恢复成 "MB Newton" 后立刻正常（12 秒启动）。
  只有 9 个字节的差异，所以基本可以断定它有 PiPL 完整性校验，
  或者 name 被用于内部授权/资源索引。

  这类插件只能整个跳过，不能碰 name。
"""
from __future__ import annotations

import os
from typing import Optional

#: 文件名 -> 不能改名的原因
NO_LOCALIZE_NAME = {
    "Newton.aex": (
        "实测：name 任何改动（全中文或保留 MB 前缀）都会让 AE 启动 6 秒崩溃，"
        "恢复原名后正常。疑似 PiPL 完整性校验。"
    ),
}

#: packed/加壳插件（2026-10-28 自姊妹项目 ae-i18n 1.1.0 实测，其 FINAL_REPORT §7.1）：
#:   入口指向运行时解包内存 —— 进程内钩子挂不上（cross-target），
#:   对它们写 pd->name 会令 addProperty 失败（对方 A1 影子采集实测：Deep Glow）。
#: 结论：packed = capture-only。任何「写名字」的通道（paramnative 写 PF +0x40、
#:   二进制标签、PiPL 槽、FLT 注册表）都应跳过；读取（参数目录采集）不受限。
#: 按模块文件名小写子串匹配；宁可多跳过也不写坏。
PACKED_NAME_PATTERNS = (
    "deepglow",       # Deep Glow (PEDG)
    "glitchify",      # CSpice Glitchify
    "particular_zh",  # Trapcode Particular 中文版
    "cineware",       # Maxon Cineware（Cineware_AE_Effect.aex 实测 cross-target）
    "mocha",          # MochaAE / Mocha Pro
    "universe",       # Red Giant Universe 全系
)


def blocked(path: str) -> Optional[str]:
    """这个文件是否禁止改名。返回原因，允许则返回 None。"""
    return NO_LOCALIZE_NAME.get(os.path.basename(path))


def packed(path: str) -> Optional[str]:
    """这个模块是否 packed（只许采集、不许写名字）。返回命中的模式，否则 None。"""
    name = os.path.basename(path or "").lower()
    for pat in PACKED_NAME_PATTERNS:
        if pat in name:
            return pat
    return None