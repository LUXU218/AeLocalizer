"""生成运行时引擎（guard_uni）需要的词典目录。

为什么需要这一层
----------------
guard_uni 只认一个扁平的 {英文: 中文} JSON（extra_overrides.json），
它不关心"这条译名属于哪个插件"。而本软件的核心需求是
**让用户自己选择汉化哪些插件** —— 所以必须在把词典交给引擎之前
先按用户的勾选做一次过滤。

过滤依据（按优先级，前者能判定就不看后者）：
  1. 归属索引（core/attrib.py）：这个名字在哪些插件文件里出现过
     -> 只放行"出现过的插件里至少有一个被勾选"的名字
  2. 无归属的名字：索引里查不到（插件把名字加密/压缩了，实测 61%）
     -> 保守放行（宁可多汉化，也不要让用户勾了插件却没效果）

为什么无归属的放行而不是拦下
----------------------------
实测 20,111 个参数名里只有 7,816 个（38.9%）能静态归属到具体插件文件，
剩下 61% 在 DLL 里是压缩/加密/动态拼装的。如果按"查不到就拦"处理，
用户勾了 Sapphire 却发现大半参数名还是英文，这是更糟的体验。
放行的代价是：某个没勾的插件如果恰好用了同名参数，也会被汉化。
实测同名冲突只有 2,557 个（32.7%），且冲突双方的译名通常一致。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

#: 引擎认的词典文件名（guard_uni 的 _find_dict_dir 就是找它）
DICT_FILE = "extra_overrides.json"
#: 引擎优先认的加密版（目录解析先看 .dat）
DICT_DAT = "extra_overrides.dat"

#: 枚举词典（下拉值通道）—— 必须同时放到我们的目录，
#: 否则引擎只看到硬编码的 338 条，大量枚举词会丢失（实测教训）
ENUM_BASE = "enum_fill_20261011"
ENUM_DAT = ENUM_BASE + ".dat"

#: 术语修正层（优先级最高的那一层，见 core/termfix.py）
from .termfix import TERM_FIX_DAT, clear_term_fix, write_term_fix  # noqa: E402


@dataclass
class DictBuildResult:
    """一次引擎词典生成的结果。"""

    path: str = ""
    entries: int = 0
    total: int = 0
    attributed: int = 0
    dropped: int = 0
    unowned_kept: int = 0
    dropped_sample: List[str] = field(default_factory=list)
    #: 枚举词典条数（下拉值通道）
    enum_entries: int = 0
    #: 枚举词典是否就绪
    enum_ready: bool = False
    #: 术语修正层条数（term_fix.dat）
    term_fix: int = 0
    #: 实际发布的 extra_overrides.json 字节 SHA-256
    payload_sha256: str = ""

    def summary(self) -> str:
        if not self.total:
            return "词典为空"
        base = "引擎词典 %d 条（总 %d，归属 %d，未归属放行 %d，按勾选剔除 %d）" % (
            self.entries, self.total, self.attributed,
            self.unowned_kept, self.dropped,
        )
        if self.enum_ready:
            base += " · 下拉值 %d 条" % self.enum_entries
        else:
            base += " · 下拉值词典缺失"
        if self.term_fix:
            base += " · 术语修正 %d 条" % self.term_fix
        return base


def _normalize(name: str) -> str:
    """与 attrib 一致的归一化：多空白压成一个、去首尾。"""
    return " ".join(name.split())


def filter_entries(
    entries: Dict[str, str],
    attribution: Dict[str, List[str]],
    selected_groups: Set[str],
    *,
    keep_unowned: bool = True,
) -> Dict[str, str]:
    """按勾选的插件组过滤译名表。

    entries:      {英文: 中文}，来自 DictStore.resolve()
    attribution:  {英文: [组 id, ...]}，来自 core.attrib
    selected_groups: 用户勾选的组 id 集合
    keep_unowned: 无归属的名字是否放行（默认放行）
    """
    out: Dict[str, str] = {}
    for key, zh in entries.items():
        if not key or not zh:
            continue
        owners = attribution.get(_normalize(key))
        if not owners:
            if keep_unowned:
                out[key] = zh
            continue
        if any(g in selected_groups for g in owners):
            out[key] = zh
    return out


def build_engine_dict(
    entries: Dict[str, str],
    out_dir: str,
    *,
    attribution: Optional[Dict[str, List[str]]] = None,
    selected_groups: Optional[Set[str]] = None,
    keep_unowned: bool = True,
    drop_keys: Optional[Set[str]] = None,
) -> DictBuildResult:
    """把译名表写成引擎能读的词典目录。

    attribution 为 None 或 selected_groups 为 None 时不做过滤（全量导出）。
    """
    res = DictBuildResult(total=len(entries))
    data = entries
    if attribution is not None and selected_groups is not None:
        data = filter_entries(entries, attribution, selected_groups, keep_unowned=keep_unowned)
        res.dropped = len(entries) - len(data)
        res.dropped_sample = [k for k in entries if k not in data][:12]
    if drop_keys:
        dropped = [k for k in data if k in drop_keys]
        if dropped:
            data = {k: v for k, v in data.items() if k not in drop_keys}
            res.dropped += len(dropped)

    # 统计"能归属到插件"的条数（用于报告）
    if attribution:
        res.attributed = sum(1 for k in data if attribution.get(_normalize(k)))
        res.unowned_kept = len(data) - res.attributed
    else:
        res.unowned_kept = len(data)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, DICT_FILE)
    # Concurrent readers (managed runtime) must never observe a truncated or
    # half-written override file. Unique temp + fsync + Windows sharing retry.
    import hashlib
    import uuid

    blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
    res.payload_sha256 = hashlib.sha256(blob).hexdigest()
    tmp = path + ".tmp." + uuid.uuid4().hex
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    for i in range(40):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 39:
                raise
            time.sleep(0.02)

    # ★ 必须同时写 .dat：引擎的目录解析把 .dat 放在外层循环，
    #   只写 .json 抢不到它自己目录里的全量 .dat（实测确认）。
    from .legacy.writer import write_dat

    write_dat(data, os.path.join(out_dir, DICT_DAT))

    # ★★ 枚举词典也必须补一份（实测教训）：
    #   设置 SAPPHIRE_CN_DICT 后引擎从我们目录读 enum_fill_20261011，
    #   那里没有 -> 枚举表只剩硬编码 338 条（原版 11713 条），
    #   "Texture Only" / "Mult" 这类下拉值全部丢失。
    res.enum_entries = _ensure_enum_dict(out_dir, data)
    res.enum_ready = res.enum_entries > 0

    # ★★ 术语修正层必须每次重建都重写一遍：
    #   它在引擎的合并顺序里排第一（WIN.build_dict 用 setdefault），
    #   是唯一能压住 extra_overrides 里历史错译的位置。只写 extra_overrides
    #   的话，下一次重建又会把 "Glow = 发光" 这类错条目翻出来。
    try:
        res.term_fix = write_term_fix(out_dir)
    except Exception:
        res.term_fix = 0

    res.path = path
    res.entries = len(data)
    return res


def _ensure_enum_dict(out_dir: str, words: Dict[str, str]) -> int:
    """保证 out_dir 里有完整的枚举词典。返回写入的条数（失败返回 0）。

    ★ 为什么枚举词典必须单独处理（实测教训，两次踩坑）：
      ① 引擎的枚举通道按【单词】查表（不是整串）：
            enumLookup(parts[pi])   // 逐词
         所以整串译文对下拉值无效，必须保证【每个单词】都在表里。
      ② 设置 SAPPHIRE_CN_DICT 后，引擎从【我们自己的目录】读
         enum_fill_20261011.dat。那里没有 -> 枚举表只剩硬编码的 338 条
         （原版 11,713 条），"Texture Only" / "Mult" 这类词全部丢失。

    做法：从引擎原目录复制基础表，再合并词典里所有单词级译名。
    """
    dst = os.path.join(out_dir, ENUM_DAT)
    try:
        from .legacy.reader import read_dict_file
        from .legacy.writer import write_dat

        base: Dict[str, str] = {}
        src = os.environ.get("AELOC_LEGACY_ENUM_PATH", "").strip()
        if os.path.isfile(src):
            try:
                base = read_dict_file(src) or {}
            except Exception:
                base = {}

        merged = dict(base)
        for k, v in words.items():
            if not k or not v:
                continue
            # 枚举通道只查单词；整串条目对下拉值无效
            if "|" in k:
                continue
            merged[k] = v

        if not merged:
            return 0
        write_dat(merged, dst)
        return len(merged)
    except Exception:
        return 0


#: 词典条数缓存 {(路径, mtime): 条数}
_READY_CACHE: dict = {}


def _cached_count(p: str) -> int:
    """按 mtime 缓存词典条数。

    ★ 性能（2026-10-21）：读 2 万条 .dat 要 0.5s，
      而 engineReport 每次刷新都会调，导致点按钮卡顿。
    """
    try:
        sig = (p, os.path.getmtime(p))
    except OSError:
        return 0
    hit = _READY_CACHE.get(sig)
    if hit is not None:
        return hit
    try:
        from .legacy.reader import read_dict_file

        n = len(read_dict_file(p))
    except Exception:
        n = 0
    _READY_CACHE.clear()
    _READY_CACHE[sig] = n
    return n


def enum_dict_ready(out_dir: str) -> int:
    """枚举词典就绪则返回条数。"""
    p = os.path.join(out_dir, ENUM_DAT)
    if not os.path.exists(p):
        return 0
    return _cached_count(p)


def clear_engine_dict(out_dir: str) -> bool:
    """清掉引擎词典（让引擎下次启动时读不到译名 -> 全英文）。

    ★ 枚举词典也要清 —— 否则下拉值仍是中文，与"全部还原"的语义不符。
    """
    ok = clear_term_fix(out_dir)
    for name in (DICT_FILE, DICT_DAT, ENUM_DAT):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                ok = False
    return ok


_DICT_READY_CACHE: dict = {}
"""out_dir -> ((mtime_ns, size), 条数)。engine.check 是高频调用,
   每次 engine.check 都完整解密 990KB 的 .dat 太浪费(2026-11-01),
   按 mtime+size 缓存;文件一变自动失效。"""


def engine_dict_ready(out_dir: str) -> int:
    """引擎词典就绪则返回条数，否则 0。

    ★ 以 .dat 为准 —— 引擎实际读的是它（.dat 优先级高于 .json）。
    """
    p = os.path.join(out_dir, DICT_DAT)
    if os.path.exists(p):
        from .legacy.writer import decrypt_legacy

        try:
            st = os.stat(p)
            sig = (st.st_mtime_ns, st.st_size)
            hit = _DICT_READY_CACHE.get(out_dir)
            if hit and hit[0] == sig:
                return hit[1]
            with open(p, "rb") as f:
                obj = decrypt_legacy(f.read())
            if isinstance(obj, dict):
                n = len(obj)
                _DICT_READY_CACHE[out_dir] = (sig, n)
                return n
        except Exception:
            pass
    p = os.path.join(out_dir, DICT_FILE)
    if not os.path.exists(p):
        return 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return 0
    return len(obj) if isinstance(obj, dict) else 0
