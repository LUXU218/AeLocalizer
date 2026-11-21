# -*- coding: utf-8 -*-
"""AE 对话框处理回归测试（2026-10-30 用户报「多添加几个效果就跳报错」）。

背景（真机实测结论，见 docs/兼容性-AE2021-2023.md §7）：
  · AE 的提示框（例如 Boris Continuum 转场变体的「全局出点标记不匹配」）
    是 Adobe DirectUI 自绘的 #32770：EnumChildWindows 读不到按钮文字，
    而且【完全不吃合成按键】（WM_KEYDOWN / keybd_event / BM_CLICK 都无效）。
  · 旧 dismiss_dialogs() 只在找不到按钮时发一个 VK_RETURN，所以对这类框
    从来关不掉 —— 用户看到的就是「AE 卡在那儿 / 报错框一直在」。
  · 新逻辑：先发回车，再用真鼠标点窗口右下角按钮区（单按钮框 = 确定；
    三按钮保存框 = 最右边的「取消」，取消只取消关闭、不会丢数据）。

另外覆盖 tools/ae_dialog.py：这个模块曾经丢失，导致 core/aelaunch.py 里
  launch_ae() 的启动期「边等边点框」整条路径被 except 静默吞掉。
"""
import ctypes
import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import aelaunch  # noqa: E402


class _FakeUser32:
    """只实现 _click_modal 用到的那几个 API，坐标固定：窗口 600x220 @ (100,100)。"""

    def __init__(self):
        self.set_cursor_pos = []
        self.mouse_events = []

    def GetWindowRect(self, hwnd, ref):
        r = ref._obj
        r.left, r.top, r.right, r.bottom = 100, 100, 700, 320
        return True

    def GetCursorPos(self, ref):
        p = ref._obj
        p.x, p.y = 10, 20
        return True

    def ShowWindow(self, *a):
        return True

    def BringWindowToTop(self, *a):
        return True

    def SetForegroundWindow(self, *a):
        return True

    def SetCursorPos(self, x, y):
        self.set_cursor_pos.append((x, y))
        return True

    def mouse_event(self, flags, dx, dy, data, extra):
        self.mouse_events.append(flags)
        return None


class TestClickModal(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeUser32()
        self._old = aelaunch.user32
        aelaunch.user32 = self.fake

    def tearDown(self):
        aelaunch.user32 = self._old

    def test_clicks_bottom_right_button(self):
        """点的是窗口右下角 (right-60, bottom-28)，并且是左键按下+抬起。"""
        self.assertTrue(aelaunch._click_modal(0x1234))
        self.assertIn((640, 292), self.fake.set_cursor_pos)
        self.assertEqual(self.fake.mouse_events, [0x0002, 0x0004])

    def test_restores_cursor(self):
        """点完把鼠标还回原位，不能抢用户的指针。"""
        aelaunch._click_modal(0x1234)
        self.assertEqual(self.fake.set_cursor_pos[-1], (10, 20))


class TestDismissSelfDrawn(unittest.TestCase):
    """自绘框（buttons 为空）必须走鼠标兜底，不能只发回车。"""

    def test_fallback_clicks_modal(self):
        dialogs = [{"hwnd": 0x999, "class": "#32770",
                    "title": "After Effects", "buttons": []}]
        clicked = []
        keys = []
        old = (aelaunch.find_dialogs, aelaunch._click_modal, aelaunch._send_key)
        aelaunch.find_dialogs = lambda: dialogs
        aelaunch._click_modal = lambda h: clicked.append(h) or True
        aelaunch._send_key = lambda h, vk: keys.append((h, vk)) or True
        try:
            n = aelaunch.dismiss_dialogs()
        finally:
            aelaunch.find_dialogs, aelaunch._click_modal, aelaunch._send_key = old
        self.assertEqual(n, 1)
        self.assertEqual(clicked, [0x999])
        self.assertEqual(keys, [(0x999, 0x0D)])

    def test_button_dialog_not_clicked_blindly(self):
        """有按钮文字的框照旧按按钮点，绝不再补一次盲点。"""
        dialogs = [{"hwnd": 0x888, "class": "#32770", "title": "After Effects",
                    "buttons": [{"hwnd": 0x777, "text": "继续", "enabled": True}]}]
        clicked = []
        posted = []
        old = (aelaunch.find_dialogs, aelaunch._click_modal, aelaunch._click)
        aelaunch.find_dialogs = lambda: dialogs
        aelaunch._click_modal = lambda h: clicked.append(h) or True
        aelaunch._click = lambda h: posted.append(h) or True
        try:
            n = aelaunch.dismiss_dialogs()
        finally:
            aelaunch.find_dialogs, aelaunch._click_modal, aelaunch._click = old
        self.assertEqual(n, 1)
        self.assertEqual(posted, [0x777])
        self.assertEqual(clicked, [])


class TestAeDialogModule(unittest.TestCase):
    """启动期点框模块必须存在且真正委托给 core.aelaunch。"""

    def test_module_exists_and_delegates(self):
        tools = str(ROOT / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        mod = importlib.import_module("ae_dialog")
        old = aelaunch.dismiss_dialogs
        aelaunch.dismiss_dialogs = lambda verbose=False: 7
        try:
            self.assertEqual(mod.handle_once(), 7)
            self.assertEqual(mod.handle_once(verbose=True), 7)
        finally:
            aelaunch.dismiss_dialogs = old
        self.assertTrue(callable(mod.main_window))


if __name__ == "__main__":
    unittest.main()
