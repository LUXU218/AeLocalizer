"""按插件归属过滤参数名（反向算法）。"""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Sequence, Set, Tuple

#: 单文件大小上限
MAX_FILE_BYTES = 48 * 1024 * 1024
#: 总扫描预算
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_FILES = 400

#: 提取可打印字符串（参数名都是 ASCII 可见字符）
_STR_RE = re.compile(rb"[\x20-\x7e]{2,80}")


def matched_by_files(
    paths: Sequence[str],
    names: Iterable[str],
    *,
    max_bytes: int = MAX_TOTAL_BYTES,
    max_files: int = MAX_FILES,
) -> Tuple[Set[str], bool]:
    """在给定文件里搜参数名，返回 (命中集合, 是否被预算截断)。

    ★ 为什么用"提取字符串再求交集"而不是"对每个名字做 in 搜索"：
      实测 1732 个名字 × 每个文件 = 每个文件搜 1732 次（还有 UTF-16 形态），
      60 组全选的 848MB 要跑 15 秒以上。
      反过来每个文件只正则遍历一次、把出现的字符串丢进集合，
      再和名字集合求交集 —— 复杂度从 O(文件数×名字数) 降到 O(总字节数)。
    """
    want: Set[str] = {n for n in names if n and 2 <= len(n) <= 80}
    if not want:
        return set(), False

    found: Set[str] = set()
    used_bytes = 0
    used_files = 0
    truncated = False

    for p in paths:
        if not p or not p.lower().endswith(".aex"):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        if used_files >= max_files or used_bytes + size > max_bytes:
            truncated = True
            break
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        used_files += 1
        used_bytes += size

        for m in _STR_RE.finditer(data):
            s = m.group(0).decode("ascii", "ignore").strip()
            if s and s in want:
                found.add(s)
        if len(found) == len(want):
            break

    return found, truncated


def filter_for_groups(groups, names: Iterable[str]) -> Tuple[Set[str], Set[str], bool]:
    """按勾选的插件组过滤参数名。返回 (命中, 未归属, 是否截断)。

    ★ 截断时的保守策略（2026-10-22）：
      扫描预算不够覆盖勾选插件的全部文件时，无法可靠判断归属。
      这时候【把所有名字都算进来】而不是返回 0 条 ——
      因为这些名字本来就可能属于勾选的插件，漏掉会让人以为「没有参数要翻」。
      （典型场景：用户点全选 60 组 = 848MB，预算 96MB 扫不完。）
    """
    names = list(names)
    paths: List[str] = []
    for g in groups:
        for f in g.files:
            paths.append(f.path)
    hit, truncated = matched_by_files(paths, names)
    if truncated:
        # 预算不足 -> 保守：全部包含，宁可多翻不可漏翻
        return set(names), set(), True
    miss = set(names) - hit
    return hit, miss, False
