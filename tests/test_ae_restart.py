"""AE 重启让译名即时生效（core.aelaunch.restart_running_ae / core.aejsx）。

★ 2026-10-30 实测结论：效果名与参数名不存进 .aep，是 AE 在加载工程时按当时
  插件状态解析并缓存在进程里的，所以切换中英必须重启进程 —— 应用侧因此会在
  引擎开关后自动重启 AE。这些用例锁住安全闸（有未保存改动时绝不动用户的工程）。
"""
import os
import unittest
from unittest import mock

from core import aelaunch, aejsx


class TestTitleUnsaved(unittest.TestCase):
    def test_star_means_unsaved(self):
        self.assertTrue(aejsx.title_has_unsaved(
            "Adobe After Effects 2022 - E:/x/a.aep *"))

    def test_clean_title(self):
        self.assertFalse(aejsx.title_has_unsaved(
            "Adobe After Effects 2022 - E:/x/a.aep"))

    def test_empty(self):
        self.assertFalse(aejsx.title_has_unsaved(""))
        self.assertFalse(aejsx.title_has_unsaved(None))


class TestRestartRunningAe(unittest.TestCase):
    def test_no_window(self):
        with mock.patch.object(aelaunch, "main_window", return_value=None):
            ok, msg = aelaunch.restart_running_ae(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("主窗口", msg)

    def test_refuses_when_unsaved(self):
        with mock.patch.object(aelaunch, "main_window",
                               return_value=(101, "AE - a.aep *")):
            ok, msg = aelaunch.restart_running_ae(timeout=1.0)
        self.assertFalse(ok)
        self.assertIn("未保存", msg)

    def test_happy_path_reopens_project(self):
        exe = r"C:/Program Files/Adobe/Adobe After Effects 2022/Support Files/AfterFX.exe"
        aep = r"E:/Ae/a.aep"
        popen = mock.Mock()
        with mock.patch.object(aelaunch, "main_window",
                               return_value=(101, "AE - a.aep")), \
             mock.patch.object(aelaunch, "_pid_of_window", return_value=4242), \
             mock.patch.object(aejsx, "exe_of_pid", return_value=exe), \
             mock.patch.object(aejsx, "project_path", return_value=aep), \
             mock.patch.object(os.path, "exists", return_value=True), \
             mock.patch.object(aelaunch, "ae_pids", side_effect=[[4242], []]), \
             mock.patch.object(aelaunch, "dismiss_dialogs", return_value=0), \
             mock.patch.object(aelaunch, "wait_ready", return_value=True), \
             mock.patch.object(aelaunch, "ctypes") as fake_ct, \
             mock.patch.object(aelaunch.subprocess, "Popen", popen):
            ok, msg = aelaunch.restart_running_ae(timeout=5.0)
        self.assertTrue(ok, msg)
        self.assertIn("a.aep", msg)
        args = popen.call_args[0][0]
        self.assertEqual(args, [exe, aep])
        # 关闭走的是 WM_CLOSE(0x0010)，不是强杀
        self.assertEqual(fake_ct.windll.user32.PostMessageW.call_args[0][1], 0x0010)

    def test_does_not_launch_when_ae_never_exits(self):
        exe = r"C:/AE/AfterFX.exe"
        popen = mock.Mock()
        with mock.patch.object(aelaunch, "main_window",
                               return_value=(101, "AE - a.aep")), \
             mock.patch.object(aelaunch, "_pid_of_window", return_value=4242), \
             mock.patch.object(aejsx, "exe_of_pid", return_value=exe), \
             mock.patch.object(aejsx, "project_path", return_value=""), \
             mock.patch.object(os.path, "exists", return_value=True), \
             mock.patch.object(aelaunch, "ae_pids", return_value=[4242]), \
             mock.patch.object(aelaunch, "dismiss_dialogs", return_value=0), \
             mock.patch.object(aelaunch.time, "time") as fake_time, \
             mock.patch.object(aelaunch.time, "sleep"), \
             mock.patch.object(aelaunch, "ctypes"), \
             mock.patch.object(aelaunch.subprocess, "Popen", popen):
            fake_time.side_effect = [0.0, 0.0, 1e9]
            ok, msg = aelaunch.restart_running_ae(timeout=5.0)
        self.assertFalse(ok)
        self.assertIn("没有在", msg)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
