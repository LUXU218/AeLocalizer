"""UTF-8 修复层（AE-UTF8-Fixer）检测与安装状态。

★★ 真机实测结论（2026-10-20，Deep Glow v1.6.6 对照实验）：
   AE 2026 的参数名缓冲改成了 UTF-8，而插件编译时写死的是 GBK 串 ->
   不装 Fixer，Deep Glow 的参数名全部乱码（"半径" 变成 "ͼ"）。

   装上 Fixer 后立刻正常。

   但【我们自己写进 .aex PiPL 的效果名】AE 仍按 GBK 读，不需要 Fixer。

   | 内容 | 来源 | 需要 Fixer |
   |---|---|---|
   | 效果名 | 我们改 .aex（写 GBK） | 否 |
   | 参数名 / 下拉值 | 插件自带 GBK 串 | 是（2026+） |
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

FIXER_NAME = "00_UTF8_Fixer.aex"

#: AE 从哪一版开始需要 Fixer（实测 26.5 需要）
MIN_VERSION_NEEDING_FIXER = 2026


@dataclass
class FixerStatus:
    ae_root: str
    ae_version: str = ""
    installed: bool = False
    path: str = ""
    size: int = 0
    version: str = ""
    needs_fixer: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        """是否处于「该装就装了」的状态。"""
        if not self.needs_fixer:
            return True
        return self.installed


def plugin_dir(ae_root: str) -> str:
    return os.path.join(ae_root, "Support Files", "Plug-ins")


def detect(ae_root: str, ae_version: str = "") -> FixerStatus:
    """检测某个 AE 安装是否装了 Fixer。"""
    st = FixerStatus(ae_root=ae_root, ae_version=ae_version)
    p = os.path.join(plugin_dir(ae_root), FIXER_NAME)
    st.path = p
    st.installed = os.path.exists(p)
    if st.installed:
        try:
            st.size = os.path.getsize(p)
        except OSError:
            pass

    try:
        ver = int(ae_version)
    except (TypeError, ValueError):
        ver = 0
    st.needs_fixer = ver >= MIN_VERSION_NEEDING_FIXER

    if not st.needs_fixer:
        st.note = "AE %s 为 GBK 世界，插件自带的中文可正常显示，无需修复层" % ae_version
    elif st.installed:
        st.note = "已安装 UTF-8 修复层，插件自带的原生中文可正常显示"
    else:
        st.note = (
            "未安装 UTF-8 修复层：插件自带的中文参数名/下拉值会显示为乱码"
            "（如 Deep Glow 的「半径」会变成「ͼ」）"
        )
    return st


def scan_all(installs: List[tuple]) -> List[FixerStatus]:
    """installs: [(version, root)]"""
    return [detect(root, ver) for ver, root in installs]
