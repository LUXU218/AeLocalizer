"""AE 模块哈希钉死。

★ 目的：
  如果 AE 被补丁版/精简版替换过（AfterFX.exe 或 FLT.dll 被改），
  内存布局、函数签名都会变，此时写内存可能损坏 AE 甚至崩溃。
  所以启动引擎前先校验这两个模块的哈希，不匹配就拒绝写入。

★ 策略：
  首次运行记录基线（已知良好），之后每次比对。
  基线保存在 data_root/module_baseline.json。
  用户换 AE 版本时需要重新「信任」，所以提供 reset()。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

#: 需要钉死的模块（相对 AE 安装根目录）
PINNED = [
    ("AfterFX.exe", os.path.join("", "AfterFX.exe")),
    ("FLT.dll", os.path.join("", "FLT.dll")),
]


@dataclass
class ModuleHash:
    name: str = ""
    path: str = ""
    size: int = 0
    sha256: str = ""
    exists: bool = False


def _sha256(path: str, chunk: int = 1 << 20) -> tuple:
    """返回 (sha256, size)。大文件分块读，避免一次性 200MB。"""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def module_hash(ae_root: str) -> List[ModuleHash]:
    """计算 AE 关键模块的哈希。"""
    out: List[ModuleHash] = []
    support = os.path.join(ae_root, "Support Files")
    for name, rel in PINNED:
        # 先试 Support Files，再试根目录
        for base in (support, ae_root):
            p = os.path.join(base, os.path.basename(rel))
            if os.path.exists(p):
                try:
                    sha, size = _sha256(p)
                except OSError:
                    continue
                out.append(ModuleHash(name=name, path=p, size=size,
                                      sha256=sha, exists=True))
                break
        else:
            out.append(ModuleHash(name=name, exists=False))
    return out


def _baseline_path(data_root: str) -> str:
    return os.path.join(data_root, "module_baseline.json")


def version_of(ae_root: str) -> str:
    """从 AE 安装路径取版本号("...Adobe After Effects 2025" -> "2025")。

    ★ 基线必须按版本分开存:老实现是全局单份,信任 2026 之后 2021~2025
      全部被判"模块已被替换"而拒绝启动引擎(用户实测:只有 25/26 能进)。
    """
    m = re.search(r"After Effects (\d{4})", ae_root or "")
    return m.group(1) if m else ""


def load_baseline(data_root: str, version: str = "") -> Dict[str, str]:
    """读基线。返回 {模块名: sha256}。按版本分桶;兼容旧的全局单份格式。"""
    p = _baseline_path(data_root)
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    if version and version in d and isinstance(d[version], dict):
        bucket = d[version]
        return {k: v.get("sha256", "") for k, v in bucket.items()
                if isinstance(v, dict)}
    # 旧格式(顶层直接是 {模块名:{...}},全局单份)作废:按版本重建,
    # 避免拿别的版本的哈希来判当前版本(实事故:信任 2026 后 21~25 全被拒)。
    return {}


def save_baseline(data_root: str, mods: List[ModuleHash],
                  version: str = "") -> str:
    """把当前哈希存为该版本的基线。返回文件路径。"""
    os.makedirs(data_root, exist_ok=True)
    p = _baseline_path(data_root)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict) or "AfterFX.exe" in d:
        d = {}   # 旧格式整体作废,按版本重建
    d[version or ""] = {m.name: asdict(m) for m in mods if m.exists}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return p


@dataclass
class VerifyResult:
    ok: bool = True
    reason: str = ""
    first_run: bool = False
    checked: int = 0
    mismatched: List[str] = None  # type: ignore


def verify(ae_root: str, data_root: str, *, trust_first: bool = True,
           version: str = "") -> VerifyResult:
    """校验 AE 模块哈希（按版本分桶的基线）。

    trust_first=True 时，该版本没有基线就自动建立（首次运行）。
    version 缺省时从 ae_root 路径推导。
    """
    mods = module_hash(ae_root)
    present = [m for m in mods if m.exists]
    if not present:
        return VerifyResult(ok=False, reason="找不到 AfterFX.exe / FLT.dll")

    ver = version or version_of(ae_root)
    base = load_baseline(data_root, ver)
    if not base:
        # ★ 本版本还没有自己的基线 = 首次遇到该版本,直接建档(自适应)。
        #   拒绝只发生在"本版本基线存在但哈希对不上"(=文件真被换过)。
        if trust_first:
            save_baseline(data_root, mods, ver)
            return VerifyResult(ok=True, first_run=True, checked=len(present),
                                reason="首次运行，已记录 %s 基线" % (ver or "当前版本"))
        return VerifyResult(ok=False, reason="没有基线，请先信任当前 AE")

    bad: List[str] = []
    for m in present:
        want = base.get(m.name, "")
        if not want:
            continue
        if want != m.sha256:
            bad.append(m.name)

    if bad:
        return VerifyResult(
            ok=False,
            checked=len(present),
            mismatched=bad,
            reason=("AE 模块已被替换或补丁过：%s。\n"
                    "内存布局可能不同，写入有崩溃风险，已拒绝。\n"
                    "如果这是你主动升级/换版，请在「引擎」页点「重新信任」。") % "、".join(bad),
        )

    return VerifyResult(ok=True, checked=len(present))


def reset_baseline(ae_root: str, data_root: str) -> VerifyResult:
    """重新信任当前 AE（更新该版本的基线）。"""
    mods = module_hash(ae_root)
    present = [m for m in mods if m.exists]
    if not present:
        return VerifyResult(ok=False, reason="找不到 AE 模块")
    save_baseline(data_root, mods, version_of(ae_root))
    return VerifyResult(ok=True, first_run=True, checked=len(present),
                        reason="已重新信任当前 AE（%d 个模块）" % len(present))
