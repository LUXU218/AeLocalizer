"""译名缩短：GBK/UTF-8 长度闸下的"一键安全缩短"。

规则与旧引擎 _shrink_variants 同源，但**可预测、可测试**：
  1. 去空格
  2. 常见冗词替换（不透明度→透明、数量→数、调整→"" …）
  3. 前缀截断（保头去尾）

返回**能放下的最短候选**；一个都放不下时返回 None（调用方据此判定"会被引擎跳过"）。
"""
from __future__ import annotations

from typing import List, Optional

#: 冗词替换表（顺序敏感：先长后短）
REDUCTIONS = [
    ("不透明度", "透明"),
    ("不透明", "透明"),
    ("透明度", "透明"),
    ("偏移量", "偏移"),
    ("模糊量", "模糊"),
    ("显示量", "显示"),
    ("调整", ""),
    ("大小", ""),
    ("尺寸", ""),
    ("数量", "数"),
    ("长度", "长度"),
    ("的", ""),
]


#: 厂商/系列前缀：中文译名里保留它们纯属浪费字节
#: （AE 界面上本来就在分类里区分厂商，参数名里再带一遍没意义）
VENDOR_PREFIXES = (
    "Boris Continuum Complete ",
    "Boris FX ",
    "Sapphire ",
    "VideoCopilot ",
    "BCC ",
    "CC ",
    "S_",
    "Sapphire_",
)

#: 纯 ASCII 的候选至少要这么长才算有意义
_MIN_ASCII = 3


def _is_junk(cand, original):
    # 判断缩短候选是不是垃圾（2026-10-22 实测问题）
    # 现象: BCC Blur Dslv（13字节）的译文 BCC 模糊溶解 16字节，
    #       逐字截断最后给出 BC，用户看到一堆 BC 完全不知道是什么。
    if not cand:
        return True
    if cand.isascii():
        # 纯英文候选：太短、或只是原文英文前缀的缩水（"BC"/"BCC" 这类）
        if len(cand) < _MIN_ASCII:
            return True
        if original.startswith(cand) and len(cand) < len(original) * 0.6:
            return True
        return False
    # 含中文：两个字就够表达（"橙色"/"模糊"），一个字才可疑
    return len(cand) < 2

def gbk_len(s: str) -> Optional[int]:
    try:
        return len(s.encode("gbk"))
    except Exception:
        return None


def _split_vendor(zh):
    # 拆成 (厂商前缀, 其余)。前缀【原样保留】绝不删。
    # 2026-10-22 用户要求：BCC/CC/Sapphire 这些厂商名必须留着，
    # 否则界面上只剩模糊溶解，根本不知道是哪个厂商的效果。
    for p in VENDOR_PREFIXES:
        if zh.startswith(p):
            return p, zh[len(p):]
    return "", zh


def _shrink_body(s):
    # 对【不含厂商前缀】的部分做冗词替换 + 截断。
    out = []
    def add(x):
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    add(s)
    add(s.replace(" ", ""))
    base = s
    for a, b in REDUCTIONS:
        if a in base:
            x = base.replace(a, b)
            add(x)
            base = x
    core = s
    for i in range(len(core) - 1, 1, -1):
        add(core[:i])
    return out


def candidates(zh: str) -> List[str]:
    # 生成候选（去重，保持顺序）。厂商前缀始终保留。
    out: List[str] = []
    def add(x):
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    add(zh)
    pfx, body = _split_vendor(zh)
    for shorter in _shrink_body(body):
        add(pfx + shorter if pfx else shorter)
    return out

def shortest_fitting(zh: str, max_bytes: int) -> Optional[str]:
    """返回 GBK 字节数 <= max_bytes 的【最长】候选；都不行返回 None。

    ★ 2026-10-22 修正：原来取"最短"，结果「BCC 模糊溶解」明明能放下完整
      四个汉字（GBK 8 字节 <= 13），却只取了「模糊溶」（6 字节）—— 白丢信息。
      现在取能放下的最长候选，一样长时保留生成顺序靠前的（更自然）。
    """
    best: Optional[str] = None
    best_n = -1
    for c in candidates(zh):
        if _is_junk(c, zh):
            continue
        n = gbk_len(c)
        if n is None or n > max_bytes:
            continue
        if n > best_n:
            best = c
            best_n = n
    return best
