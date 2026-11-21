"""start_engine 真重启（先停旧守护）测试。

★★ 2026-10-25 事故根因修复的回归护栏：
   「重启引擎」原来不停旧守护就 spawn 新的 → 多个 frida 代理同时注入
   同一个 AE → frida-agent.dll 崩溃 → AE 卡死「崩溃修复选项」死循环。
   start_engine 现在必须先 stop_engine 再 spawn。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestStartEngineRestart(unittest.TestCase):
    def test_stops_old_guard_before_spawn(self):
        from core import engine

        order = []

        with mock.patch.object(engine, "engine_running",
                               side_effect=[True, False, False]) as m_run, \
             mock.patch.object(engine, "stop_engine",
                               return_value=(True, "引擎已停止")) as m_stop, \
             mock.patch.object(engine, "force_kill_guards") as m_kill, \
             mock.patch("subprocess.Popen") as m_popen, \
             mock.patch.object(engine.os.path, "exists", return_value=True), \
             mock.patch.object(engine.os.path, "isdir", return_value=True):

            m_stop.side_effect = lambda *a, **k: (
                order.append("stop"), (True, "ok"))[1]
            m_popen.side_effect = lambda *a, **k: order.append("spawn")

            log = os.path.join(tempfile.mkdtemp(prefix="aeloc_t_"), "g.log")
            ok, msg = engine.start_engine(
                "fake_guard.py", "fake_dict_dir",
                log_path=log, skip_hash=True)

        self.assertTrue(ok, msg)
        self.assertEqual(order, ["stop", "spawn"])   # 停必须发生在起之前
        m_stop.assert_called_once()
        m_kill.assert_not_called()   # 停掉了就不需要强杀
        m_run.assert_called()   # 至少查过一次运行状态

    def test_refuses_spawn_when_stop_not_confirmed(self):
        """新安全语义：stop 未确认退出时拒绝 spawn，不强杀未知 PID。"""
        from core import engine

        with mock.patch.object(engine, "engine_running",
                               side_effect=[True, False, False]), \
             mock.patch.object(engine, "stop_engine",
                               return_value=(False, "未退出")), \
             mock.patch.object(engine, "force_kill_guards",
                               return_value=1) as m_kill, \
             mock.patch("subprocess.Popen") as m_popen, \
             mock.patch.object(engine.os.path, "exists", return_value=True), \
             mock.patch.object(engine.os.path, "isdir", return_value=True):

            log = os.path.join(tempfile.mkdtemp(prefix="aeloc_t_"), "g.log")
            ok, _ = engine.start_engine(
                "fake_guard.py", "fake_dict_dir",
                log_path=log, skip_hash=True)

        self.assertFalse(ok)
        m_kill.assert_not_called()
        m_popen.assert_not_called()

    def test_no_stop_when_engine_not_running(self):
        from core import engine

        with mock.patch.object(engine, "engine_running", return_value=False), \
             mock.patch.object(engine, "stop_engine") as m_stop, \
             mock.patch("subprocess.Popen"), \
             mock.patch.object(engine.os.path, "exists", return_value=True), \
             mock.patch.object(engine.os.path, "isdir", return_value=True):

            log = os.path.join(tempfile.mkdtemp(prefix="aeloc_t_"), "g.log")
            ok, _ = engine.start_engine(
                "fake_guard.py", "fake_dict_dir",
                log_path=log, skip_hash=True)

        self.assertTrue(ok)
        m_stop.assert_not_called()


    def test_spawn_isolates_stdin_with_devnull(self):
        """2026-11-14 定位：guard 不能继承调用方 stdin。

        生产 driver 有后台线程阻塞读 sys.stdin；若 guard 继承同一管道，
        Windows 上子进程 Python 启动早期的 CRT stdio 位置查询会长时间阻塞
        （实测 992s 级，reader 释放/父进程退出同刻恢复）。spawn 必须显式
        stdin=subprocess.DEVNULL，且保持 stdout 重定向与无窗口 flag。
        """
        import subprocess

        from core import engine

        with mock.patch.object(engine, "engine_running", return_value=False), \
             mock.patch.object(engine, "stop_engine") as m_stop, \
             mock.patch("subprocess.Popen") as m_popen, \
             mock.patch.object(engine.os.path, "exists", return_value=True), \
             mock.patch.object(engine.os.path, "isdir", return_value=True):

            log = os.path.join(tempfile.mkdtemp(prefix="aeloc_t_"), "g.log")
            ok, msg = engine.start_engine(
                "fake_guard.py", "fake_dict_dir",
                log_path=log, skip_hash=True)

        self.assertTrue(ok, msg)
        m_stop.assert_not_called()
        kwargs = m_popen.call_args.kwargs
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("stderr"), subprocess.STDOUT)
        self.assertEqual(kwargs.get("creationflags"),
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    unittest.main()
