"""名字归属：判断某个英文名（参数名/效果名）属于哪个插件组。

为什么需要它
------------
"让用户自己选择汉化哪些插件" 这条需求，落到实现上有两条完全不同的路径：

  L1 效果名  —— 改的是 .aex 文件本身，文件就属于某个组，天然可归属，
                勾选哪个组就写哪个组的文件，零成本。
  L2 参数名  —— 走运行时 hook，引擎拿到的是一张 {英文: 中文} 的全局表，
                它并不知道 "Blur Amount" 是 Sapphire 的还是 BCC 的。
                所以必须静态把名字反查到插件上，才能在生成引擎词典时
                只放行被勾选插件的名字。

归属怎么做（以及为什么不用"子串搜索"）
--------------------------------------
最初想用 key in file_bytes 直接搜 —— 实测 3000 个键 × 62MB DLL 要 40 秒，
2 万个键要 4 分钟以上，不可接受。

改为【按词切分 + n-gram 查表】：
  1. 一次正则扫过文件，切出所有 ASCII 词
  2. 对每个位置，把 1..MAX_WORDS 个连续词用单空格拼起来，查一次键集合
  3. 命中即记归属

这样每次查表是 O(1) 哈希，总代价 ≈ 词数 × MAX_WORDS，实测 200MB 语料 ~20s，
结果缓存到磁盘后二次加载 < 0.2s。

为什么 n-gram 而不是整串精确匹配
--------------------------------
实测发现插件把参数名嵌在更长的文本块里，而不是 NUL 分隔的独立字符串：
    'dissolve_amt ( shared percent ) ) dissolve_speed glint_brig'
    'htness_diag1 ( :modify brightness_diag2 ( group_end Brightne'
所以 "Amount"、"Brightness" 这类词单独切成 token 是切不出来的，
必须允许"从任意词开始取 1..6 个词"去匹配。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Iterable, List, Optional, Set

#: 一个名字最多由几个词组成（实测最长的参数名 6 个词足够）
MAX_WORDS = 6

#: 词切分：首字符必须是字母，后续允许常见连接符
_WORD_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_'&/+.\\-]*")

#: 归属索引的缓存格式
CACHE_SCHEMA = "aeloc-attrib/1"


def extract_words(data: bytes) -> List[str]:
    """把字节串切成 ASCII 词列表（保留顺序，用于 n-gram）。"""
    return [m.group(0).decode("latin-1") for m in _WORD_RE.finditer(data)]


def find_names_in_words(
    words: List[str],
    keys: Set[str],
    *,
    max_words: int = MAX_WORDS,
) -> Set[str]:
    """在一串词里找出哪些键出现过（按 1..max_words 个词拼接匹配）。

    只做纯查表，不做模糊匹配 —— 命中即"这个词组在文件里出现过"。
    """
    hit: Set[str] = set()
    n = len(words)
    # ★ 性能关键：绝大多数词根本不可能是一个键的开头。
    #   预先把"所有键的首词"建成集合，首词不在里面就直接跳过内层循环 ——
    #   实测把 1271 个文件的扫描从 142s 压到 ~15s（同样的命中结果）。
    firsts = _first_words(keys, max_words)
    for i in range(n):
        w = words[i]
        if w not in firsts:
            continue
        if w in keys:
            hit.add(w)
        acc = w
        for j in range(1, max_words):
            if i + j >= n:
                break
            acc = acc + " " + words[i + j]
            if acc in keys:
                hit.add(acc)
    return hit


def _first_words(keys: Iterable[str], max_words: int) -> Set[str]:
    """所有键的"首词"集合（含单键首词）。"""
    out: Set[str] = set()
    for k in keys:
        parts = k.split(" ", 1)
        if parts and parts[0]:
            out.add(parts[0])
    return out


def scan_file(path: str, keys: Set[str]) -> Set[str]:
    """扫一个文件，返回其中出现过的键。读失败返回空集（不抛）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return set()
    if not data:
        return set()
    return find_names_in_words(extract_words(data), keys)


