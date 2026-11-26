# -*- coding: utf-8 -*-
"""Actual ParamDrawHook JS pair behavior (pre/post, pending, wrong RSP, OFF).

Skipped if node is unavailable; the parent's Windows Python host used for the
real App tests has node (probe_draw_lifetime.mjs).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import param_draw_hook as pdh
from runtime import build_profile as bp

AE2025_LIB = (r"C:\Program Files\Adobe\Adobe After Effects 2025"
              r"\Support Files\AfterFXLib.dll")


def _profile():
    if not os.path.exists(AE2025_LIB):
        raise unittest.SkipTest("AE2025 AfterFXLib.dll unavailable")
    return bp.draw_candidate(AE2025_LIB, bp.sha256_file(AE2025_LIB))


class ParamDrawJsTest(unittest.TestCase):
    def test_checkbox_attachment_rolls_back_without_tearing_down_regular_draws(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        harness = Path(__file__).with_name("js") / "checkbox_attach_harness.mjs"
        with tempfile.TemporaryDirectory(prefix="aeloc_checkbox_attach_") as td:
            template = Path(td) / "candidate.js"
            template.write_text(pdh.JS, encoding="utf-8")
            proc = subprocess.run([node, str(harness), str(template)], capture_output=True,
                                  text=True, encoding="utf-8", timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_pre_post_pending_lifetime_and_off(self):
        self._exercise()

    def test_second_site_return_owns_its_pointer(self):
        self._exercise(extra=True)

    def _exercise(self, extra=False):
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        prof = _profile()
        extra_site = {key: prof[key] for key in ("pre_bytes", "call_bytes", "ret_bytes")}
        extra_site.update(pre_rva=0xD03855, call_rva=0xD03866, ret_rva=0xD0386B)
        src = (pdh.JS.replace("__MAP__", json.dumps({"Amount": "强度"}))
               .replace("__EXTRA_SITES__", json.dumps([extra_site] if extra else []))
               .replace("__TEXT__", "null")
               .replace("__PRE_RVA__", hex(prof["pre_rva"]))
               .replace("__CALL_RVA__", hex(prof["call_rva"]))
               .replace("__RET_RVA__", hex(prof["ret_rva"]))
               .replace("__PRE_BYTES__", json.dumps(prof["pre_bytes"]))
               .replace("__CALL_BYTES__", json.dumps(prof["call_bytes"]))
               .replace("__RET_HEX__", json.dumps(prof["ret_bytes"]))
               .replace("__SLOT__", str(int(prof["arg_slot"]))))
        with tempfile.TemporaryDirectory(prefix="aeloc_drawjs_") as td:
            js = os.path.join(td, "candidate.js")
            with open(js, "w", encoding="utf-8") as f:
                f.write(src)
            harness = os.path.join(os.path.dirname(__file__), "js",
                                   "param_pair_harness.mjs")
            try:
                args = [node, "--expose-gc", harness, js]
                if extra:
                    args.append(json.dumps(extra_site))
                proc = subprocess.run(args,
                                      capture_output=True, text=True,
                                      encoding="utf-8", timeout=20)
            except Exception as exc:
                self.fail("node execution failed: %r" % (exc,))
        detail = (proc.stderr or "") + (proc.stdout or "")
        self.assertEqual(proc.returncode, 0, detail)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["probe_hooks"], 4 if extra else 2)
        self.assertEqual(out["object_hooks"], 0)
        self.assertEqual(out["leave_traps"], 0)
        self.assertEqual(out["pre_callback_type"], "function")
        self.assertEqual(out["post_callback_type"], "function")
        self.assertEqual(out["active1"], 1)
        self.assertEqual(out["activeMap"], 1)
        self.assertEqual(out["activeWrong"], 1)
        self.assertEqual(out["activeEnd"], 0)
        self.assertTrue(out["restored"])
        self.assertTrue(out["bSlot"])
        self.assertTrue(out["cSlot"])
        self.assertTrue(out["offPointer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
