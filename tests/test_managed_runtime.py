# -*- coding: utf-8 -*-
"""真实 managed runtime 进程：只验拒绝/互斥/半写/STOP，不伪造成正向 AE。"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


class ManagedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_mgruntime_")
        self.env = dict(os.environ)
        self.env["AELOC_DATA_ROOT"] = self.tmp
        self.env["AELOC_ENGINE_DICT"] = os.path.join(self.tmp, "engine_dict")
        self.env["PYTHONIOENCODING"] = "utf-8"
        src = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "runtime", "managed_guard.py")
        self.src = src
        self.proc = subprocess.Popen(
            [sys.executable, src, "apply", "60"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ctl = os.path.join(self.tmp, "guard_runtime", "control")
        self.st = os.path.join(self.ctl, "status.json")

    def tearDown(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait(self, pred, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                with open(self.st, encoding="utf-8") as f:
                    st = json.load(f)
                if pred(st):
                    return st
            except Exception:
                pass
            time.sleep(0.05)
        return None

    def _req(self, gen, mode, host):
        os.makedirs(self.ctl, exist_ok=True)
        obj = {"protocol": "aeloc-managed-guard/1", "gen": gen,
               "request_id": "r%d" % gen, "host": host, "mode": mode}
        tmp = os.path.join(self.ctl, "request.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, os.path.join(self.ctl, "request.json"))

    def test_forged_host_rejected_and_second_process_exits(self):
        self.assertIsNotNone(self._wait(lambda s: s.get("control_ready")))
        self._req(1, "on", {"pid": os.getpid(), "ctime": 0,
                            "image": "C:\\Fake\\AfterFX.exe"})
        st = self._wait(lambda s: s.get("state") == "rejected")
        self.assertIsNotNone(st)
        self.assertEqual(st.get("mode"), "off")
        self.assertFalse(st.get("language_ready"))
        p2 = subprocess.Popen([sys.executable, self.src, "apply", "60"],
                              env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        self.assertNotEqual(p2.wait(timeout=3), 0)

    def test_half_write_ignored_and_stop_without_host(self):
        self.assertIsNotNone(self._wait(lambda s: s.get("control_ready")))
        with open(os.path.join(self.ctl, "request.json.tmp"), "w") as f:
            f.write("{half")
        time.sleep(0.25)
        self._req(1, "stop", {"pid": 0, "ctime": 0, "image": ""})
        self.assertTrue(self.proc.wait(timeout=3) == 0)

    def test_source_has_no_legacy_guard_import_or_frida(self):
        import ast
        tree = ast.parse(open(self.src, encoding="utf-8").read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertFalse(mods & {"frida", "guard_uni", "WIN"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
