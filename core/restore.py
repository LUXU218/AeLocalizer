"""分层还原：让用户选择还原哪一部分汉化。

★ 为什么要分层（实测教训）：
  汉化分四层，每层改的东西不同，还原方式也不同：

    L1 效果名   -> 改 .aex 的 PiPL（磁盘）  -> 用备份/matchName 还原
    L2 参数名   -> 引擎运行时改内存        -> 停引擎 + 清词典
    (L3 DLL 层已删:2026-10-29 起 BCC 走引擎内存实时汉化,磁盘是英文原版)
    L4 编码修复 -> 装 00_UTF8_Fixer.aex     -> 删除该文件

  用户可能只想还原其中一层（比如只想要回英文效果名，但保留中文参数名）。
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "AeLocalizer")


@dataclass
class RestoreLayer:
    """一个可还原的层。"""

    key: str
    label: str
    desc: str
    #: 是否有可用的还原依据
    available: bool = False
    #: 状态说明
    status: str = ""


def _count_files(root: str, ext: str = "") -> int:
    n = 0
    if not os.path.isdir(root):
        return 0
    for _cur, _d, files in os.walk(root):
        for f in files:
            if ext and not f.lower().endswith(ext):
                continue
            n += 1
    return n


def list_layers(ae_root: str = "") -> List[RestoreLayer]:
    """列出所有可还原层及其状态。"""
    out: List[RestoreLayer] = []

    # L1 效果名 + 分类名
    aex_bak = os.path.join(DATA, "aex_name_backup")
    n_aex = _count_files(aex_bak, ".aex")
    out.append(RestoreLayer(
        key="effect_names",
        label="效果名 + 分类名（插件菜单）",
        desc="把 S_通道切换 / Sapphire 调整 这类改回英文",
        available=True,        # 有备份 或 可用 matchName 重建
        status=("有 %d 个备份可直接还原；分类名按厂商表翻译" % n_aex) if n_aex
               else "无备份，用 matchName 重建效果名；分类名按厂商表翻译",
    ))

    # L2 参数名
    try:
        from core.engine import engine_running, ENGINE_DICT_DIR
        from core.enginedict import DICT_FILE, DICT_DAT, ENUM_DAT

        files = [f for f in (DICT_FILE, DICT_DAT, ENUM_DAT)
                 if os.path.exists(os.path.join(ENGINE_DICT_DIR, f))]
        running = engine_running()
        out.append(RestoreLayer(
            key="param_names",
            label="参数名 + 下拉值（效果控件里的名字）",
            desc="把 强度、中心 XY、Reflect 这类改回英文",
            available=True,
            status=("引擎运行中，需先停止；词典 %d 个文件" % len(files))
                   if running else
                   ("词典 %d 个文件待清理" % len(files)) if files
                   else "引擎词典已清空",
        ))
    except Exception as exc:
        out.append(RestoreLayer(key="param_names", label="参数名 + 下拉值",
                                desc="", status="检测失败: %s" % exc))

    # L4 编码修复
    #
    # ★★ 高危项（2026-10-21 实测教训）：
    #    AE-UTF8-Fixer 用 MinHook 挂钩 AE 的字符串处理，
    #    在运行时把插件的 GBK 中文转成 UTF-8。
    #    【它是 BCC / BCC+ 等 DLL 层汉化能在 AE 2026 显示中文的唯一前提】
    #    一旦移除，所有已汉化的 DLL 字符串会立刻变成乱码（Ч / Ӧ / ƫ）。
    fixer = ""
    if ae_root:
        fixer = os.path.join(ae_root, "Support Files", "Plug-ins",
                             "00_UTF8_Fixer.aex")
    has_fixer = bool(fixer) and os.path.exists(fixer)
    out.append(RestoreLayer(
        key="utf8_fixer",
        label="UTF-8 修复层（⚠ 移除会导致 DLL 层汉化变乱码）",
        desc=("移除 00_UTF8_Fixer.aex。"
              "★ 它是 DLL 层汉化的前提：BCC/BCC+ 的中文靠它在运行时"
              "从 GBK 转成 UTF-8，移除后所有参数名会变成乱码"),
        available=has_fixer,
        status=("已安装（DLL 层汉化正常）" if has_fixer
                else "未安装（DLL 层汉化会显示乱码）"),
    ))

    return out


def restore_effect_names(dry: bool = False,
                        ae_root: str = "") -> Tuple[int, str]:
    """还原效果名 + 分类名。

    ae_root: 目标 AE 安装根目录。留空自动探测。
             ★ 2026-10-22：加这个参数，让「还原」跟着当前选的 AE 版本走。

    ★ 2026-10-21 补充：之前只还原 name（效果名），漏了 category（分类名），
      用户看到 AE 菜单里还是 "Sapphire 调整" 这种中文。
      现在两样一起还原。
    """
    total, msg = _restore_names_only(dry, ae_root)
    try:
        from core.restorecat import restore_categories

        cn, cmsg = restore_categories(dry, ae_root)
        total += cn
        msg += "；" + cmsg
    except Exception as exc:
        msg += "；分类名还原失败：%s" % str(exc)[:40]
    return total, msg


def _match_name_looks_display(mn: str) -> bool:
    """判断一个 matchName 是否【看起来像显示名】，而不是内部 ID。

    ★ 2026-10-22 实测（BCC 的 matchName 分两类）：
        像显示名：BCC Blobs Wipe / BCC Beat Reactor / BCC Alpha Process
        是内部ID：BCC3Composite / BCC6ThreeWayCC / BCC_AMBIENTLIGHT
                  BCC33D Image Shatter / BCC_AMBIENTLIGHT
      内部 ID 的特征：全大写+下划线、带 "BCC<数字>" 前缀、没有空格。
    """
    import re

    s = (mn or "").strip().rstrip("\x00").strip()
    if len(s) < 4:
        return False
    # 尾部混进半个 NUL / 'M'（BCC 有 52 个文件被写坏过）
    if "\x00" in s or s.endswith("M"):
        return False
    # 全大写 + 下划线 -> 内部常量
    if "_" in s and s == s.upper() and " " not in s:
        return False
    # BCC3 / BCC6 / BCC33D 这种带版本数字的内部 ID
    if re.match(r"^[A-Za-z]{2,}\d", s):
        return False
    # 显示名几乎都含空格；但 Sapphire 例外 —— 它原名就是 S_Glow 这种
    # （带 S_ 前缀、含小写字母），必须放行，否则 287 个 Sapphire 都还原不了。
    if " " not in s:
        if "_" in s and any(c.islower() for c in s):
            return True
        return False
    return True


#: 判断一个 aex 的 PiPL name 是不是纯英文（可当还原源）
def _name_is_english(path: str) -> bool:
    import re as _re

    from .pipl import parse_pipl

    _cjk = _re.compile(r"[\u4e00-\u9fff]")
    try:
        d = open(path, "rb").read()
    except OSError:
        return False
    for s in parse_pipl(d):
        if s.key != "name":
            continue
        raw = d[s.offset:s.offset + s.length]
        try:
            t = raw.rstrip(b"\x00 ").decode("gbk")
        except UnicodeDecodeError:
            return True
        return not bool(_cjk.search(t))
    return False


def _backup_roots_oldest_first() -> list:
    """所有备份根，从旧到新。

    实测安装的 481 个 BCC AEX 在 aex_name_backup 中没有副本，
    而最早一轮应用备份含 474 个英文原件。扫描所有应用前的历史备份，
    还原时优先使用英文原件。
    """
    roots = []
    p = os.path.join(DATA, "aex_name_backup")
    if os.path.isdir(p):
        roots.append(p)
    bk = os.path.join(DATA, "backup")
    if os.path.isdir(bk):
        try:
            names = sorted(n for n in os.listdir(bk)
                           if os.path.isdir(os.path.join(bk, n)))
        except OSError:
            names = []
        for n in names:
            d = os.path.join(bk, n, "files")
            if os.path.isdir(d):
                roots.append(d)
    return roots


def _escape_rel(rel: str) -> str:
    """把 "C:\\Program Files\\x.aex" 转成 "C__Program Files\\x.aex"。"""
    r = rel.replace("/", "\\")
    if len(r) > 2 and r[1] == ":":
        drive = r[0]
        rest = r[2:].lstrip("\\")
        return drive + "__" + rest
    return ""


def _restore_names_only(dry: bool = False,
                        ae_root: str = "") -> Tuple[int, str]:
    """还原效果名（name 字段）。

    ae_root: 目标 AE 安装根目录。留空则自动探测（取最新安装）。
             ★ 2026-10-22：原来写死 "Adobe After Effects 2026"，
               在 AE 2025 上点「还原效果名」会去改 2026 的目录，
               2025 自己的中文名反而留着 —— 版本切换时行为完全错位。
    """
    from core.pipl import parse_pipl

    MC = r"C:\Program Files\Adobe\Common\Plug-ins\7.0\MediaCore"
    AE_ROOT = ae_root or _detect_ae_root()
    BAK = os.path.join(DATA, "aex_name_backup")

    restored = rebuilt = fail = 0

    # ① 从备份还原
    #
    # ★★ 2026-10-22 重写：原来只从 aex_name_backup 还原，而那个目录里
    #    【一个 BCC 文件都没有】。BCC 共 481 个 aex，用户点「执行还原」后
    #    只有 156 个变回英文，剩下 292 个还是中文（"镜置换贴" 这种）。
    #
    #    现在改成：扫【所有】备份源（aex_name_backup + backup/*/files），
    #    对每个目标 aex 优先挑一份【PiPL name 是纯英文】的原件覆盖回去。
    #    最早那次备份（20261020-165232_apply）里有 474 个纯英文 BCC 原件。
    roots = _backup_roots_oldest_first()

    def _cand_paths(real_path: str):
        """给真实路径生成各种备份根下的相对路径候选。"""
        out = []
        try:
            rel = os.path.relpath(real_path, "C:\\")
        except ValueError:
            return out
        # backup/*/files/ 的结构：Program Files\...
        out.append(rel)
        # aex_name_backup 的结构：C__Program Files\...
        esc = _escape_rel(real_path)
        if esc:
            out.append(esc)
        # aex_name_backup 的 _s/files/... 结构
        out.append(os.path.join("_s", "files", rel))
        return out

    targets = []
    for root in (MC, os.path.join(AE_ROOT, "Support Files", "Plug-ins")):
        if not os.path.isdir(root):
            continue
        for cur, _d, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".aex"):
                    continue
                p = os.path.join(cur, f)
                cands = _cand_paths(p)
                src = ""
                for r in roots:
                    for c in cands:
                        q = os.path.join(r, c)
                        if os.path.exists(q) and _name_is_english(q):
                            src = q
                            break
                    if src:
                        break
                if not src:
                    continue
                if dry:
                    restored += 1
                    continue
                try:
                    shutil.copy2(src, p)
                    restored += 1
                except (PermissionError, OSError):
                    fail += 1

    # ② matchName 重建（备份没覆盖的）
    import re
    CJK = re.compile(r"[\u4e00-\u9fff]")
    # ★★ 2026-10-22 修正：原来硬编码几个目录，漏了 "Red Giant Universe" ——
    #    用户点「还原效果名」后 BCC/Sapphire 都变回英文，但红巨星的
    #    105 个 aex 仍显示 "uni.HUD 组件" 这类中文。
    #    改成直接扫整个 MediaCore + AE 自带 Plug-ins，以后新厂商也不会漏。
    for root in (MC, os.path.join(AE_ROOT, "Support Files", "Plug-ins")):
        if not os.path.isdir(root):
            continue
        for cur, _d, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".aex"):
                    continue
                p = os.path.join(cur, f)
                try:
                    d = bytearray(open(p, "rb").read())
                except OSError:
                    continue
                ent = mn = None
                for s in parse_pipl(bytes(d)):
                    if s.key == "name":
                        ent = s
                    elif s.key == "matchName":
                        mn = s
                if ent is None or mn is None:
                    continue
                cur_s = bytes(d[ent.offset:ent.offset + ent.length])
                try:
                    txt = cur_s.decode("gbk")
                except UnicodeDecodeError:
                    continue
                if not CJK.search(txt):
                    continue
                eng = bytes(d[mn.offset:mn.offset + mn.length])
                try:
                    eng_s = eng.decode("ascii")
                except UnicodeDecodeError:
                    continue
                # ★★ 2026-10-22：matchName 必须先过"像不像显示名"这一关。
                #   实测 BCC 的 matchName 大量是内部 ID：
                #     BCC3Composite / BCC6ThreeWayCC / BCC_AMBIENTLIGHT / BCC33D Image Shatter
                #   直接拿它当效果名，用户会看到一堆 BCC3Composite 这种鬼东西，
                #   比中文还难认。只有 BCC Blobs Wipe / BCC Beat Reactor
                #   这种才是真正的显示名。
                if not _match_name_looks_display(eng_s):
                    continue
                eb = eng_s.encode("ascii", "ignore")
                if not eb or len(eb) > ent.length:
                    continue
                if dry:
                    rebuilt += 1
                    continue
                try:
                    d[ent.offset:ent.offset + ent.length] = (
                        eb + b"\x00" * (ent.length - len(eb)))
                    with open(p, "wb") as fp:
                        fp.write(d)
                    rebuilt += 1
                except (PermissionError, OSError):
                    fail += 1

    # ★ 2026-11-15：CC 乱码串修复通道已随「CC 系列乱码修复」卡一起删除。
    #   原因：该卡的根因是 AE2026 按 UTF-8 读插件自带 GBK 名，现在由
    #   UTF-8 修复层（00_UTF8_Fixer.aex）在运行时正确解码覆盖；CC 名不再
    #   需要二次改写。整包还原后仍建议确认修复层在位。

    # ③ 从 aex 内部残留的英文名还原（Red Giant Universe 专用）
    #
    # ★★ 2026-10-22 实测：Universe 的 matchName 是内部文件名
    #    （Universe_Blur_Blur_Premium_AE_Fx，26 字节），比 name 槽位（8 字节）
    #    长得多，matchName 重建对它们完全无效 —— 104 个全卡在 "uni.模糊"。
    #    而 aex 内部还留着英文原名 "uni.Blur"，可以用它还原。
    from core.aexname import restore_dir as _restore_from_aex

    rg_restored = 0
    for sub in ("Red Giant Universe",):
        d = os.path.join(MC, sub)
        if os.path.isdir(d):
            rg_restored += _restore_from_aex(d, dry_run=dry)

    msg = "备份还原 %d 个，matchName 重建 %d 个" % (restored, rebuilt)
    if rg_restored:
        msg += "，Red Giant 内部名还原 %d 个" % rg_restored
    if fail:
        msg += "，失败 %d 个（可能被占用）" % fail
    return (restored + rebuilt + rg_restored), msg


def restore_param_names(dry: bool = False) -> Tuple[int, str]:
    """还原参数名：停引擎 + 清空引擎词典。"""
    try:
        from core.engine import stop_engine, engine_running, ENGINE_DICT_DIR
        from core.enginedict import clear_engine_dict
    except Exception as exc:
        return 0, "模块加载失败: %s" % exc

    if dry:
        return 1, "将停止引擎并清空词典"

    ok, msg = stop_engine(timeout=20)
    cleared = clear_engine_dict(ENGINE_DICT_DIR)
    return (1 if cleared else 0), ("%s；词典已清空" % msg if cleared
                                   else "%s；词典清理失败" % msg)


def _detect_ae_root() -> str:
    """自动探测 AE 安装根目录（不再硬编码 2026）。"""
    try:
        from core.inventory import find_ae_installs

        inst = find_ae_installs()
        if inst:
            return inst[0][1]
    except Exception:
        pass
    base = r"C:\Program Files\Adobe"
    if os.path.isdir(base):
        cands = sorted(
            (d for d in os.listdir(base)
             if d.lower().startswith("adobe after effects")),
            reverse=True)
        for d in cands:
            p = os.path.join(base, d)
            if os.path.isdir(p):
                return p
    return ""


def restore_utf8_fixer(ae_root: str, dry: bool = False) -> Tuple[int, str]:
    """移除 UTF-8 修复层。"""
    try:
        from core.engine import remove_fixer
    except Exception as exc:
        return 0, "模块加载失败: %s" % exc
    if dry:
        return 1, "将移除 Fixer"
    ok, msg = remove_fixer(ae_root)
    return (1 if ok else 0), msg


RESTORERS: Dict[str, Callable] = {
    "effect_names": restore_effect_names,
    "param_names": restore_param_names,
}
