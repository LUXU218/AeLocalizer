# -*- coding: utf-8 -*-
"""managed runtime 模板部署：原子 hash、损坏修复、模板升级。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import guardruntime


class GuardEntryTest(unittest.TestCase):
    def test_run_guard_can_import_modules_next_to_the_deployed_script(self):
        with tempfile.TemporaryDirectory(prefix="aeloc_guard_entry_") as tmp:
            runtime = Path(tmp, "deployed guard")
            runtime.mkdir()
            Path(runtime, "guard_entry_dependency.py").write_text(
                "EXPECTED_EXIT = 19\n", encoding="utf-8")
            guard = Path(runtime, "guard_uni.py")
            guard.write_text(
                "import sys\n"
                "assert 'PySide6.QtQuick' not in sys.modules\n"
                "from guard_entry_dependency import EXPECTED_EXIT\n"
                "raise SystemExit(EXPECTED_EXIT)\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "app.main", "--run-guard", str(guard)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                text=True, timeout=30)
        self.assertEqual(result.returncode, 19, result.stderr)


class GuardRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_guardruntime_")
        self.tpl = os.path.join(self.tmp, "managed_guard.py")
        Path(self.tpl).write_text("print('managed')\n", encoding="utf-8")
        for n in ("build_profile.py", "param_draw_hook.py", "popup_session.py",
                  "effect_owner.py"):
            Path(os.path.join(self.tmp, n)).write_text("# dep\n", encoding="utf-8")
        self.p = mock.patch.object(guardruntime, "TEMPLATE", self.tpl)
        self.p.start()
        self.addCleanup(self.p.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deploy_template_and_pin_file_hashes(self):
        data = os.path.join(self.tmp, "data")
        m = guardruntime.ensure_runtime(data)
        self.assertTrue(os.path.isfile(guardruntime.runtime_guard(data)))
        self.assertEqual(m["source"], "project-template")
        self.assertEqual(m["protocol_version"], guardruntime.PROTOCOL_VERSION)
        self.assertIn("guard_uni.py", m["files"])
        m2 = guardruntime.ensure_runtime(data)
        self.assertEqual(m["files"], m2["files"])

    def test_unchanged_fastpath_does_not_rewrite(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        files = [guardruntime.runtime_guard(data)]
        files.append(os.path.join(guardruntime.runtime_dir(data),
                                  "manifest.json"))
        before = {p: os.stat(p).st_mtime_ns for p in files}
        guardruntime.ensure_runtime(data)
        after = {p: os.stat(p).st_mtime_ns for p in files}
        self.assertEqual(before, after)

    def test_same_template_corrupt_runtime_repairs(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        rt = guardruntime.runtime_guard(data)
        Path(rt).write_text("corrupt\n", encoding="utf-8")
        guardruntime.ensure_runtime(data)
        self.assertEqual(Path(rt).read_text(encoding="utf-8"), "print('managed')\n")

    def test_deploy_repair_preserves_control_lock_and_logs(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        rdir = guardruntime.runtime_dir(data)
        for rel, text in (("control/request.json", '{"x":1}'),
                          ("out/audit.log", "audit"),
                          ("out/guard.lock", "123"),
                          ("tmp/keep", "tmp")):
            p = os.path.join(rdir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Path(p).write_text(text, encoding="utf-8")
        Path(guardruntime.runtime_guard(data)).write_text("broken", encoding="utf-8")
        guardruntime.ensure_runtime(data)
        for rel, text in (("control/request.json", '{"x":1}'),
                          ("out/audit.log", "audit"),
                          ("out/guard.lock", "123"),
                          ("tmp/keep", "tmp")):
            self.assertEqual(Path(os.path.join(rdir, rel)).read_text(
                encoding="utf-8"), text)

    def test_dependency_change_redeploys_staged_file(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        dep_src = os.path.join(self.tmp, "param_draw_hook.py")
        Path(dep_src).write_text("# changed dep\n", encoding="utf-8")
        guardruntime.ensure_runtime(data)
        staged = Path(os.path.join(guardruntime.runtime_dir(data),
                                   "param_draw_hook.py")).read_text(encoding="utf-8")
        self.assertEqual(staged, "# changed dep\n")

    def test_template_change_requires_force(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        Path(self.tpl).write_text("print('v2')\n", encoding="utf-8")
        with self.assertRaises(guardruntime.RuntimeUpgradeRequired):
            guardruntime.ensure_runtime(data)
        m = guardruntime.ensure_runtime(data, force=True)
        self.assertIn("guard_uni.py", m["files"])

    def test_can_auto_upgrade_only_for_own_template(self):
        data = os.path.join(self.tmp, "data")
        self.assertFalse(guardruntime.can_auto_upgrade(data))
        guardruntime.ensure_runtime(data)
        self.assertTrue(guardruntime.can_auto_upgrade(data))
        mpath = os.path.join(guardruntime.runtime_dir(data), "manifest.json")
        man = json.loads(Path(mpath).read_text(encoding="utf-8"))
        man["source"] = "external-guard"
        Path(mpath).write_text(json.dumps(man), encoding="utf-8")
        self.assertFalse(guardruntime.can_auto_upgrade(data))

    def test_external_source_rejected(self):
        ext = os.path.join(self.tmp, "external.py")
        Path(ext).write_text("print('external')\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            guardruntime.ensure_runtime(os.path.join(self.tmp, "data"), ext)


if __name__ == "__main__":
    unittest.main(verbosity=2)
