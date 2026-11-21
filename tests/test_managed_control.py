# -*- coding: utf-8 -*-
"""ControlState 纯状态机：身份/host/gen/ack，不启动进程。"""
import importlib.util
import os
import unittest


def _load():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "runtime", "managed_guard.py")
    spec = importlib.util.spec_from_file_location("managed_guard_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class HostValidator:
    def __init__(self, hosts):
        self.hosts = hosts

    def __call__(self, h):
        if not isinstance(h, dict):
            return None
        k = (h.get("pid"), h.get("ctime"), h.get("image"))
        return dict(self.hosts[k]) if k in self.hosts else None


class ControlStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def _req(self, gen, host, mode="on", rid="r"):
        return {"protocol": self.m.PROTOCOL, "gen": gen, "request_id": rid,
                "host": host, "mode": mode}

    def _state(self):
        h1 = {"pid": 1, "ctime": 111, "image": "C:\\AE\\AfterFX.exe"}
        h2 = {"pid": 2, "ctime": 222, "image": "C:\\AE\\AfterFX.exe"}
        valid = {("pid",): None}
        hosts = {(h1["pid"], h1["ctime"], h1["image"]): h1,
                 (h2["pid"], h2["ctime"], h2["image"]): h2}
        return self.m.ControlState(HostValidator(hosts)), h1, h2

    def test_forged_or_wrong_host_rejected_without_gen_commit(self):
        st, h1, _ = self._state()
        ok, err, _ = st.handle(self._req(5, {"pid": 9, "ctime": 9,
                                            "image": "C:\\AE\\AfterFX.exe"}))
        self.assertFalse(ok)
        self.assertEqual(err, "host")
        self.assertEqual(st.last_gen, 0)
        self.assertEqual(st.mode, "off")
        ok, err, _ = st.handle(self._req(5, h1))
        self.assertTrue(ok)
        self.assertEqual(st.last_gen, 5)
        self.assertEqual(st.state, "control-acked")
        _st = st.status(1, 1)
        self.assertFalse(_st["language_ready"])
        self.assertFalse(_st["rows_pending"])
        self.assertFalse(_st["background_discovery_pending"])

    def test_language_progress_fields_synced_to_status(self):
        st, _h1, _h2 = self._state()

        class L:
            rows_pending = True
            background_discovery_pending = True

        self.m._copy_language_progress(st, L())
        s = st.status(1, 1)
        self.assertTrue(s["rows_pending"])
        self.assertTrue(s["background_discovery_pending"])
        self.assertFalse(s["language_ready"])

    def test_stale_gen_does_not_change_mode(self):
        st, h1, _ = self._state()
        st.handle(self._req(5, h1, "on"))
        ok, err, _ = st.handle(self._req(4, h1, "off"))
        self.assertFalse(ok)
        self.assertEqual(err, "stale-gen")
        self.assertEqual(st.mode, "on")

    def test_live_other_host_is_busy_but_dead_host_can_be_replaced(self):
        h1 = {"pid": 1, "ctime": 111, "image": "C:\\AE\\AfterFX.exe"}
        h2 = {"pid": 2, "ctime": 222, "image": "C:\\AE\\AfterFX.exe"}
        live = {("pid",): None}
        hosts = {(h1["pid"], h1["ctime"], h1["image"]): h1,
                 (h2["pid"], h2["ctime"], h2["image"]): h2}
        st = self.m.ControlState(HostValidator(hosts))
        st.handle(self._req(5, h1, "on"))
        ok, err, _ = st.handle(self._req(6, h2, "off"))
        self.assertFalse(ok)
        self.assertEqual(err, "host-busy")
        self.assertEqual(st.mode, "on")
        hosts.pop((h1["pid"], h1["ctime"], h1["image"]))
        st.validate = HostValidator(hosts)
        ok, err, _ = st.handle(self._req(7, h2, "off"))
        self.assertTrue(ok)
        self.assertEqual(st.host["pid"], 2)

    def test_stop_needs_no_live_host(self):
        st, _, _ = self._state()
        ok, err, stop = st.handle({"protocol": self.m.PROTOCOL, "gen": 1,
                                   "request_id": "s", "mode": "stop",
                                   "host": {"pid": 9, "ctime": 9,
                                            "image": "AfterFX.exe"}})
        self.assertTrue(ok)
        self.assertTrue(stop)
        self.assertEqual(st.state, "stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
