# -*- coding: utf-8 -*-
"""AE 启动期「挡路对话框」处理（core.aelaunch 的薄壳）。

★★ 2026-10-30 重要修复：这个文件【曾经丢失】，而 core/aelaunch.py 里
   launch_ae() 是这样用它的：

       try:
           import ae_dialog as _dlg
           ...  while ...: _dlg.handle_once()  # 启动期边等边点框
       except Exception:
           pass

   ImportError 被那个 except 吞掉 -> 「启动时自动点掉 Mocha 许可证框 /
   崩溃修复框」这条路径**一直是死的**。AE 一被对话框挡住，launch_ae 只能
   干等到 30 秒超时，用户看到的就是「每次进入 AE 都要很久」「AE 打不开」。
   现在补回本模块，并把实现统一到 core.aelaunch（避免两套逻辑各自漂移）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import core.aelaunch as _al  # noqa: E402

# ★ 注意：这里【故意】每次现取 _al.dismiss_dialogs，而不是 from ... import。
#   直接用 from-import 会把函数对象绑死在导入那一刻 —— 之后 core.aelaunch
#   里改了实现、或测试里 monkeypatch，这个壳都还指着旧函数（已踩过）。


def handle_once(verbose: bool = False) -> int:
    """处理当前所有挡路对话框，返回处理数（launch_ae 每 0.8 秒调一次）。"""
    return _al.dismiss_dialogs(verbose=verbose)


def main_window():
    """当前 AE 主窗口 (hwnd, title)，没有则 None。"""
    return _al.main_window()


def list_dialogs():
    """当前 AE 对话框清单（排查用）。"""
    return _al.find_dialogs()


if __name__ == "__main__":
    n = handle_once(verbose=True)
    print("处理了 %d 个对话框" % n)
