# -*- coding: utf-8 -*-
"""main-level STOP must wait for native drain, not just close() False."""
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path


def _load():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "runtime", "managed_guard.py")
    spec = importlib.util.spec_from_file_location("managed_stop_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class RuntimeStopTest(unittest.TestCase):
    def test_stop_waits_until_native_drain_safe(self):
        m = _load()
        safe = threading.Event()
        entered = threading.Event()
        calls = []

        class Language:
            def __init__(self, *a):
                self.host = None
                self.draw = None
                self.popup = None
                self.mapping = {}

            def popup_activity(self, mode):
                return None

            def close(self):
                entered.set()
                calls.append(safe.is_set())
                return safe.is_set()

        with tempfile.TemporaryDirectory(prefix="aeloc_stop_") as td:
            root = Path(td)
            out = root / "out"
            ctl = root / "control"
            ctl.mkdir()
            vals = {"DATA_ROOT": root, "RUNTIME_DIR": root, "OUT": out,
                    "CTL": ctl, "TMP": root / "tmp", "DICT": root / "dict",
                    "STOP_FLAG": root / "dict" / "STOP_NOW.flag",
                    "LOG": out / "g.log", "LOCK": out / "lock",
                    "LOCK_ID": out / "lock.json",
                    "REQ": ctl / "request.json", "ST": ctl / "status.json"}
            for k, v in vals.items():
                setattr(m, k, str(v))
            m._mutex = lambda root: 1
            m._proc_identity = lambda pid: {"pid": pid, "ctime": 1,
                                            "image": r"C:\test\python.exe"}
            m.LanguageEngine = Language
            (ctl / "request.json").write_text(json.dumps({
                "protocol": m.PROTOCOL, "mode": "stop", "gen": 1,
                "request_id": "stop-active"}), encoding="utf-8")
            result = {}

            def worker():
                result["exit"] = m.main([])

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.2)
            self.assertTrue(t.is_alive(), "runtime exited before native drain safe")
            safe.set()
            t.join(3)
            self.assertFalse(t.is_alive())
            self.assertEqual(result.get("exit"), 0)
            self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
