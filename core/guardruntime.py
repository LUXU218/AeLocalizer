# -*- coding: utf-8 -*-
"""项目自有 managed guard runtime（阶段1控制面）。

源永远是项目模板 runtime/managed_guard.py，不复制/执行外部共享 guard。
runtime 原子 staging；manifest 包含模板 hash、每个实际文件 hash、协议版本。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

PROTOCOL_VERSION = "aeloc-managed-guard/1"

# 受控运行时部署文件全集（managed_guard.py 部署名为 guard_uni.py）。
# manifest 的整体集合、升级判定与 _source_deps_sha 都以它为准。
_DEPLOY_NAMES = (
    "guard_uni.py",
    "build_profile.py",
    "param_draw_hook.py",
    "popup_session.py",
    "effect_owner.py",
)
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "runtime", "managed_guard.py")


class RuntimeUpgradeRequired(RuntimeError):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def template_path() -> str:
    return TEMPLATE


def runtime_dir(data_root: str) -> str:
    return os.path.join(str(data_root), "guard_runtime")


def runtime_guard(data_root: str) -> str:
    return os.path.join(runtime_dir(data_root), "guard_uni.py")


def _source_deps_sha() -> str:
    d = os.path.dirname(TEMPLATE)
    items = []
    for src_name, deploy_name in (
            ("managed_guard.py", "guard_uni.py"),
            ("build_profile.py", "build_profile.py"),
            ("param_draw_hook.py", "param_draw_hook.py"),
            ("popup_session.py", "popup_session.py"),
            ("effect_owner.py", "effect_owner.py")):
        items.append((deploy_name, _sha256(os.path.join(d, src_name))))
    h = hashlib.sha256()
    for deploy_name, digest in sorted(items):
        h.update(deploy_name.encode("ascii") + b"\0")
        h.update(digest.encode("ascii") + b"\0")
    return h.hexdigest()


def _manifest_ok(rdir: str, old: dict, template_sha: str) -> bool:
    if old.get("protocol_version") != PROTOCOL_VERSION:
        return False
    # fastpath 只接受本项目模板自己的 manifest；来源字段缺失/外部的旧
    # manifest 即使哈希碰巧全对也不得被当成"已验证"。
    if old.get("source") != "project-template":
        return False
    if old.get("template_sha256") != template_sha:
        return False
    if old.get("deps_sha256") != _source_deps_sha():
        return False
    files = old.get("files") or {}
    if not isinstance(files, dict):
        return False
    # 部署文件的完整集合必须正好是这 5 件（managed_guard.py 部署为
    # guard_uni.py）。只校验"列出的文件"会让 partial manifest 走 fastpath，
    # 全新 data_root 随后 import build_profile 失败（reviewer R9）。
    if set(files) != set(_DEPLOY_NAMES):
        return False
    if files.get("guard_uni.py") != template_sha:
        return False
    for name, expected in files.items():
        p = os.path.join(rdir, name)
        if not os.path.isfile(p) or not expected or _sha256(p) != expected:
            return False
    return True


def _manifest_path(data_root: str) -> str:
    return os.path.join(runtime_dir(data_root), "manifest.json")


def can_auto_upgrade(data_root: str) -> bool:
    """旧 manifest 是否由本项目模板部署（仅这种允许一次自动升级）。

    外部共享 guard / 未知来源永远不能自动覆盖：那是用户的安全边界。
    """
    try:
        with open(_manifest_path(data_root), encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return False
    return (isinstance(old, dict)
            and old.get("source") == "project-template"
            and bool(old.get("template_sha256")))


def managed_code_outdated(data_root: str) -> bool:
    """Identify an older, project-owned runtime that needs a graceful restart."""
    try:
        with open(_manifest_path(data_root), encoding="utf-8") as handle:
            installed = json.load(handle)
        return (isinstance(installed, dict)
                and installed.get("source") == "project-template"
                and bool(installed.get("template_sha256"))
                and installed.get("deps_sha256") != _source_deps_sha())
    except (OSError, ValueError):
        return False


def ensure_runtime(data_root: str, source_guard: str = "", *,
                   force: bool = False) -> dict:
    """从项目模板原子部署 managed runtime；不接受外部 guard 作为源。"""
    src = source_guard or TEMPLATE
    if not os.path.isfile(src):
        raise FileNotFoundError("managed runtime 模板不存在")
    if os.path.abspath(src) != os.path.abspath(TEMPLATE):
        raise RuntimeError("managed runtime 只能来自项目模板，拒绝外部 guard 源")
    rdir = runtime_dir(data_root)
    os.makedirs(os.path.dirname(rdir), exist_ok=True)
    mpath = os.path.join(rdir, "manifest.json")
    old = {}
    try:
        with open(mpath, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    tsha = _sha256(TEMPLATE)
    if old and not force:
        if _manifest_ok(rdir, old, tsha):
            return old
        # 只有本项目模板自己的 manifest 允许继续：模板未变时属于"部署损坏/
        # 依赖变化"，可静默原子修复；模板字节变化必须走一次性升级（backend
        # 在 can_auto_upgrade() 为真时以 force=True 再调用一次）。外部共享
        # guard 或来源/模板签名缺失的 manifest 一律拒绝自动覆盖。
        own = (isinstance(old, dict)
               and old.get("source") == "project-template"
               and bool(old.get("template_sha256")))
        if not own:
            raise RuntimeUpgradeRequired("managed runtime 来源未知，拒绝自动覆盖")
        if old.get("template_sha256") != tsha:
            raise RuntimeUpgradeRequired("managed runtime 需要一次性升级初始化")

    # 只原子更新代码文件和 manifest；out/control/tmp（lock/request/audit）
    # 属于运行态，绝不能因部署/修复被删除或目录级替换。
    os.makedirs(rdir, exist_ok=True)
    src_dir = os.path.dirname(TEMPLATE)
    files = {}
    # managed_guard.py 部署名必须是 guard_uni.py；其它受控模块同目录部署。
    for name in ("managed_guard.py", "build_profile.py", "param_draw_hook.py",
                 "popup_session.py", "effect_owner.py"):
        sp = os.path.join(src_dir, name)
        if not os.path.isfile(sp):
            raise FileNotFoundError("managed runtime 依赖缺失: " + name)
        dst_name = "guard_uni.py" if name == "managed_guard.py" else name
        dst = os.path.join(rdir, dst_name)
        tmp = dst + ".tmp"
        with open(sp, "rb") as srcf, open(tmp, "wb") as outf:
            shutil.copyfileobj(srcf, outf)
            outf.flush()
            os.fsync(outf.fileno())
        os.replace(tmp, dst)
        files[dst_name] = _sha256(dst)
    if files.get("guard_uni.py") != tsha:
        raise RuntimeError("managed runtime 模板 hash 校验失败")
    deps_sha = hashlib.sha256()
    for name in sorted(files):
        deps_sha.update(name.encode("ascii") + b"\0" +
                        files[name].encode("ascii") + b"\0")
    manifest = {"schema": 4, "protocol_version": PROTOCOL_VERSION,
                "template_sha256": tsha,
                "deps_sha256": deps_sha.hexdigest(), "files": files,
                "source": "project-template"}
    mt = mpath + ".tmp"
    with open(mt, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(mt, mpath)
    return manifest
