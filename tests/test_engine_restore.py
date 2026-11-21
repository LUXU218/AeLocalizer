# -*- coding: utf-8 -*-
"""参数/下拉引擎启动恢复与 stale lock 的最小回归。"""
import os
import tempfile
import time
import types
import unittest
from unittest import mock

import app.backend as backend_mod
from core import engine as engine_mod


class GuardLockTest(unittest.TestCase):
    def test_stale_lock_pid_alive_only_with_stale_ok(self):
        with tempfile.TemporaryDirectory(prefix="aeloc_guard_lock_") as d:
            p = os.path.join(d, "guard.lock")
            with open(p, "w") as f:
                f.write("12345")
            old = time.time() - 3600
            os.utime(p, (old, old))
            with mock.patch.object(engine_mod, "guard_lock_path", return_value=p), \
                 mock.patch.object(engine_mod, "pid_alive", return_value=True), \
                 mock.patch.object(engine_mod, "pid_is_guard", return_value=True):
                self.assertEqual(engine_mod.read_guard_lock(), 0)
                self.assertEqual(engine_mod.read_guard_lock(stale_ok=True), 12345)

    def test_lock_pid_any_detects_elevated_guard_without_cim(self):
        with tempfile.TemporaryDirectory(prefix="aeloc_guard_lock2_") as d:
            p = os.path.join(d, "guard.lock")
            with open(p, "w") as f:
                f.write("98765")
            with mock.patch.object(engine_mod, "guard_lock_path", return_value=p), \
                 mock.patch.object(engine_mod, "pid_alive", return_value=True):
                self.assertEqual(engine_mod.read_guard_lock_pid_any(), 98765)
            with mock.patch.object(engine_mod, "guard_lock_path", return_value=p), \
                 mock.patch.object(engine_mod, "pid_alive", return_value=False):
                self.assertEqual(engine_mod.read_guard_lock_pid_any(), 0)

    def test_pid_identity_failure_is_not_guard(self):
        with mock.patch.object(engine_mod.subprocess, "run",
                               side_effect=RuntimeError("CIM denied")):
            self.assertFalse(engine_mod.pid_is_guard(12345))


