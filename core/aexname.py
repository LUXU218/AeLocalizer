"""从 aex 内部残留的英文名还原效果名（通用）。

★ 2026-10-22 实测发现：
  Red Giant Universe 的 aex 里，除了 PiPL 的 name 字段（被我们改成了
  "uni.模糊"），文件内部还留着英文原名 "uni.Blur"。
  而它的 matchName 是内部文件名（Universe_Blur_Blur_Premium_AE_Fx，26 字节），
  比 name 槽位（8 字节）长得多，没法用来重建 ——
  所以之前的「还原效果名」对 Red Giant 完全无效（104 个全卡在中文）。

  这里改为从文件内部找 "uni.<英文>" 形式的串，取能塞进槽位的最长一个。
"""
from __future__ import annotations

import os
import re
from typing import Optional

#: 匹配 uni.<英文名>
_UNI_RE = re.compile(rb"uni\.[A-Za-z][A-Za-z0-9 .]{0,48}")
#: 中文
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _fit_word_boundary(s: bytes, limit: int) -> bytes:
    """把 s 截到 <= limit 字节，尽量在空格处断（不切半个单词）。"""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    sp = cut.rfind(b" ")
    if sp > 0:
        cut = cut[:sp]
    return cut.rstrip()


#: 这些不是效果名，是插件内部命令/标识，绝不能拿来当显示名
_BLACKLIST = (b"uni.dashboard", b"uni.internal", b"uni.core", b"uni.registry")


def english_name_from_aex(
    data: bytes, slot_len: int, hint: str = ""
) -> Optional[bytes]:
    """从 aex 内部找能塞进 slot_len 的英文名（形如 uni.Blur）。

    hint: 文件名（如 Universe_Blur_Compound_Blur_Premium_AE_Fx.aex），
          用来在多个候选里挑出跟这个效果对得上的那个。
    """
    words = [w.lower() for w in re.split(r"[_\-. ]+", hint) if len(w) > 2]
    # 去掉通用词，避免把 "universe"/"premium" 这种当特征
    words = [w for w in words if w not in ("universe", "ae", "fx", "premium", "pro")]

    best: Optional[bytes] = None
    best_score = -1
    for m in _UNI_RE.finditer(data):
        raw = m.group(0).rstrip()
        if not raw:
            continue
        low = raw.lower()
        if any(b in low for b in _BLACKLIST):
            continue
        fit = _fit_word_boundary(raw, slot_len)
        if not fit or len(fit) < 4:
            continue
        # 打分：命中文件名关键词越多越可信，其次越长越好
        fl = fit.lower()
        score = sum(1 for w in words if w.encode() in fl) * 100 + len(fit)
        if score > best_score:
            best, best_score = fit, score
    return best


def restore_one(path: str, dry_run: bool = True) -> int:
    """还原单个 aex 的效果名。返回 1=改了，0=没改。"""
    from .pipl import parse_pipl

    try:
        data = bytearray(open(path, "rb").read())
    except OSError:
        return 0

    ent = None
    for s in parse_pipl(bytes(data)):
        if s.key == "name":
            ent = s
            break
    if ent is None:
        return 0

    cur = bytes(data[ent.offset:ent.offset + ent.length])
    try:
        txt = cur.rstrip(b"\x00 ").decode("gbk")
    except UnicodeDecodeError:
        return 0
    if not _CJK_RE.search(txt):
        return 0        # 已经是英文了

    eng = english_name_from_aex(bytes(data), ent.length, os.path.basename(path))
    if not eng:
        return 0
    if dry_run:
        return 1
    try:
        data[ent.offset:ent.offset + ent.length] = (
            eng + b" " * (ent.length - len(eng)))
        with open(path, "wb") as fp:
            fp.write(data)
        return 1
    except (PermissionError, OSError):
        return 0


def restore_dir(root: str, dry_run: bool = True) -> int:
    """递归还原一个目录下所有 aex。"""
    n = 0
    for cur, _d, files in os.walk(root):
        for f in files:
            if not f.lower().endswith(".aex"):
                continue
            n += restore_one(os.path.join(cur, f), dry_run=dry_run)
    return n
