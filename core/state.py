"""重新设计汉化检测：用【已知中文词命中】而不是字节占比。

★ 为什么字节占比不行（实测数据）：
    Universe Glow 英文原版   2.35%
    3D Stroke 英文原版       4.86%
    BCC 8Bit 原版备份        4.57%
    BCC 8Bit 已汉化          5.84%
    CC Snowfall 已汉化       2.29%   <- 比英文原版还低！

  二进制里 0x81-0xFE 的字节组合到处都是，占比完全不能作为判据。

★ 可靠判据：
  拿词典里的【已知中文译名】去文件里搜。
  真汉化过的插件一定含大量已知中文串；
  英文原版一个都不含（除非巧合）。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .pipl import parse_pipl

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 命中多少个已知中文译名才判定"已汉化"
MIN_HITS = 3

#: 最少采样多少个译名去搜（避免全量搜索太慢）
SAMPLE_SIZE = 400


@dataclass
class LocalizeState:
    """一个插件的汉化状态。"""

    path: str
    localized: bool = False
    reason: str = ""
    zh_names: List[str] = field(default_factory=list)
    hits: int = 0
    samples: List[str] = field(default_factory=list)


def _pick_samples(entries: Dict[str, str]) -> List[bytes]:
    """从词典里挑一批中文译名，作为"已汉化指纹"。

    优先挑 2-3 个汉字的（最不容易巧合命中）。
    """
    cands = []
    for zh in entries.values():
        if not zh or len(zh) < 2 or len(zh) > 4:
            continue
        if not CJK_RE.search(zh):
            continue
        try:
            b = zh.encode("gbk")
        except Exception:
            continue
        if len(b) < 4:
            continue
        cands.append(b)
        if len(cands) >= SAMPLE_SIZE:
            break
    return cands


def detect(path: str, entries: Optional[Dict[str, str]] = None) -> LocalizeState:
    """检测插件是否已汉化。

    entries: 词典 {英文: 中文}。为 None 时只用 PiPL 判断。
    """
    st = LocalizeState(path=path)
    if not os.path.exists(path):
        st.reason = "文件不存在"
        return st

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        st.reason = "读取失败: %s" % exc
        return st

    # ① PiPL 里是否有中文名（最可靠）
    try:
        for s in parse_pipl(data):
            if s.key not in ("name", "category"):
                continue
            txt = s.text()
            if txt and CJK_RE.search(txt):
                st.zh_names.append(txt.strip())
    except Exception:
        pass

    if st.zh_names:
        st.localized = True
        st.reason = "PiPL 含中文：%s" % st.zh_names[0][:20]
        return st

    # ② 用词典里的中文译名做指纹搜索
    if entries:
        samples = _pick_samples(entries)
        hit = 0
        found = []
        for b in samples:
            if b in data:
                hit += 1
                if len(found) < 5:
                    try:
                        found.append(b.decode("gbk"))
                    except Exception:
                        pass
        st.hits = hit
        st.samples = found
        if hit >= MIN_HITS:
            st.localized = True
            st.reason = "命中 %d 个已知中文译名（%s）" % (hit, "、".join(found[:3]))
            return st
        st.reason = "未汉化（仅命中 %d 个中文译名）" % hit
        return st

    st.reason = "未汉化（无词典，只看 PiPL）"
    return st


def should_skip(path: str, entries: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """是否需要跳过。返回 (跳过?, 原因)。"""
    st = detect(path, entries)
    return st.localized, st.reason