class AutoEngineRestoreTest(unittest.TestCase):
    def setUp(self):
        from PySide6.QtGui import QGuiApplication

        self.app = QGuiApplication.instance() or QGuiApplication([])
        self.tmp = tempfile.mkdtemp(prefix="aeloc_engine_restore_")
        self.old_root = backend_mod.DATA_ROOT
        self.old_default = backend_mod._DEFAULT_DATA_ROOT
        backend_mod.DATA_ROOT = self.tmp
        self.old_eng_root = engine_mod.DATA_ROOT
        self.old_eng_dict = engine_mod.ENGINE_DICT_DIR
        engine_mod.DATA_ROOT = self.tmp
        engine_mod.ENGINE_DICT_DIR = os.path.join(self.tmp, "engine_dict")
        self.old_env = os.environ.get("AELOC_DATA_ROOT")
        # ★ 构造 AppBackend 前必须保留沙盒 env：backend 的旧自动适配 timer
        #   只看 AELOC_DATA_ROOT，不看 _DEFAULT；构造完成后再移除 env，
        #   以便本类直接测试 _auto_restore_engine_async。
        os.environ["AELOC_DATA_ROOT"] = self.tmp
        # 隔离外部 guard/engine 探测；只 mock 边界，不 mock auto_restore/startEngine。
        self._check_patch = mock.patch.object(
            engine_mod, "check", return_value=engine_mod.EngineStatus())
        self._check_patch.start()
        self.addCleanup(self._check_patch.stop)
        self.b = backend_mod.AppBackend()
        os.environ.pop("AELOC_DATA_ROOT", None)
        # 构造时用旧 default 防止常驻 timer 启动；构造后再放开 auto_restore 闸。
        backend_mod._DEFAULT_DATA_ROOT = self.tmp
        # 这是放开既有 _live_auto_allowed 测试权限，不是 fork 测试算法。
        self.b._live_auto_allowed = lambda: True

    def tearDown(self):
        backend_mod.DATA_ROOT = self.old_root
        backend_mod._DEFAULT_DATA_ROOT = self.old_default
        engine_mod.DATA_ROOT = self.old_eng_root
        engine_mod.ENGINE_DICT_DIR = self.old_eng_dict
        if self.old_env is None:
            os.environ.pop("AELOC_DATA_ROOT", None)
        else:
            os.environ["AELOC_DATA_ROOT"] = self.old_env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inv(self):
        from core.inventory import CH_PARAM

        class _G:
            group_id = "test"
            selected = True
            channels = {CH_PARAM}

        class _Inv:
            groups = [_G()]

            def selected(self):
                return [g for g in self.groups if g.selected]

        self.b._inv = _Inv()

    def test_auto_restore_starts_when_param_intent_and_dead_guard(self):
        self._inv()
        self.b._engineAutoTried = False
        self.b._engineBusy = False
        started = []
        with mock.patch.object(engine_mod, "engine_running", return_value=False), \
             mock.patch.object(engine_mod, "read_guard_lock", return_value=0), \
             mock.patch.object(engine_mod, "find_guard", return_value=("g", "")), \
             mock.patch.object(self.b, "startEngine",
                               side_effect=lambda: (started.append(1),
                                                    setattr(self.b, "_engineBusy", True))):
            self.b._auto_restore_engine_async()
        self.assertEqual(started, [1])
        self.assertTrue(self.b._engineRestoreQuiet)

    def test_auto_restore_gated_off_does_not_touch_guard_or_start(self):
        self._inv()
        self.b._live_auto_allowed = lambda: False
        with mock.patch.object(engine_mod, "engine_running") as er, \
             mock.patch.object(engine_mod, "read_guard_lock") as rl, \
             mock.patch.object(self.b, "startEngine") as st:
            self.b._auto_restore_engine_async()
        self.assertFalse(er.called)
        self.assertFalse(rl.called)
        self.assertFalse(st.called)

    def test_auto_restore_respects_explicit_stop(self):
        self._inv()
        self.b._engine_intent_set(False)
        with mock.patch.object(self.b, "startEngine") as st:
            self.b._auto_restore_engine_async()
        self.assertFalse(st.called)

    def test_new_adaptation_restarts_only_outdated_managed_engine(self):
        self._inv()
        with mock.patch.object(engine_mod, "engine_running", return_value=True), \
                mock.patch.object(engine_mod, "find_guard", return_value=("g", "")), \
                mock.patch("core.guardruntime.managed_code_outdated", side_effect=[False, True]), \
                mock.patch.object(self.b, "_send_language_control") as control, \
                mock.patch.object(self.b, "startEngine") as start:
            self.b._auto_restore_engine_async(refresh_runtime=True)
            self.assertEqual(control.call_count, 1)
            self.assertFalse(start.called)
            self.b._auto_restore_engine_async(refresh_runtime=True)
            self.assertEqual(control.call_count, 1)
            self.assertEqual(start.call_count, 1)

    def test_engine_running_requires_confirmed_guard(self):
        old = engine_mod._STOPPED_AT
        engine_mod._STOPPED_AT = 0.0
        try:
            with mock.patch.object(engine_mod, "guard_confirmed", return_value=False), \
                 mock.patch.object(engine_mod, "_engine_log_fresh", return_value=True):
                self.assertFalse(engine_mod.engine_running())
            with mock.patch.object(engine_mod, "guard_confirmed", return_value=True):
                self.assertTrue(engine_mod.engine_running())
        finally:
            engine_mod._STOPPED_AT = old

    def test_start_engine_failure_clears_busy_and_allows_retry(self):
        self._inv()
        self.b._store.resolve = lambda: {"X": types.SimpleNamespace(zh="甲")}
        captured = {}

        def fake_run(fn, on_done, *a, **k):
            captured["fail"] = k.get("_on_fail")
            captured["done"] = on_done

        self.b._run = fake_run
        self.b._engineBusy = False
        self.b._engineRestoreQuiet = True
        with mock.patch.object(engine_mod, "find_guard", return_value=("g", "")):
            self.b.startEngine()
        self.assertTrue(self.b._engineBusy)
        captured["fail"]("prepare_dict boom")
        self.assertFalse(self.b._engineBusy)
        self.assertFalse(self.b._engineRestoreQuiet)
        self.assertFalse(self.b._engineAutoTried)

    def _report_running_text(self):
        rows = self.b.engineReport()
        return next(r["detail"] for r in rows if r["label"] == "引擎进程")

    def test_refresh_engine_updates_report_cache_false_to_true(self):
        st0 = engine_mod.EngineStatus(running=False)
        st1 = engine_mod.EngineStatus(running=True)
        with mock.patch.object(engine_mod, "check", side_effect=[st0, st1]):
            self.b.refreshEngine()
            self.assertEqual(self._report_running_text(), "未运行")
            self.b.refreshEngine()
            self.assertEqual(self._report_running_text(), "运行中")

    def test_refresh_engine_updates_report_cache_true_to_false(self):
        st0 = engine_mod.EngineStatus(running=True)
        st1 = engine_mod.EngineStatus(running=False)
        with mock.patch.object(engine_mod, "check", side_effect=[st0, st1]):
            self.b.refreshEngine()
            self.assertEqual(self._report_running_text(), "运行中")
            self.b.refreshEngine()
            self.assertEqual(self._report_running_text(), "未运行")

    def test_param_catchup_writes_existing_param_display_only(self):
        from core.inventory import CH_PARAM

        class _F:
            match_name = "m1"

        class _G:
            group_id = "test"
            selected = True
            channels = {CH_PARAM}
            files = [_F()]

        class _Inv:
            groups = [_G()]

            def selected(self):
                return self.groups

        self.b._inv = _Inv()
        self.b._live_auto_allowed = lambda: True
        self.b._live_mapping = lambda: {}
        self.b._paramCatchupBusy = False
        self.b._paramCatchupAt = 0.0
        writes = []

        class _Proc:
            def __init__(self, *a, **k):
                pass

            def read(self, addr, size):
                return b"\x00" * size

            def u64(self, addr):
                return 10

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Slot:
            kind = "param"

            def __init__(self, addr):
                self.english = "Blur" if addr == 1 else "New"

        class _LP:
            profile = types.SimpleNamespace(display_cache=0xF0)
            display_off = 0xF0

            def __init__(self, *a, **k):
                self.profile = k.get("profile") or _LP.profile
                self.display_off = 0xF0

            def _slot_from_stream(self, addr):
                return _Slot(addr)

            def write_display(self, slot, zh):
                writes.append(zh)
                return True

        def fake_parameter_rows(p, data_root, deadline=None, cancel=None,
                                with_state=False):
            rows = [{"name": "Blur", "pfName": "Blur", "address": 1,
                     "effectMatchName": "m1", "guid": "00" * 16,
                     "parent": 10},
                    {"name": "New", "pfName": "New", "address": 2,
                     "effectMatchName": "m1", "guid": "00" * 16,
                     "parent": 10}]
            return (_LP.profile, rows, {10}, False) if with_state else (
                _LP.profile, rows, {10})

        def fake_run(fn, on_done, *a, **k):
            on_done(fn())

        self.b._run = fake_run
        self.b._live_session_key = lambda pid: (123, 0, "x")
        with mock.patch("core.aelaunch.live_ae_target", return_value=(123, "x")), \
             mock.patch("core.export.collect_layers",
                        return_value=({"Blur": {"zh": "模糊"},
                                       "New": {"zh": "新"}}, {})), \
             mock.patch("core.paramdiscovery.parameter_rows", fake_parameter_rows), \
             mock.patch("core.liveparams.LiveParams", _LP), \
             mock.patch("core.proc.Process", _Proc), \
             mock.patch.object(self.b, "_live_win32_redraw"):
            self.b._param_catchup_async("test")
        self.assertEqual(sorted(writes), ["新", "模糊"])

    def test_param_catchup_skips_effect_only(self):
        from core.inventory import CH_EFFECT

        class _G:
            group_id = "test"
            selected = True
            channels = {CH_EFFECT}

        class _Inv:
            groups = [_G()]

            def selected(self):
                return self.groups

        self.b._inv = _Inv()
        self.b._live_auto_allowed = lambda: True
        with mock.patch.object(self.b, "_run") as run:
            self.b._param_catchup_async("test")
        self.assertFalse(run.called)

    def test_auto_restore_skips_effect_only(self):
        from core.inventory import CH_EFFECT

        class _G:
            selected = True
            channels = {CH_EFFECT}

        class _Inv:
            groups = [_G()]

            def selected(self):
                return self.groups

        self.b._inv = _Inv()
        with mock.patch.object(self.b, "startEngine") as st:
            self.b._auto_restore_engine_async()
        self.assertFalse(st.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
