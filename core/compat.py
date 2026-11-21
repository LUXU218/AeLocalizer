"""插件兼容性检测 + 一键停用/恢复。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

#: 已知"未授权会弹模态框"的厂商（实测确认）
LICENSE_VENDORS = {
    "digital anarchy": (
        "Digital Anarchy",
        "未授权时每次添加效果都会弹出序列号对话框，会卡住 AE",
    ),
}

#: ★ 崩溃黑名单（2026-10-21 实测确认）
#:
#: 判定依据（不是猜的）：
#:   崩溃事件日志：
#:     Faulting module: BCCPlus.dll
#:     Fault offset:    0x7A55FD
#:     指令:            48 8B 49 68 = mov rcx, [rcx+0x68]  (读 NULL)
#:   崩溃栈: Radial Tint -> PluginMain -> BCCPlus -> FLT
#:
#:   二分实验：
#:     全量汉化 4 个 BCC DLL               -> 崩溃
#:     只改 Radial Tint 一处               -> 正常
#:     只汉化 871 个安全串                 -> 正常
#:     只汉化 446 个危险串                 -> 正常
#:     全量汉化 + Digital Anarchy 移出     -> 正常  ★
#:
#:   同一份汉化版 DLL，只因 DA 移出就不崩了 -> DA 是触发源。
#:
#: 冲突机理：
#:   DA 未授权时反复调用 BCC 共享 UI 组件；BCC 汉化后字符串表被改，
#:   DA 拿到 NULL 返回值，BCC 里 mov rcx,[rcx+0x68] 读 NULL -> 崩溃。
CRASH_BLACKLIST = {
    "digital anarchy": {
        "vendor": "Digital Anarchy",
        "reason": "未授权时可能弹出序列号对话框",
        "evidence": "崩溃偏移 BCCPlus.dll+0x7A55FD / mov rcx,[rcx+0x68]",
        "level": "block",
    },
}

#: 各厂商的授权文件特征
LICENSE_MARKERS = {
    "digital anarchy": ("license.dat", ".lic", "serial.txt", "authorize.dat"),
}

#: 停用后缀
DISABLED_SUFFIX = ".disabled"


@dataclass
class CompatIssue:
    """一个兼容性问题。"""

    path: str
    vendor: str
    level: str = "warn"      # "block" 会崩溃 / "warn" 可能有问题
    reason: str = ""
    action: str = ""
    #: 是否已停用
    disabled: bool = False


def _find_license(root: str, markers: Tuple[str, ...]) -> bool:
    """在插件目录下找授权文件。"""
    try:
        for cur, dirs, files in os.walk(root):
            depth = cur.count(os.sep) - root.count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            for f in files:
                for m in markers:
                    if f.lower() == m.lower():
                        return True
    except OSError:
        pass
    return False


def check_plugin_dir(media_core: str) -> List[CompatIssue]:
    """扫描插件目录，找出有兼容性风险的插件。"""
    out: List[CompatIssue] = []
    if not os.path.isdir(media_core):
        return out

    for name in sorted(os.listdir(media_core)):
        low = name.lower()

        # ★ 先查崩溃黑名单（比授权检测更严格：命中即报，不看授权）
        crash_key = None
        for key in CRASH_BLACKLIST:
            if key in low:
                crash_key = key
                break

        full = os.path.join(media_core, name)
        is_disabled = low.endswith(DISABLED_SUFFIX)

        if crash_key:
            info = CRASH_BLACKLIST[crash_key]
            out.append(CompatIssue(
                path=full,
                vendor=info["vendor"],
                level="block",
                reason="%s：%s" % (info["vendor"], info["reason"]),
                action=("已停用（安全）" if is_disabled
                        else "建议停用（未授权；历史上曾与汉化版 BCC 冲突）"),
                disabled=is_disabled,
            ))
            continue

        # 其次查未授权弹框
        matched = None
        for key in LICENSE_VENDORS:
            if key in low:
                matched = key
                break
        if not matched:
            continue

        vendor, desc = LICENSE_VENDORS[matched]

        markers = LICENSE_MARKERS.get(matched, ())
        has_lic = _find_license(full, markers) if markers else False
        if has_lic:
            continue

        out.append(CompatIssue(
            path=full,
            vendor=vendor,
            level="block",
            reason="%s：%s" % (vendor, desc),
            action=("已停用" if is_disabled
                    else "购买授权，或点「停用」把它移出加载列表"),
            disabled=is_disabled,
        ))

    return out


def summarize(issues: List[CompatIssue]) -> str:
    """一句话概括。"""
    if not issues:
        return "没有发现兼容性问题"
    active = [i for i in issues if not i.disabled]
    if active:
        vendors = sorted({i.vendor for i in active})
        names = "、".join(vendors)
        # 区分崩溃级
        if any(i.level == "block" for i in active):
            return ("%s 未授权，建议停用" % names)
        return "发现 %d 个插件需要授权（%s），未授权时会弹框卡住 AE" % (
            len(active), names)
    return "有 %d 个冲突插件已停用" % len(issues)


def disable(issue: CompatIssue) -> Tuple[bool, str]:
    """停用一个插件目录。

    ★ 2026-10-21 实测修正：
      只加 .disabled 后缀【没用】—— AE 仍会扫描 MediaCore 下的所有子目录，
      连 "Samurai 1.2.1.disabled" 这种都会被加载（日志里能看到）。
      所以改成【移动到 MediaCore.disabled/】，彻底移出扫描范围。

      优先搬到 MediaCore.disabled；搬不动（跨盘/权限）再退回加后缀。
    """
    if issue.disabled:
        return True, "已经是停用状态"
    src = issue.path
    if not os.path.isdir(src):
        return False, "目录不存在"

    # 方案 A：移到 MediaCore.disabled/
    parent = os.path.dirname(src)                     # .../MediaCore
    dst_root = parent + DISABLED_SUFFIX               # .../MediaCore.disabled
    name = os.path.basename(src).replace(DISABLED_SUFFIX, "")
    dst = os.path.join(dst_root, name)

    try:
        os.makedirs(dst_root, exist_ok=True)
        if os.path.isdir(dst):
            # 已存在同名 -> 先清掉旧的
            import shutil

            shutil.rmtree(dst, ignore_errors=True)
        os.rename(src, dst)
        return True, "已移出 MediaCore: %s" % os.path.basename(dst)
    except PermissionError:
        return False, "需要管理员权限"
    except OSError:
        pass

    # 方案 B：退回加后缀
    dst2 = src + DISABLED_SUFFIX
    if os.path.exists(dst2):
        return False, "目标已存在: %s" % os.path.basename(dst2)
    try:
        os.rename(src, dst2)
        return True, "已停用（加后缀）: %s" % os.path.basename(dst2)
    except PermissionError:
        return False, "需要管理员权限"
    except OSError as exc:
        return False, str(exc)


def enable(issue: CompatIssue) -> Tuple[bool, str]:
    """恢复一个已停用的插件目录。"""
    src = issue.path
    if not src.lower().endswith(DISABLED_SUFFIX):
        return True, "本来就是启用状态"
    dst = src[:-len(DISABLED_SUFFIX)]
    try:
        os.rename(src, dst)
        return True, "已恢复: %s" % os.path.basename(dst)
    except PermissionError:
        return False, "需要管理员权限"
    except OSError as exc:
        return False, str(exc)
