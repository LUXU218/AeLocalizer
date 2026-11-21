# -*- coding: utf-8 -*-
"""Authenticated AF_PIPE broker unit lifecycle (no Frida/native process)."""
import json
import os
import tempfile
import time
import unittest

from core.effect_broker import BrokerClient, BrokerServer, info_path


class BrokerTest(unittest.TestCase):
    def _server(self, handler):
        tmp = tempfile.mkdtemp(prefix="aeloc_broker_")
        s = BrokerServer(tmp, handler)
        self.assertTrue(s.start())
        return tmp, s

    def test_roundtrip_and_correlation(self):
        seen = []

        def handler(req):
            seen.append(req["request_id"])
            return {"echo": req["op"]}

        tmp, s = self._server(handler)
        try:
            c = BrokerClient(tmp, timeout=2)
            self.assertEqual(c.call("ping"), {"echo": "ping"})
            self.assertEqual(len(seen), 1)
            info = json.load(open(info_path(tmp), encoding="utf-8"))
            self.assertGreater(int(info.get("ctime") or 0), 0)
        finally:
            s.stop()
        self.assertFalse(os.path.exists(info_path(tmp)))

    def test_timeout_and_stale_info(self):
        def handler(req):
            time.sleep(0.3)
            return True

        tmp, s = self._server(handler)
        try:
            c = BrokerClient(tmp, timeout=0.05)
            with self.assertRaises(TimeoutError):
                c.call("slow")
        finally:
            s.stop()
        with self.assertRaises(Exception):
            BrokerClient(tmp, timeout=0.05).call("ping")

    def test_stop_waits_for_accepted_handler(self):
        import threading

        entered = threading.Event()
        release = threading.Event()

        def handler(req):
            entered.set()
            release.wait(3)
            return True

        tmp, s = self._server(handler)
        try:
            threading.Thread(
                target=lambda: BrokerClient(tmp, timeout=0.2).call("slow"),
                daemon=True).start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(s.stop(0.1))
            release.set()
            self.assertTrue(s.quiesced(2))
        finally:
            release.set()
            s.stop(1)

    def test_single_inflight_after_timeout(self):
        import threading

        entered = threading.Event()
        release = threading.Event()

        def handler(req):
            entered.set()
            release.wait(3)
            return True

        tmp, s = self._server(handler)
        c = BrokerClient(tmp, timeout=0.05)
        try:
            result = {}
            def first():
                try:
                    c.call("slow")
                except Exception as exc:
                    result["error"] = exc
            t = threading.Thread(target=first)
            t.start()
            self.assertTrue(entered.wait(2))
            with self.assertRaises(RuntimeError) as ctx:
                c.call("second")
            self.assertIn("busy", str(ctx.exception))
            release.set()
            t.join(3)
        finally:
            release.set()
            s.stop(1)

    def test_slow_auth_never_sends_after_deadline(self):
        import threading
        from unittest import mock

        seen = []

        def handler(req):
            seen.append(req["op"])
            return True

        tmp, s = self._server(handler)
        release = threading.Event()

        class SlowClient:
            def __init__(self, *a, **k):
                release.wait(1.0)

            def send(self, *a, **k):
                seen.append("SENT")

            def poll(self, *a, **k):
                return False

            def close(self):
                pass

        try:
            with mock.patch("core.effect_broker.Client", SlowClient):
                with self.assertRaises(TimeoutError):
                    BrokerClient(tmp, timeout=0.05).call("late-on")
            release.set()
            time.sleep(0.2)
            self.assertNotIn("SENT", seen)
            self.assertNotIn("late-on", seen)
        finally:
            release.set()
            s.stop(1)

    def test_cross_generation_new_token(self):
        tmp, s1 = self._server(lambda req: {"gen": 1})
        a1 = json.load(open(info_path(tmp), encoding="utf-8"))
        s1.stop()
        tmp2, s2 = self._server(lambda req: {"gen": 2})
        try:
            a2 = json.load(open(info_path(tmp2), encoding="utf-8"))
            self.assertNotEqual(a1["token"], a2["token"])
            self.assertEqual(BrokerClient(tmp2, timeout=2).call("ping"),
                             {"gen": 2})
        finally:
            s2.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
