# -*- coding: utf-8 -*-
"""Positive guard identity cache: one PS/CIM verification, strict revalidation."""
import json
import os
import tempfile
import unittest
from unittest import mock

from core import engine


class HiddenGuardChecksTest(unittest.TestCase):
    def test_all_powershell_checks_run_without_a_console(self):
        no_window = 0x08000000
        checks = [
            (engine._engine_running_ps, (), "2", True),
            (engine.pid_is_guard, (42,), "python guard_uni.py apply", True),
            (engine.guard_pids, (), "42\n73", [42, 73]),
        ]
        with mock.patch.object(engine.subprocess, "CREATE_NO_WINDOW", no_window,
                               create=True):
            for check, args, stdout, expected in checks:
                with self.subTest(check=check.__name__), mock.patch.object(
                        engine.subprocess, "run",
                        return_value=mock.Mock(stdout=stdout)) as run:
                    self.assertEqual(check(*args), expected)
                    self.assertEqual(run.call_args.kwargs.get("creationflags"), no_window)
                    self.assertEqual(run.call_args.kwargs.get("stdin"), engine.subprocess.DEVNULL)


class GuardCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_guardcache_")
        self.lock = os.path.join(self.tmp, "guard.lock")
        self.side = self.lock + ".json"
        self.pid = 4242
        self.ctime = 111222333
        self.identity = {"protocol": "aeloc-managed-guard/1",
                         "pid": self.pid, "ctime": self.ctime,
                         "image": r"C:\Python\python.exe",
                         "token": "tok-a"}
        self._write()
        self.p_alive = mock.patch.object(
            engine, "pid_alive", side_effect=lambda p: bool(self.alive))
        self.alive = True
        self.p_ctime = mock.patch.object(
            engine, "_proc_creation_time",
            side_effect=lambda p: int(self.current_ctime))
        self.current_ctime = self.ctime
        self.p_guard = mock.patch.object(
            engine, "pid_is_guard", return_value=True)
        self.m_alive = self.p_alive.start()
        self.m_ctime = self.p_ctime.start()
        self.m_guard = self.p_guard.start()
        self.addCleanup(self.p_guard.stop)
        self.addCleanup(self.p_ctime.stop)
        self.addCleanup(self.p_alive.stop)
        engine._clear_guard_identity_cache()

    def _write(self, identity=None):
        ident = dict(identity or self.identity)
        with open(self.lock, "w", encoding="ascii") as f:
            f.write(str(ident["pid"]))
        with open(self.side, "w", encoding="utf-8") as f:
            json.dump(ident, f)

    def _read(self):
        with mock.patch.object(engine, "guard_lock_path", return_value=self.lock):
            return engine.read_guard_lock(stale_ok=True)

    def test_same_identity_uses_ps_once(self):
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self.m_guard.call_count, 1)

    def test_pid_reuse_and_death_revalidate(self):
        self.assertEqual(self._read(), self.pid)
        self.current_ctime += 1
        self.assertEqual(self._read(), 0)
        self.alive = False
        self.assertEqual(self._read(), 0)

    def test_token_change_revalidates(self):
        self.assertEqual(self._read(), self.pid)
        self.identity["token"] = "tok-b"
        self._write()
        self.assertEqual(self._read(), self.pid)
        self.assertGreaterEqual(self.m_guard.call_count, 2)

    def test_malformed_or_missing_ctime_never_fast_accepts(self):
        self.identity["ctime"] = 0
        self._write()
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self.m_guard.call_count, 2)
        with open(self.side, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self.m_guard.call_count, 3)

    def test_legacy_no_sidecar_never_fast_accepts(self):
        os.remove(self.side)
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self._read(), self.pid)
        self.assertEqual(self.m_guard.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
