# -*- coding: utf-8 -*-
"""DisplayHook 模块晚 ready 握手回归（fake frida，不连 AE）。"""
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from core.live_hook import DisplayHook


class _Exports:
    def setwant(self, b):
        return bool(b)

    def setpaused(self, b):
        return 1

    def revert(self):
        return 0

    def stats(self):
        return {}

    def setreal(self, r):
        return bool(r)

    def invalidate(self):
        return 0


class _Script:
    def __init__(self, ready=True, delay=0.05):
        self.ready = ready
        self.delay = delay
        self.cb = None
        self.exports_sync = _Exports()
        self.unloaded = False

    def on(self, event, cb):
        self.cb = cb

    def load(self):
        if not self.ready:
            self.cb({"type": "send", "payload": {"k": "err", "msg": "not ready"}},
                    None)
            return

        def fire():
            import time
            time.sleep(self.delay)
            self.cb({"type": "send", "payload": {"k": "ready"}}, None)

        threading.Thread(target=fire, daemon=True).start()

    def unload(self):
        self.unloaded = True


class _Session:
    def __init__(self, script):
        self.script = script
        self.detached = False

    def create_script(self, src):
        return self.script

    def detach(self):
        self.detached = True


class ReadyHandshakeTest(unittest.TestCase):
    def _run(self, script):
        sess = _Session(script)
        mod = types.SimpleNamespace(attach=lambda pid: sess)
        hook = DisplayHook()
        hook.READY_TIMEOUT = 0.2
        with mock.patch.dict(sys.modules, {"frida": mod}):
            ok = hook.ensure(1234, {"A": "甲"}, {"A"}, 0)
        return hook, ok, sess

    def test_delayed_ready_retries_and_succeeds(self):
        hook, ok, sess = self._run(_Script(ready=True, delay=0.08))
        try:
            self.assertTrue(ok)
            self.assertTrue(hook.running())
        finally:
            hook.stop()

    def test_missing_ready_fails_and_detaches(self):
        hook, ok, sess = self._run(_Script(ready=False))
        self.assertFalse(ok)
        self.assertFalse(hook.running())
        self.assertTrue(sess.detached)
        self.assertIn("not ready", hook.last_error)

    def test_missing_ready_then_same_hook_retry_succeeds(self):
        """第一次模块未 ready 失败后，同一 DisplayHook 下一次 ensure 必须能挂上。"""
        hook, ok, sess = self._run(_Script(ready=False))
        self.assertFalse(ok)
        sess2 = _Session(_Script(ready=True, delay=0.02))
        mod = types.SimpleNamespace(attach=lambda pid: sess2)
        with mock.patch.dict(sys.modules, {"frida": mod}):
            ok2 = hook.ensure(1234, {"A": "甲"}, {"A"}, 0)
        try:
            self.assertTrue(ok2, hook.last_error)
            self.assertTrue(hook.running())
        finally:
            hook.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
