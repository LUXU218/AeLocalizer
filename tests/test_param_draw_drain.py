# -*- coding: utf-8 -*-
"""Python drain must fail closed on missing/incomplete stats and pending>0."""
import threading
import unittest

from runtime.param_draw_hook import ParamDrawHook


class _Exports:
    def __init__(self, value):
        self.value = value

    def stats(self):
        return self.value


class _Script:
    def __init__(self, value):
        self.exports_sync = _Exports(value)


class DrainTest(unittest.TestCase):
    def _hook(self, value):
        h = object.__new__(ParamDrawHook)
        h.script = _Script(value)
        h._lock = threading.RLock()
        h.timeout = 0.05
        h.last_error = ""
        return h

    def test_completed(self):
        self.assertTrue(self._hook({"pending": 0, "active": 0}).drain(0.2))

    def test_pending_one_is_not_drained(self):
        h = self._hook({"pending": 1, "active": 0})
        self.assertFalse(h.drain(0.1))
        self.assertEqual(h.last_error, "")

    def test_empty_or_missing_stats_fail_closed(self):
        for value in ({}, {"active": 0}, {"pending": 0}, None):
            h = self._hook(value)
            self.assertFalse(h.drain(0.05), value)
            self.assertTrue(h.last_error, value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
