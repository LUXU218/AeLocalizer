"""预检与效果名映射。

★ 2026-11-15：静态写盘整层已删除（改写 .aex 的 PiPL 名字）。
  汉化效果名现在只走运行时 hook（managed runtime 的 DisplayHook），
  不再需要关闭 AE、不需要管理员、也不写 Program Files。
  随之删除：apply_effects / rollback / run_flow / aexwrite / namecheck /
  tools/flow_cli.py，以及命令行那条「扫描→写盘→回滚」的链路。

  保留：
    · preflight()            —— 启动引擎前的检查清单（引擎卡在用）
    · build_effect_mapping() —— 运行时通道的 {英文: 译名} 映射（仍在用）
    · is_admin()             —— 供外部脚本查询当前权限（运行时通道不依赖它）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

from .dictstore import DictStore
from .encoder import WORLD_UTF8
from .inventory import Inventory


# ---------------------------------------------------------------- 预检


@dataclass
class CheckItem:
    ok: bool
    label: str
    detail: str = ""
    warning: bool = False


@dataclass
class PreflightReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i.ok or i.warning for i in self.items)

    @property
    def blockers(self) -> List[CheckItem]:
        return [i for i in self.items if not i.ok and not i.warning]

    def summary(self) -> str:
        bad = len(self.blockers)
        warn = sum(1 for i in self.items if i.warning)
        if bad:
            return "预检失败：%d 项阻塞" % bad
        return "预检通过（%d 项警告）" % warn if warn else "预检通过"


def _is_admin() -> bool:
    """是否以管理员身份运行。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_admin() -> bool:
    """公开版：给界面层判断「效果名这一层能不能写」。"""
    return _is_admin()


def preflight(
    inv: Inventory,
    store: DictStore,
) -> PreflightReport:
    """应用前的检查清单 —— 让用户"敢按按钮"。"""
    rep = PreflightReport()
    selected = inv.selected()

    rep.items.append(
        CheckItem(
            ok=bool(selected),
            label="已勾选插件",
            detail="%d 组 / %d 个文件" % (len(selected), sum(g.file_count for g in selected)),
        )
    )

    res = store.resolve()
    rep.items.append(
        CheckItem(ok=bool(res), label="词典条目", detail="%d 条生效" % len(res))
    )

    # ★ UTF-8 修复层（真机实测：2026+ 插件自带中文必须靠它）
    try:
        from .fixer import detect as _detect_fixer

        fs = _detect_fixer(inv.ae_root, inv.ae_version)
        rep.items.append(
            CheckItem(
                ok=fs.ok,
                label="UTF-8 修复层",
                detail=fs.note,
                warning=(fs.needs_fixer and not fs.installed),
            )
        )
    except Exception as exc:
        rep.items.append(
            CheckItem(ok=True, label="UTF-8 修复层", detail="检测失败：%s" % exc, warning=True)
        )

    # ★ 未授权插件冲突检测（2026-10-21 崩溃根因）
    #   实测：Digital Anarchy 未授权时会反复调用 BCC 的共享 UI 组件。
    #   BCC 汉化后字符串表被改，它拿到异常返回值 -> BCCPlus.dll 里
    #   mov rcx,[rcx+0x68] 读 NULL -> ACCESS_VIOLATION，AE 直接崩。
    #   所以这是【阻断项】：有未授权插件还开着，就不许应用汉化。
    try:
        from .compat import check_plugin_dir as _check_compat

        media_core = r"C:\Program Files\Adobe\Common\Plug-ins\7.0\MediaCore"
        issues = _check_compat(media_core)
        active = [i for i in issues if not i.disabled]
        if active:
            vendors = "、".join(sorted({i.vendor for i in active}))
            rep.items.append(
                CheckItem(
                    ok=False,
                    label="未授权插件冲突",
                    detail=("%s 未授权，会与汉化后的 BCC 冲突并导致 AE 崩溃。"
                            "在引擎卡点该插件旁的「停用」，或购买授权" % vendors),
                )
            )
        elif issues:
            rep.items.append(
                CheckItem(
                    ok=True,
                    label="未授权插件冲突",
                    detail="%d 个需授权插件已停用，不会冲突" % len(issues),
                )
            )
        else:
            rep.items.append(
                CheckItem(ok=True, label="未授权插件冲突", detail="没有冲突插件")
            )
    except Exception as exc:
        rep.items.append(
            CheckItem(ok=True, label="未授权插件冲突",
                      detail="检测失败：%s" % exc, warning=True)
        )

    # ★ AE 模块哈希钉死（2026-10-21）
    #   补丁版/精简版的 AE，内存布局和函数签名都变了，写内存会崩。
    try:
        from .modhash import verify as _verify_mods

        vr = _verify_mods(inv.ae_root, os.path.join(os.path.expanduser("~"),
                                                    "AppData", "Local", "AeLocalizer"))
        rep.items.append(
            CheckItem(
                ok=vr.ok,
                label="AE 模块校验",
                detail=(vr.reason if not vr.ok else
                        ("首次运行，已记录基线（%d 个模块）" % vr.checked
                         if vr.first_run else "AfterFX.exe / FLT.dll 未被替换")),
                warning=not vr.ok,
            )
        )
    except Exception as exc:
        rep.items.append(
            CheckItem(ok=True, label="AE 模块校验", detail="检测失败：%s" % exc, warning=True)
        )

    return rep


# ---------------------------------------------------------------- 效果名映射

def build_effect_mapping(store: DictStore, world: str = WORLD_UTF8) -> Dict[str, str]:
    """整理 {英文: 译名}，供 .aex 改写用。

    ★ 这里**不做长度裁剪**。原因：映射的键可能是去前缀的短名
      （"ClampChroma" 11 字节），而真实槽位属于完整名（"S_ClampChroma"
      13 字节）。预裁剪会用错基准，把「色度限制」砍成「色度」。
      缩短推迟到 plan_file —— 那里拿得到真实槽位长度。

    ★★ 2026-10-21 重大修正：
      之前只用词典，导致 Sapphire 287 个效果只汉化了 54 个 ——
      因为词典里没有 S_ 开头的条目，而 data/effect_names.json
      有 296 条（键是去掉 S_ 前缀的 "Blur"/"Beauty"）。
      现在自动合并效果名表，并对每个键额外生成「加 S_ 前缀」的变体，
      让 plan_file 用完整名也能命中。
    """
    mapping = {key: r.zh for key, r in store.resolve().items() if r.zh}

    # 自动加载仓库自带的效果名表（若存在）
    try:
        import json as _json
        import os as _os

        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        tbl_path = _os.path.join(root, "data", "effect_names.json")
        if _os.path.exists(tbl_path):
            with open(tbl_path, encoding="utf-8") as f:
                tbl = _json.load(f)
            if isinstance(tbl, dict):
                for k, v in tbl.items():
                    if not isinstance(k, str) or not isinstance(v, str) or not v:
                        continue
                    # 原样
                    if k not in mapping:
                        mapping[k] = v
                    # ★★ Sapphire 形态（2026-10-21 修正）：
                    #   文件里是 S_XXX，表里键是 XXX、值是纯中文。
                    #   写进去必须是「S_ + 中文」，否则会丢掉 S_ 前缀，
                    #   菜单里 287 个效果全变成裸中文（实测踩过）。
                    full = "S_" + k
                    if full not in mapping:
                        mapping[full] = "S_" + v
    except Exception:
        pass

    return mapping