def build_index(
    group_files: Dict[str, List[str]],
    keys: Iterable[str],
    *,
    shared_files: Optional[Dict[str, List[str]]] = None,
    on_progress=None,
) -> Dict[str, List[str]]:
    """建立 {名字: [组 id, ...]} 归属索引。

    ★ 键必须归一化后再查：词典里大量条目带首尾空格（" Amount "），
      而文件里切出来的词不带 —— 不归一化会导致这些名字永远归属不到，
      进而被当成"未归属"处理，用户勾了插件却发现没过滤掉。
    """
    # 归一化 -> 原始键列表（一个归一化名可能对应多个原始键）
    keyset = {_norm(k) for k in keys if k}
    name_groups: Dict[str, List[str]] = {}
    total = sum(len(v) for v in group_files.values())
    done = 0
    for gid, paths in group_files.items():
        for p in paths:
            done += 1
            if on_progress and done % 100 == 0:
                on_progress(done, total)
            for name in scan_file(p, keyset):
                _add_owner(name_groups, name, gid)

    # ★ 厂商共享文件：同一个 DLL 被同厂商的所有插件共用（Sapphire 的
    #   sapphire_ae.dll、BCC 的 Continuum_AE_*.dll）。名字出现在这些 DLL 里，
    #   说明它属于该厂商 —— 但具体是哪个效果无法从静态数据分辨，
    #   所以把归属记到该厂商【全部】组上。
    #   实测：只扫 .aex 时 Sapphire 只能归属 38 个名字；加上 DLL 后变成 2178 个。
    for vendor_key, paths in (shared_files or {}).items():
        gids = vendor_key.split("|")
        for p in paths:
            for name in scan_file(p, keyset):
                for gid in gids:
                    _add_owner(name_groups, name, gid)
    return name_groups


def _add_owner(index: Dict[str, List[str]], name: str, gid: str) -> None:
    lst = index.get(name)
    if lst is None:
        index[name] = [gid]
    elif gid not in lst:
        lst.append(gid)


def _norm(name: str) -> str:
    """多空白压成一个空格并去首尾（与 enginedict 完全一致）。"""
    return " ".join(name.split())


# ---------------------------------------------------------------- 厂商共享文件


#: 厂商引擎目录（插件把参数名放在这些 DLL 里，而不是各个 .aex 里）。
#: 键是"目录候选"，值是匹配的组 id 前缀规则（在 discover_shared_files 里算）。
VENDOR_LIB_DIRS: Dict[str, List[str]] = {
    "Sapphire": [
        r"C:\Program Files\BorisFX\Sapphire 2025 Adobe\lib64",
        r"C:\Program Files\BorisFX\Sapphire 2024 Adobe\lib64",
        r"C:\Program Files\BorisFX\Sapphire 2023 Adobe\lib64",
    ],
    "Boris FX": [
        r"C:\Program Files\BorisFX\ContinuumAE\18\lib",
        r"C:\Program Files\BorisFX\ContinuumAE\17\lib",
    ],
}

#: 单个共享文件的大小上限（超过的跳过，避免把几个 GB 的素材包读进内存）
MAX_SHARED_BYTES = 400 * 1024 * 1024


def discover_shared_files(
    groups: Iterable[tuple],
    *,
    vendor_dirs: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    """找出"厂商共享 DLL"，返回 {组 id 用 | 连接: [文件]}。

    groups: [(组 id, 厂商), ...]
    返回的键是若干组 id 用 '|' 拼起来的，表示"这些组共享这批文件"。
    """
    table = vendor_dirs if vendor_dirs is not None else VENDOR_LIB_DIRS
    by_vendor: Dict[str, List[str]] = {}
    for gid, vendor in groups:
        by_vendor.setdefault(vendor, []).append(gid)

    out: Dict[str, List[str]] = {}
    for vendor, dirs in table.items():
        gids = by_vendor.get(vendor) or []
        if not gids:
            continue
        files: List[str] = []
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, _dirs, names in os.walk(d):
                for n in names:
                    if not n.lower().endswith((".dll", ".aex")):
                        continue
                    p = os.path.join(root, n)
                    try:
                        if os.path.getsize(p) > MAX_SHARED_BYTES:
                            continue
                    except OSError:
                        continue
                    files.append(p)
        if files:
            out["|".join(gids)] = files
    return out


# ---------------------------------------------------------------- 缓存


def cache_path(data_root: str, ae_version: str) -> str:
    return os.path.join(data_root, "cache", "attrib-%s.json" % (ae_version or "0"))


def save_index(path: str, index: Dict[str, List[str]], *, scanned: int = 0) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    obj = {
        "schema": CACHE_SCHEMA,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scanned_files": scanned,
        "index": index,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def load_index(path: str) -> Optional[Dict[str, List[str]]]:
    """读缓存；格式不符/损坏返回 None（调用方重新扫描）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("schema") != CACHE_SCHEMA:
        return None
    idx = obj.get("index")
    if not isinstance(idx, dict):
        return None
    return idx


def index_stats(index: Dict[str, List[str]]) -> Dict[str, int]:
    shared = sum(1 for v in index.values() if len(v) > 1)
    return {
        "names": len(index),
        "shared": shared,
        "exclusive": len(index) - shared,
    }
