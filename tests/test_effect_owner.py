# -*- coding: utf-8 -*-
"""EffectOwner generation/host binding and negative-ack state tests."""
import threading
import time
import unittest
from unittest import mock

from runtime.effect_owner import EffectOwner
from core.effect_proxy import DisplayHookProxy

HOST_A = {"pid": 100, "ctime": 2, "image": "A.exe"}
HOST_B = {"pid": 200, "ctime": 3, "image": "B.exe"}


class FakeHook:
    made = []
    block_ensure = False
    enter = threading.Event()
    release = threading.Event()
    block_stop = False
    stop_enter = threading.Event()
    stop_release = threading.Event()

    def __init__(self):
        self.running_flag = True
        self.paused_flag = False
        self.last_error = ""
        self.pause_calls = 0
        self.stop_calls = 0
        FakeHook.made.append(self)

    def ensure(self, pid, table, real, ui):
        if FakeHook.block_ensure:
            FakeHook.enter.set()
            FakeHook.release.wait(5)
        return True

    def running(self):
        return self.running_flag

    def paused(self):
        return self.paused_flag

    def pause(self, gen=None):
        self.pause_calls += 1
        self.paused_flag = True
        return True

    def resume(self, gen=None):
        self.paused_flag = False
        return True

    def stop(self):
        self.stop_calls += 1
        if FakeHook.block_stop:
            FakeHook.stop_enter.set()
            FakeHook.stop_release.wait(5)
        self.running_flag = False
        return True

    def stats(self):
        return {"edbCalls": 1, "miss": ["x"], "caps": ["y"]}

    def panes(self):
        return 0

    def revert_now(self):
        return 1

    def revert_addrs(self, addrs):
        return {"written": 0}

    def set_real_names(self, names):
        return True

    def invalidate(self):
        return 1


def owner():
    FakeHook.made.clear()
    FakeHook.block_ensure = False
    FakeHook.enter.clear()
    FakeHook.release.clear()
    FakeHook.block_stop = False
    FakeHook.stop_enter.clear()
    FakeHook.stop_release.clear()
    return EffectOwner("tmp", "", check_host=lambda h: h)


class EffectOwnerTest(unittest.TestCase):
    def test_cross_host_cannot_operate_owner(self):
        o = owner()
        with mock.patch("core.live_hook.DisplayHook", FakeHook):
            o.handle({"op": "ensure", "gen": 1, "host": HOST_A,
                      "payload": {"table": {"A": "中"}, "sig": "s"}})
            with self.assertRaises(RuntimeError):
                o.handle({"op": "pause", "gen": 2, "host": HOST_B})
        self.assertEqual(FakeHook.made[0].pause_calls, 0)

    def test_invalid_op_does_not_mutate_generation_or_desire(self):
        o = owner()
        with mock.patch("core.live_hook.DisplayHook", FakeHook):
            o.handle({"op": "ensure", "gen": 1, "host": HOST_A,
                      "payload": {"table": {"A": "中"}, "sig": "s"}})
            before = (o.gen, o.desired)
            with self.assertRaises(RuntimeError):
                o.handle({"op": "bogus", "gen": 99, "host": HOST_A})
            self.assertEqual((o.gen, o.desired), before)

    def test_stale_gen_rejected(self):
        o = owner()
        with mock.patch("core.live_hook.DisplayHook", FakeHook):
            o.handle({"op": "ensure", "gen": 5, "host": HOST_A,
                      "payload": {"table": {"A": "中"}, "sig": "s"}})
            with self.assertRaises(RuntimeError):
                o.handle({"op": "pause", "gen": 4, "host": HOST_A})

    def test_blocked_ensure_then_off_is_stale(self):
        o = owner()
        FakeHook.block_ensure = True
        FakeHook.enter.clear()
        FakeHook.release.clear()
        with mock.patch("core.live_hook.DisplayHook", FakeHook):
            result = {}
            def run():
                try:
                    result["value"] = o.handle({
                        "op": "ensure", "gen": 1, "host": HOST_A,
                        "payload": {"table": {"A": "中"}, "sig": "s"}})
                except Exception as exc:
                    result["error"] = exc
            t = threading.Thread(target=run)
            t.start()
            self.assertTrue(FakeHook.enter.wait(2))
            off = o.handle({"op": "pause", "gen": 2, "host": HOST_A})
            self.assertFalse(off["ok"])
            FakeHook.release.set()
            t.join(3)
            self.assertIn("error", result)
            # After stale ensure is discarded, OFF is acknowledged only when
            # there is no owned hook left.
            done = o.handle({"op": "pause", "gen": 2, "host": HOST_A})
            self.assertTrue(done.get("ok"))
            self.assertTrue(done.get("no-owner"))


    def test_stale_cleanup_blocks_new_ensure_until_stop_done(self):
        o = owner()
        FakeHook.block_ensure = True
        with mock.patch("core.live_hook.DisplayHook", FakeHook):
            def run():
                try:
                    o.handle({"op": "ensure", "gen": 1, "host": HOST_A,
                              "payload": {"table": {"A": "中"}, "sig": "s"}})
                except Exception:
                    pass
            t = threading.Thread(target=run)
            t.start()
            self.assertTrue(FakeHook.enter.wait(2))
            o.handle({"op": "pause", "gen": 2, "host": HOST_A})
            FakeHook.block_stop = True
            FakeHook.stop_enter.clear()
            FakeHook.stop_release.clear()
            FakeHook.release.set()
            self.assertTrue(FakeHook.stop_enter.wait(2))
            with self.assertRaises(RuntimeError):
                o.handle({"op": "ensure", "gen": 3, "host": HOST_A,
                          "payload": {"table": {"A": "中"}, "sig": "s"}})
            FakeHook.stop_release.set()
            t.join(3)
            self.assertFalse(o.attaching)
            self.assertIsNone(o.pending_hook)


class ProxyWireGenTest(unittest.TestCase):
    def test_reopen_maps_local_gen_to_monotonic_wire(self):
        class C:
            def __init__(self):
                self.gens = []
            def call(self, op, payload=None, host=None, gen=0):
                self.gens.append((op, int(gen)))
                return {"running": True, "paused": False}
        c1 = C()
        p1 = DisplayHookProxy("tmp")
        p1._client = c1
        self.assertTrue(p1.ensure(1, {"A": "中"}, host=HOST_A,
                                  sig="a", gen=0))
        g1 = c1.gens[-1][1]
        c2 = C()
        p2 = DisplayHookProxy("tmp")
        p2._client = c2
        self.assertTrue(p2.ensure(1, {"A": "中"}, host=HOST_A,
                                  sig="a", gen=0))
        g2 = c2.gens[-1][1]
        self.assertGreater(g2, g1)
        # An old local intent must be rejected, not silently upgraded to the
        # current wire generation and turned into a fresh pause/ON.
        before = list(c1.gens)
        self.assertFalse(p1.pause(gen=-1))
        self.assertEqual(c1.gens, before)


class ProxyNegativeAckTest(unittest.TestCase):
    def test_negative_pause_resume_do_not_publish_state(self):
        class C:
            def __init__(self, *a, **k):
                pass
            def call(self, op, **k):
                return {"ok": False}
        with mock.patch("core.effect_proxy.BrokerClient", C):
            p = DisplayHookProxy("tmp")
            p._host = HOST_A
            p._running = True
            p._paused = False
            self.assertFalse(p.pause())
            self.assertFalse(p._paused)
            p._paused = True
            self.assertFalse(p.resume())
            self.assertTrue(p._paused)
            self.assertTrue(p._running)


if __name__ == "__main__":
    unittest.main(verbosity=2)
