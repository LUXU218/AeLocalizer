"""通用后台任务线程(2026-11-01 从 app/backend.py 抽出,backend 与
backend_parts 共用)。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    """通用后台任务：跑一个函数，回来发结果/错误。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # 兜底：任何异常都不能让 UI 崩
            import traceback

            self.failed.emit("%s\n%s" % (exc, traceback.format_exc(limit=3)))
