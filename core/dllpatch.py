"""BCC DLL 还原(2026-10-29 起 dllpatch 只剩这一件事)。

历史:本模块曾负责「DLL 静态汉化」—— 扫描 Continuum_AE_*.dll /
BCCPlus.dll / CC 系 .aex 的 .rdata,把词典命中的英文名写成 GBK 中文
(plan_dll / apply_dll / 质量闸,约 700 行)。

2026-10-29 起:
  · BCC 参数名/下拉值改走【引擎内存实时汉化】(守护 guard_uni.py 的
    bccScanMemImpl:按 AE 版本选 GBK/UTF-8,磁盘零改动,AE 关闭即回英文,
    交接文档 §5.27)—— 文件写入路径整层移除;
  · 磁盘上的三块 Continuum DLL 已还原为英文原版,以后不再改写;
  · CC 系标签类内容曾走「通用二进制标签写入」通道,该通道连同界面
    2026-11-15 一并删除。

因此本模块只保留「把历史上被改过的 DLL 拉回英文原版」:
  restore_dll(path, backup_root) —— 原厂备份优先,且拒绝拿含中文的
  文件当还原源(2026-10-22 实翻车:dll_backup 里的"备份"其实是汉化版)。
"""
from __future__ import annotations

import os
import shutil

#: 已知的【真正原版】DLL 位置(优先级高于 dll_backup)。
#:
#: ★★ 2026-10-22 踩过的坑:
#:   dll_backup 里的 BCCPlus.dll 其实是【汉化后】的副本,
#:   用它「还原」等于把汉化版又写回去 —— BCC+ 系列当场报
#:   「无法调用插件 (25::3)」,AE 添加 BCC+ 效果直接崩。
#:   所以还原必须先找这些原厂备份,并且【验证里面没有中文】。
KNOWN_ORIGINALS = {
    "BCCPlus.dll": [
        r"C:\ProgramData\AeCatCN\bccplus_backup\BCCPlus.dll",
    ],
    "Continuum_AE_8Bit.dll": [
        r"C:\ProgramData\AeCatCN\bcc_dll_backup_full\Continuum_AE_8Bit.dll",
    ],
    "Continuum_AE_16Bit.dll": [
        r"C:\ProgramData\AeCatCN\bcc_dll_backup_full\Continuum_AE_16Bit.dll",
    ],
    "Continuum_AE_Float.dll": [
        r"C:\ProgramData\AeCatCN\bcc_dll_backup_full\Continuum_AE_Float.dll",
    ],
}

#: 判断"看起来是中文"的字节特征(GBK 双字节)
_ZH_PROBES = [z.encode("gbk") for z in ("灯光", "蒙版", "偏移", "强度", "颜色")]

#: 备份目录(与 engine.DATA_ROOT 保持一致)
DEFAULT_BACKUP_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "AeLocalizer", "dll_backup")


def _looks_localized(data: bytes) -> bool:
    """文件里是否含中文(说明是汉化版,不能当还原源)。"""
    return any(z in data for z in _ZH_PROBES)


def _pick_original(name: str, backup_root: str) -> tuple:
    """挑一个可用的【原版】备份。返回 (路径, 说明) 或 ("", 原因)。"""
    # ① 优先已知原厂备份
    for cand in KNOWN_ORIGINALS.get(name, []):
        if not os.path.exists(cand):
            continue
        try:
            data = open(cand, "rb").read()
        except OSError:
            continue
        if _looks_localized(data):
            continue
        return cand, "原厂备份"
    # ② 退回 dll_backup,但必须验证不含中文
    bak = os.path.join(backup_root, name)
    if os.path.exists(bak):
        try:
            data = open(bak, "rb").read()
        except OSError:
            data = b""
        if data and not _looks_localized(data):
            return bak, "本地备份"
        return "", "备份文件本身已含中文（是汉化版），不能用来还原"
    return "", "找不到原版备份"


def restore_dll(path: str, backup_root: str) -> tuple:
    """从【原版】备份还原。

    ★ 绝不拿汉化版当还原源 —— 见 KNOWN_ORIGINALS 的说明。
    返回 (成功, 消息)。
    """
    name = os.path.basename(path)
    src, why = _pick_original(name, backup_root)
    if not src:
        return False, why
    try:
        shutil.copy2(src, path)
        return True, "已还原（%s）" % why
    except OSError as exc:
        return False, "还原失败: %s" % exc
