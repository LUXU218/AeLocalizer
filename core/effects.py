"""效果名词典层：把 effect_names.json 之类的「效果名 → 中文」映射接入分层词典。

为什么单独一层：
  参数字典（20,172 条）里**没有效果名** —— 效果名是 PiPL 里的独立字段，
  和参数名是两套体系。所以效果名汉化必须用另一份映射。

键的形态（三选一，按优先级）：
  1. 完整 name（如 "S_Blur"）
  2. 去掉 "S_" 前缀（如 "Blur"）
  3. matchName 去掉厂商前缀（如 "S_Blur" -> "Blur"）
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional


def load_effect_names(path: str) -> Dict[str, str]:
    """读 effect_names.json（{英文: 中文}）。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, str) and v}


def normalize_effect_key(name: str) -> str:
    """去掉常见厂商前缀与首尾空格。"""
    n = name.strip()
    for prefix in ("S_", "BCC_", "BCC+", "ADBE ", "Trapcode "):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    return n.strip()


#: 需要保留的厂商前缀（查表时去掉，写回时补上）
#: ★ 注意 "BCC " 带空格、"BCC+" 不带空格 —— 实测 BCC 效果名三种写法都有：
#:    "BCC Blur"（老系列，有空格）
#:    "BCC+Ambient Light"（BCC+ 系列，无空格）
#:    "BCC_ICEHALOS"（matchName，下划线）
KEEP_PREFIXES = ("S_", "BCC+", "BCC ", "BCC_", "ADBE ")


def _prefix_of(name: str) -> tuple:
    """拆出 (前缀, 剩余部分)。"""
    n = name.strip()
    for p in KEEP_PREFIXES:
        if n.startswith(p):
            return p, n[len(p):].strip()
    return "", n


def resolve_effect_name(
    display_name: str,
    match_name: str,
    table: Dict[str, str],
) -> Optional[str]:
    """在效果名表里查一条。

    ★★ 前缀保留规则（实测教训）：
      Sapphire 的效果显示名是 "S_ChannelSwitcher"，而效果名表的键是
      "ChannelSwitcher"（没有 S_ 前缀）。若直接返回表里的值，
      就会把 "S_" 丢掉 —— 旧版汉化显示的是 "S_通道切换"，
      我们的版本变成了 "通道切换"，前缀没了。

      正确做法：查表时去掉前缀，**写回时把原前缀补上**。
    """
    if not table:
        return None

    raw = display_name.strip()
    prefix, rest = _prefix_of(raw)

    # 候选顺序：完整名 → 去前缀名 → matchName → matchName 去前缀
    cands = [raw]
    if rest and rest != raw:
        cands.append(rest)
    if match_name:
        m = match_name.strip()
        if m not in cands:
            cands.append(m)
        mn = normalize_effect_key(m)
        if mn and mn not in cands:
            cands.append(mn)

    for c in cands:
        hit = table.get(c)
        if hit:
            # 表里的值若已带前缀，直接用；否则补回原前缀
            if prefix and not hit.startswith(prefix.strip()):
                return prefix + hit
            return hit
    return None


#: AE 给「同类第二个及以后实例」自动加的编号后缀：'CC Light Rays 2'。
#: 实测（AE 2022 / 2025 / 2026 一致）是「原名 + U+0020 + 十进制序号」，
#: 不是 NBSP、也不是 '(2)'。词典里只有无序号的原名，所以第二份起永远查不到。
_INSTANCE_SUFFIX_RE = re.compile(r"^(.*?)(\s+(\d{1,3}))$", re.S)


def split_instance_suffix(name: str) -> tuple:
    """把 'CC Light Rays 2' 拆成 ('CC Light Rays', ' 2')；无后缀返回 (name, '')。

    只拆「空白 + 纯数字」。真实产品名里也有数字（'BCC Noise Map 2'、
    'Primatte Keyer 6'），所以调用方必须先精确查表，未命中才用本函数兜底。
    """
    m = _INSTANCE_SUFFIX_RE.match(name or "")
    if not m:
        return name, ""
    return m.group(1), m.group(2)


def resolve_effect_name_instance(
    display_name: str,
    match_name: str,
    table: Dict[str, str],
) -> Optional[str]:
    """先按原名精确查表；查不到再去掉 AE 的实例序号后缀查，命中后把序号拼回。

    ★ 为什么必须先精确查再剥后缀：'BCC Noise Map 2'、'Primatte Keyer 6' 这类
      真实效果名本身就带数字，直接剥后缀会把它们拆成别的效果（'BCC Noise Map'），
      译名跟着错。精确命中就原样返回，保证这些既有行为一个字都不变。
    """
    hit = resolve_effect_name(display_name, match_name, table)
    if hit:
        return hit
    base, suffix = split_instance_suffix((display_name or "").strip())
    if not suffix:
        return None
    m_base, _m_suffix = split_instance_suffix((match_name or "").strip())
    hit = resolve_effect_name(base, m_base, table)
    if not hit:
        return None
    return hit + suffix


def merge_into_store(store, table: Dict[str, str]) -> int:
    """把效果名表并进 DictStore 的内置层；返回新增条数。"""
    if not table:
        return 0
    builtin = dict(store._layers.get("builtin") or {})
    added = 0
    for k, v in table.items():
        if k not in builtin:
            builtin[k] = v
            added += 1
    if added:
        store._layers["builtin"] = builtin
        store._invalidate()
    return added
