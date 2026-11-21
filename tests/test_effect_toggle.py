# -*- coding: utf-8 -*-
"""效果名通道开关竞态回归（2026-11-09）。

覆盖父代理要求的确定性场景：
  · 旧的 OFF pause 阻塞时，新 ON 必须最终赢（最后意图）；
  · 10 个完整 OFF→ON 循环，每一轮都恢复/暂停；
  · 快速重复 OFF 只允许一个收尾线程（旧版每个 400ms 拍起一个）；
  · 阻塞 RPC 期间，UI 线程读 running()/paused()/pid() 必须 <50ms；
  · _effWant 从未点过（None）时仍按 channelState 自动判断。

全部只使用 fake hook / fake exports_sync，不启动 AE、不读写真实数据。
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


class FakeHook:
    """可阻塞的 DisplayHook 替身；不碰 frida，不碰 AE。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._running = True
        self._pid = 4242
        self.pause_entered = threading.Event()
        self.pause_release = threading.Event()
        self.block_pause = False
        self.calls = []
        self.reverts = 0
        self.revert_addrs_calls = 0

    # 与 DisplayHook 相同的非阻塞状态接口
    def running(self):
        return self._running

    def paused(self):
        with self._lock:
            return self._paused

    def pid(self):
        return self._pid

    def panes(self):
        return 0

    def pause(self):
        with self._lock:
            self.calls.append("pause:start")
        if self.block_pause:
            self.pause_entered.set()
            self.pause_release.wait(5.0)
        with self._lock:
            self._paused = True
            self.calls.append("pause:done")
        return True

    def resume(self):
        with self._lock:
            self._paused = False
            self.calls.append("resume")
        return True

    def revert_now(self):
        with self._lock:
            self.reverts += 1
            self.calls.append("revert_now")
        return 1

    def revert_addrs(self, addrs):
        with self._lock:
            self.revert_addrs_calls += 1
            self.calls.append("revert_addrs")
        return {"written": 0}

    def stats(self):
        with self._lock:
            self.calls.append("stats")
        return {}

    def set_real_names(self, names):
        with self._lock:
            self.calls.append(("setreal", tuple(sorted(names))))
        return True

    def ensure(self, pid, mapping):
        return True

    def stop(self):
        with self._lock:
            self._paused = False
            self.calls.append("stop")


class FakeExports:
    """模拟 frida exports_sync：setpaused(True) 阻塞在 Event 上。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.setpaused_calls = []

    def setpaused(self, value):
        self.setpaused_calls.append(bool(value))
        if value:
            self.entered.set()
            self.release.wait(5.0)
        return 2

    def setwant(self, value):
        return bool(value)

    def revert(self):
        return 1

    def revert_addrs(self, addrs):
        return {"written": 0}

    def stats(self):
        return {"calls": 0}

    def unload(self):
        return None


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestEffectToggleRace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app.backend as backend_mod

        cls._tmpdir = tempfile.mkdtemp(prefix="aeloc_test_effect_toggle_")
        cls._old_root = backend_mod.DATA_ROOT
        backend_mod.DATA_ROOT = cls._tmpdir
        cls.bm = backend_mod
        cls.b = backend_mod.AppBackend()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.b._liveHook = None
        except Exception:
            pass
        cls.bm.DATA_ROOT = cls._old_root
        import shutil

        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        self.b = self.__class__.b
        self.b._liveHook = None
        self.b._liveHookStopped = False
        self.b._liveHookStopBusy = False
        self.b._liveHookResumedGen = None
        self.b._liveHookSession = None
        self.b._liveRealNamesSig = None
        self.b._liveRealNamesCache = None
        self.b._liveSessionEpoch = 0
        self.b._liveHookCatchupGen = None
        self.b._liveFirstRefreshGen = None
        self.b._liveHookStopped = False
        self.b._liveHookAt = 0.0
        self.b._liveNudgeAt = 0.0
        self.b._liveAutoInflight = False
        self.b._effWant = None
        self.b._effGen = 0
        # 单元测试里不打真实 AE 重绘窗口。
        self.b._live_nudge_after_patch = lambda: None
        # ★ 默认 stub 真实 _live_apply：_live_hook_catchup 会调用它，否则
        #   每个 ON 都启动真实 Worker，测试退出时 Qt6Core 会崩（0xC0000409）。
        #   专门测 catch-up 的用例在这个 stub 之上显式 mock。
        self._orig_live_apply = self.b._live_apply
        self.b._live_apply = lambda *a, **k: None
        self.addCleanup(self._restore_live_apply)

    def _restore_live_apply(self):
        try:
            self.b._live_apply = self._orig_live_apply
        except Exception:
            pass

    def tearDown(self):
        self._wait(lambda: not getattr(self.b, "_liveHookStopBusy", False), 2.0)
        self.b._liveHook = None

    def _wait(self, predicate, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    def _install_fake(self, fake):
        self.b._liveHook = fake
        self.b._liveHookAt = 0.0
        self.b._liveHookStopped = False
        self.b._liveHookStopBusy = False
        self.b._liveHookResumedGen = None

    def test_run_from_python_background_delivers_on_main_thread(self):
        """真实 Qt 主循环：Python 背景线程 _run 的 done 必须在 owner 线程。"""
        from PySide6.QtCore import QEventLoop, QThread, QTimer

        seen = {}

        def bg():
            self.b._run(lambda: 8, lambda value: seen.update(
                value=value,
                main=(QThread.currentThread() == self.b.thread())))

        t = threading.Thread(target=bg, daemon=True)
        loop = QEventLoop()
        QTimer.singleShot(900, loop.quit)
        t.start()
        loop.exec()
        self.assertEqual(seen.get("value"), 8)
        self.assertTrue(seen.get("main"), "done 没有回到 AppBackend 的 Qt 线程")
        self.assertTrue(self._wait(lambda: not self.b._workers, 2.0),
                        "Worker 完成后 _workers 未清空")

    def test_default_on_gen0_can_be_cancelled_by_off(self):
        """默认选区 ON（_effWant=None, gen0）也能被随后 OFF 的 gen1 取消。"""
        from core.inventory import CH_EFFECT

        class _G:
            channels = {CH_EFFECT}

        class _Inv:
            groups = [_G()]

        old_inv = self.b._inv
        self.addCleanup(lambda: setattr(self.b, "_inv", old_inv))
        self.b._inv = _Inv()
        self.b._effWant = None
        self.b._effGen = 0
        self.assertTrue(self.b._live_intent_ok(0, True))
        self.b._live_intent_set(False)
        self.assertFalse(self.b._live_intent_ok(0, True))
        self.assertTrue(self.b._live_intent_ok(1, False))

    def test_pending_apply_clears_catchup_for_retry(self):
        """roots pending 的 no-op 不能把 catchup 代次永久记成功。"""
        from core.inventory import CH_EFFECT

        class _G:
            channels = {CH_EFFECT}

        class _Inv:
            groups = [_G()]

        old_inv = self.b._inv
        self.addCleanup(lambda: setattr(self.b, "_inv", old_inv))
        self.b._inv = _Inv()
        self.b._effWant = True
        self.b._effGen = 5
        captured = {}
        old_run = self.b._run
        self.addCleanup(lambda: setattr(self.b, "_run", old_run))

        def fake_run(fn, on_done, *a, **k):
            captured["done"] = on_done

        self.b._run = fake_run
        self.b._liveHookCatchupGen = 5
        self.b._status = "实时写入 18 条"
        toasts = []
        self.b.toast.connect(lambda lvl, msg: toasts.append((lvl, msg)))
        # setUp 默认 stub 了实例 _live_apply；这里必须调用真实类方法，
        # 才能拿到真实 done 闭包（不能直接造 done）。
        type(self.b)._live_apply(self.b, auto_rename=True, quiet=True,
                                 intent_gen=5)
        captured["done"]({"noopReason": "parade roots pending/empty"})
        self.assertIsNone(self.b._liveHookCatchupGen)
        self.assertEqual(self.b._status, "效果名同步中，稍后自动补写")
        self.assertNotIn("parade", self.b._status)
        self.assertEqual(toasts, [])

    def test_old_generation_done_cannot_overwrite_off_state(self):
        """OFF 后旧 ON 的 done 不得覆盖 _status 或弹提示。"""
        captured = {}
        old_run = self.b._run
        self.addCleanup(lambda: setattr(self.b, "_run", old_run))

        def fake_run(fn, on_done, *a, **k):
            captured["done"] = on_done

        self.b._run = fake_run
        self.b._effWant = False
        self.b._effGen = 9
        self.b._status = "old"
        toasts = []
        self.b.toast.connect(lambda lvl, msg: toasts.append((lvl, msg)))
        type(self.b)._live_apply(self.b, auto_rename=True, quiet=False,
                                 intent_gen=8)
        captured["done"]({"applied": {"X": "甲"}, "written": 1})
        self.assertEqual(self.b._status, "old")
        self.assertEqual(toasts, [])

    def test_hook_enable_uses_catchup_not_duplicate_warm(self):
        """真实 _live_hook_tick 启用路径只排 catchup，不另开 Process/walk。"""
        from unittest import mock
        from PySide6.QtCore import QEventLoop, QTimer
        import core.proc as proc_mod

        fake = FakeHook()
        fake._running = False          # 走 ensure 分支
        self._install_fake(fake)
        gen = self.b._live_intent_set(True)
        opened = []
        catchups = []
        with mock.patch.object(self.b, "_live_mapping",
                               return_value={"CC Test": "CC 测试"}), \
             mock.patch.object(self.b, "_live_real_names", return_value={"CC Test"}), \
             mock.patch.object(self.b, "_live_ui_title_ret", return_value=0xA90C3F), \
             mock.patch.object(fake, "ensure", return_value=True), \
             mock.patch.object(self.b, "_live_hook_catchup",
                               side_effect=lambda g: catchups.append(g)), \
             mock.patch.object(self.b, "_live_first_refresh"), \
             mock.patch.object(self.b, "_live_nudge_after_patch"), \
             mock.patch.object(proc_mod, "Process",
                               side_effect=lambda *a, **k: opened.append(a)):
            self.b._live_hook_tick(4242)
            # 给旧版重复 _warm 线程一个执行窗口。
            loop = QEventLoop()
            QTimer.singleShot(300, loop.quit)
            loop.exec()
        self.assertEqual(opened, [], "hook 启用路径仍直接打开 Process/做 walk")
        self.assertEqual(catchups, [gen])
        self.assertEqual(self.b._liveHookResumedGen, gen)

    def test_initial_state_is_explicit(self):
        """_liveHook 等状态必须在 __init__ 就存在（TestLiveWatch 回归）。"""
        self.assertTrue(hasattr(self.b, "_liveHook"))
        self.assertTrue(hasattr(self.b, "_effWant"))
        self.assertTrue(hasattr(self.b, "_effGen"))
        self.assertIsNone(self.b._liveHook)

    def test_eff_want_none_uses_channel_state(self):
        """从没点过开关时，仍按 channelState 判断（不能把自动逻辑弄死）。"""
        from core.inventory import CH_EFFECT

        class _G:
            channels = set()

        class _Inv:
            groups = [_G()]

        old_inv = self.b._inv
        try:
            self.b._inv = _Inv()
            self.b._effWant = None
            self.b._effGen = 0
            want, gen = self.b._live_intent()
            self.assertFalse(want)
            self.assertEqual(gen, 0)
            self.assertTrue(self.b._live_intent_ok(0, False))
            self.assertFalse(self.b._live_intent_ok(0, True))

            _Inv.groups[0].channels = {CH_EFFECT}
            want, _ = self.b._live_intent()
            self.assertTrue(want)
            self.assertEqual(self.b._live_intent_set(True), 1)
            self.assertTrue(self.b._live_intent_ok(1, True))
        finally:
            self.b._inv = old_inv

    def test_delayed_off_cannot_pause_after_new_on(self):
        """旧 OFF 的 pause 阻塞时来新 ON：释放后最终必须是 ON，且不 revert。"""
        fake = FakeHook()
        fake.block_pause = True
        self._install_fake(fake)
        gen_off = self.b._live_intent_set(False)
        self.b._live_hook_stop("test")
        self.assertTrue(fake.pause_entered.wait(2.0), "OFF pause 没有进入")

        gen_on = self.b._live_intent_set(True)
        t = threading.Thread(target=self.b._live_hook_tick, args=(4242,), daemon=True)
        t.start()
        time.sleep(0.08)          # 让 tick 到达 op 锁并等待旧 pause
        fake.pause_release.set()
        t.join(3.0)
        self.assertTrue(self._wait(lambda: not self.b._liveHookStopBusy, 2.0))

        self.assertFalse(fake.paused(), "新 ON 之后 hook 仍是暂停态")
        self.assertGreaterEqual(fake.calls.count("resume"), 1)
        self.assertEqual(fake.reverts, 0, "旧 OFF 不应在新 ON 之后 revert")
        self.assertEqual(self.b._liveHookResumedGen, gen_on)
        self.assertGreater(gen_on, gen_off)

    def test_ten_off_on_cycles_keep_last_intent(self):
        """10 个完整 OFF→ON 循环，最后一轮必须回到中文（未暂停）。"""
        fake = FakeHook()
        self._install_fake(fake)
        for i in range(10):
            self.b._live_intent_set(False)
            self.b._live_hook_stop("cycle-off-%d" % i)
            self.assertTrue(self._wait(lambda: self.b._liveHookStopped, 2.0),
                            "第 %d 轮 OFF 未完成" % (i + 1))
            self.assertTrue(fake.paused(), "第 %d 轮 OFF 后 hook 未暂停" % (i + 1))

            gen = self.b._live_intent_set(True)
            self.b._liveHookAt = 0.0
            self.b._live_hook_tick(4242)
            self.assertFalse(fake.paused(), "第 %d 轮 ON 后 hook 仍暂停" % (i + 1))
            self.assertEqual(self.b._liveHookResumedGen, gen)

    def test_paused_hook_new_on_bypasses_throttle_and_catches_up(self):
        """hook running 但 paused、上一 tick 未满 1s：新 ON 必须立刻 resume，
        并在 resume 后补一次 _live_apply（覆盖暂停窗口内出生的实例）。"""
        from unittest import mock

        fake = FakeHook()
        fake._paused = True
        self._install_fake(fake)
        old_gen = self.b._effGen
        self.b._liveHookResumedGen = old_gen   # 上一代已 resume，模拟旧节流
        self.b._liveHookAt = time.monotonic()   # 1s 节流尚未满
        gen = self.b._live_intent_set(True)
        self.assertEqual(gen, old_gen + 1)     # 不依赖前序用例留下的代号
        calls = []

        def fake_resume():
            fake.calls.append("resume")
            fake._paused = False
            return True

        def fake_apply(**kw):
            fake.calls.append("apply")
            calls.append(("apply", kw.get("intent_gen"),
                          kw.get("auto_rename"), kw.get("quiet")))

        with mock.patch.object(fake, "resume", side_effect=fake_resume), \
             mock.patch.object(self.b, "_live_real_names",
                               return_value={"CC Test"}), \
             mock.patch.object(self.b, "_live_apply", side_effect=fake_apply):
            self.b._live_hook_tick(4242)

        self.assertFalse(fake.paused())
        self.assertEqual(self.b._liveHookResumedGen, gen)
        self.assertEqual(self.b._liveHookCatchupGen, gen)
        self.assertIn(("apply", gen, True, True), calls)
        self.assertLess(fake.calls.index("resume"), fake.calls.index("apply"))

    def test_cross_ae_same_gen_resets_catchup_and_first_refresh(self):
        """旧 AE 的 resumed/catchup/first 标记不能跨新 AE 复用。"""
        from unittest import mock

        fake = FakeHook()
        self._install_fake(fake)
        gen = self.b._live_intent_set(True)
        self.b._liveHookSession = ("old", 1, "AE")
        self.b._liveHookResumedGen = gen
        self.b._liveHookCatchupGen = gen
        self.b._liveFirstRefreshGen = gen
        self.b._liveHookAt = 0.0
        catchups, firsts = [], []
        with mock.patch.object(self.b, "_live_session_key",
                               return_value=("new", 2, "AE")), \
             mock.patch.object(self.b, "_live_mapping",
                               return_value={"CC Test": "CC 测试"}), \
             mock.patch.object(self.b, "_live_real_names",
                               return_value={"CC Test"}), \
             mock.patch.object(self.b, "_live_ui_title_ret", return_value=0), \
             mock.patch.object(fake, "ensure", return_value=True), \
             mock.patch.object(self.b, "_live_hook_catchup",
                               side_effect=lambda g: catchups.append(g)), \
             mock.patch.object(self.b, "_live_first_refresh",
                               side_effect=lambda pid, g: firsts.append(g)), \
             mock.patch.object(self.b, "_live_nudge_after_patch"), \
             mock.patch.object(self.b, "_live_hook_stats"):
            self.b._live_hook_tick(4242)
        self.assertEqual(self.b._liveHookSession, ("new", 2, "AE"))
        self.assertEqual(catchups, [gen])
        self.assertEqual(firsts, [gen])

    def test_stale_session_failure_and_done_do_not_touch_new_state(self):
        """旧 AE/旧代 callback 不得清新会话 inflight/catchup/status。"""
        captured = {}
        old_run = self.b._run
        self.addCleanup(lambda: setattr(self.b, "_run", old_run))

        def fake_run(fn, on_done, *a, **k):
            captured["done"] = on_done
            captured["fail"] = k.get("_on_fail")

        self.b._run = fake_run
        self.b._effWant = True
        self.b._effGen = 16
        old_epoch = self.b._liveSessionEpoch
        type(self.b)._live_apply(self.b, auto_rename=True, quiet=True,
                                 memo_key=(1, 2), intent_gen=16)
        self.b._liveSessionEpoch = old_epoch + 1
        self.b._liveAutoInflight = True
        self.b._liveHookCatchupGen = 16
        self.b._status = "new"
        toasts = []

        def _toast(lvl, msg):
            toasts.append((lvl, msg))

        self.b.toast.connect(_toast)
        self.addCleanup(lambda: self.b.toast.disconnect(_toast))
        captured["fail"]("old failure")
        self.assertEqual(self.b._status, "new")
        self.assertTrue(self.b._liveAutoInflight)
        self.assertEqual(self.b._liveHookCatchupGen, 16)
        self.assertEqual(toasts, [])
        captured["done"]({"applied": {"A": "甲"}, "written": 1})
        self.assertEqual(self.b._status, "new")
        self.assertTrue(self.b._liveAutoInflight)
        self.assertEqual(self.b._liveHookCatchupGen, 16)
        self.assertEqual(toasts, [])

    def test_real_names_growth_hot_updates_same_gen(self):
        """启动期 REAL 从空/部分到完整：同 gen 热更新并重新排 catchup/first。"""
        from unittest import mock

        fake = FakeHook()
        self._install_fake(fake)
        self.b._liveHookSession = self.b._live_session_key(4242)
        gen = self.b._live_intent_set(True)
        self.b._liveHookResumedGen = gen
        self.b._liveHookAt = 0.0
        real = [{"A"}, {"A", "B"}, {"A", "B"}]
        catchups = []
        firsts = []
        with mock.patch.object(self.b, "_live_real_names",
                               side_effect=lambda pid: real.pop(0)), \
             mock.patch.object(self.b, "_live_hook_catchup",
                               side_effect=lambda g: catchups.append(g)), \
             mock.patch.object(self.b, "_live_first_refresh",
                               side_effect=lambda pid, g: firsts.append(g)), \
             mock.patch.object(self.b, "_live_hook_stats"):
            self.b._live_hook_tick(4242)
            self.b._liveHookAt = 0.0
            self.b._live_hook_tick(4242)
            self.b._liveHookAt = 0.0
            self.b._live_hook_tick(4242)
        self.assertEqual(catchups, [gen, gen, gen])
        self.assertEqual(firsts, [gen, gen, gen])
        self.assertIn(("setreal", ("A", "B")), fake.calls)

    def test_real_names_sync_failure_retries_same_gen(self):
        """setreal 返回 False/异常时不能先提交 sig，下一拍必须重试。"""
        from unittest import mock

        fake = FakeHook()
        self._install_fake(fake)
        self.b._liveHookSession = self.b._live_session_key(4242)
        gen = self.b._live_intent_set(True)
        self.b._liveHookResumedGen = gen
        self.b._liveHookAt = 0.0
        catchups = []
        with mock.patch.object(self.b, "_live_real_names",
                               return_value={"A"}), \
             mock.patch.object(fake, "set_real_names",
                               side_effect=[False, RuntimeError("rpc")]), \
             mock.patch.object(self.b, "_live_hook_catchup",
                               side_effect=lambda g: catchups.append(g)), \
             mock.patch.object(self.b, "_live_first_refresh"), \
             mock.patch.object(self.b, "_live_hook_stats"):
            self.b._live_hook_tick(4242)
            self.assertIsNone(self.b._liveRealNamesSig)
            self.assertEqual(catchups, [])
            self.b._liveHookAt = 0.0
            self.b._live_hook_tick(4242)
            self.assertIsNone(self.b._liveRealNamesSig)  # exception 也不提交
            self.b._liveHookAt = 0.0
            with mock.patch.object(fake, "set_real_names", return_value=True):
                self.b._live_hook_tick(4242)
        self.assertEqual(self.b._liveRealNamesSig, frozenset({"A"}))
        self.assertEqual(catchups, [gen])

    def test_registry_read_failure_keeps_known_real_names(self):
        """临时 Registry 失败不能把已确认 REAL 清空。"""
        from unittest import mock
        import core.proc as proc_mod

        self.b._liveRealNamesCache = (4242, 0.0, frozenset({"A"}))
        with mock.patch.object(proc_mod, "Process",
                               side_effect=RuntimeError("registry unavailable")):
            got = self.b._live_real_names(4242)
        self.assertEqual(got, {"A"})

    def test_off_nudge_sends_win32_redraw_to_target_pid(self):
        """OFF 收尾必须发 Win32 异步重绘，不能只调 noop invalidate。"""
        fake = FakeHook()
        self._install_fake(fake)
        self.b._liveNudgeAt = time.monotonic()  # 模拟 ON 刚 nudge 过
        calls = []
        old = self.b._live_win32_redraw
        self.addCleanup(lambda: setattr(self.b, "_live_win32_redraw", old))
        self.b._live_win32_redraw = lambda pid: calls.append(pid)
        # setUp 把实例方法替换成了无参 lambda；这里必须调用真实类方法。
        type(self.b)._live_nudge_after_patch(self.b, force=True)
        self.assertTrue(self._wait(lambda: len(calls) >= 3, 2.0),
                        "OFF nudge 没有发出 3 次 Win32 redraw")
        self.assertTrue(all(p == 4242 for p in calls), calls)

    def test_epoch_change_after_first_redraw_cancels_remaining(self):
        """第一次 Win32 后会话 epoch 变化，余下 2 次必须取消。"""
        fake = FakeHook()
        self._install_fake(fake)
        self.b._liveSessionEpoch = 0
        self.b._liveNudgeAt = 0.0
        calls = []
        old = self.b._live_win32_redraw
        self.addCleanup(lambda: setattr(self.b, "_live_win32_redraw", old))
        self.addCleanup(lambda: setattr(self.b, "_liveSessionEpoch", 0))

        def _redraw(pid):
            calls.append(pid)
            self.b._liveSessionEpoch = 1

        self.b._live_win32_redraw = _redraw
        type(self.b)._live_nudge_after_patch(self.b, force=True)
        self.assertTrue(self._wait(lambda: len(calls) >= 1, 2.0))
        time.sleep(0.3)
        self.assertEqual(calls, [4242], "epoch 变化后仍继续发 Win32 redraw")

    def test_rapid_off_only_one_stop_worker(self):
        """连续 10 次 OFF 请求只能产生一次 pause（旧版每拍一个线程）。"""
        fake = FakeHook()
        fake.block_pause = True
        self._install_fake(fake)
        self.b._live_intent_set(False)
        for _ in range(10):
            self.b._live_hook_stop("rapid")
        self.assertTrue(fake.pause_entered.wait(2.0))
        self.assertEqual(fake.calls.count("pause:start"), 1,
                         "旧 OFF 收尾线程没有被合并: %r" % (fake.calls,))
        fake.pause_release.set()
        self.assertTrue(self._wait(lambda: not self.b._liveHookStopBusy, 2.0))

    def test_off_does_not_do_slow_walk_under_control_lock(self):
        """OFF 收尾不得在 _liveOpLock 内现场 walk（否则新 ON 等 15~45 秒）。"""
        from unittest import mock
        from core import liveparams

        fake = FakeHook()
        self._install_fake(fake)
        liveparams._WALK_CACHE.pop(4242, None)
        self.b._live_intent_set(False)
        t0 = time.monotonic()
        with mock.patch.object(liveparams.LiveParams, "walk",
                               side_effect=AssertionError("OFF 不应现场 walk")):
            self.b._live_hook_stop("no-slow-walk")
            self.assertTrue(self._wait(lambda: not self.b._liveHookStopBusy, 2.0))
        self.assertLess(time.monotonic() - t0, 0.5,
                        "OFF 收尾被慢 walk 拖住")
        self.assertTrue(fake.paused())

    def test_status_reads_do_not_block_on_rpc(self):
        """阻塞 setpaused 时，UI 线程读 running/paused/pid 必须 <50ms。

        使用真实 DisplayHook + fake exports_sync，和父代理的复现方式一致；
        QTimer 心跳验证主线程没有被状态查询拖住。
        """
        from core.live_hook import DisplayHook

        hook = DisplayHook()
        exports = FakeExports()
        script = types.SimpleNamespace(exports_sync=exports,
                                       unload=lambda: None)
        hook._script = script
        hook._session = types.SimpleNamespace(detach=lambda: None)
        hook._pid = 4242
        hook._paused = False

        worker = threading.Thread(target=hook.pause, daemon=True)
        worker.start()
        self.assertTrue(exports.entered.wait(2.0), "pause 没有进入阻塞 RPC")

        stats = {"ticks": 0, "max_ms": 0.0, "over_50ms": 0}

        def beat():
            t0 = time.monotonic()
            hook.running()
            hook.paused()
            hook.pid()
            hook.panes()
            elapsed = (time.monotonic() - t0) * 1000.0
            stats["ticks"] += 1
            stats["max_ms"] = max(stats["max_ms"], elapsed)
            if elapsed >= 50.0:
                stats["over_50ms"] += 1

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(beat)
        timer.start()
        loop = QEventLoop()
        QTimer.singleShot(260, exports.release.set)
        QTimer.singleShot(520, loop.quit)
        loop.exec()
        timer.stop()
        worker.join(3.0)
        paused_after_rpc = hook.paused()
        try:
            hook.stop()
        except Exception:
            pass

        self.assertGreater(stats["ticks"], 20, "Qt 心跳没有正常跑起来")
        self.assertEqual(stats["over_50ms"], 0,
                         "状态查询被阻塞 RPC 拖住: max=%.1fms" % stats["max_ms"])
        self.assertLess(stats["max_ms"], 50.0,
                        "状态查询耗时 %.1fms" % stats["max_ms"])
        self.assertTrue(paused_after_rpc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
