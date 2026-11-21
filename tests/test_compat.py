"""插件兼容性检测测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.compat import (  # noqa: E402
    DISABLED_SUFFIX, CompatIssue, check_plugin_dir, disable, enable,
    summarize,
)


class TestCheckPluginDir(unittest.TestCase):
    def test_finds_unlicensed_vendor(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "Digital Anarchy", "Flicker Free"))
            issues = check_plugin_dir(td)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].vendor, "Digital Anarchy")
            self.assertEqual(issues[0].level, "block")

    def test_keeps_vendor_warning_when_license_file_is_present(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "Digital Anarchy")
            os.makedirs(d)
            with open(os.path.join(d, "license.dat"), "w") as f:
                f.write("x")
            issues = check_plugin_dir(td)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].level, "block")
            self.assertIn("序列号对话框", issues[0].reason)

    def test_marks_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "Digital Anarchy" + DISABLED_SUFFIX))
            issues = check_plugin_dir(td)
            self.assertEqual(len(issues), 1)
            self.assertTrue(issues[0].disabled)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(check_plugin_dir(td), [])

    def test_missing_dir(self):
        self.assertEqual(check_plugin_dir("Z:/nope"), [])

    def test_ignores_other_vendors(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "BorisFX"))
            os.makedirs(os.path.join(td, "Sapphire"))
            self.assertEqual(check_plugin_dir(td), [])


class TestSummarize(unittest.TestCase):
    def test_empty(self):
        self.assertIn("没有", summarize([]))

    def test_block(self):
        # 2026-10-29 起崩溃源(磁盘汉化版 BCC)已消除,文案降级为"建议停用"
        i = CompatIssue(path="/x", vendor="Digital Anarchy", level="block")
        self.assertIn("未授权", summarize([i]))

    def test_all_disabled(self):
        i = CompatIssue(path="/x", vendor="Digital Anarchy", disabled=True)
        self.assertIn("已停用", summarize([i]))


class TestDisableEnable(unittest.TestCase):
    def test_disable_renames(self):
        # ★ 2026-10-21：停用改为【移到 MediaCore.disabled/】，
        #   因为只加 .disabled 后缀 AE 仍会扫描并加载（实测）。
        with tempfile.TemporaryDirectory() as td:
            mc = os.path.join(td, "MediaCore")
            os.makedirs(mc)
            d = os.path.join(mc, "Digital Anarchy")
            os.makedirs(d)
            issue = CompatIssue(path=d, vendor="Digital Anarchy")
            ok, msg = disable(issue)
            self.assertTrue(ok, msg)
            self.assertFalse(os.path.exists(d))
            moved = os.path.join(td, "MediaCore" + DISABLED_SUFFIX, "Digital Anarchy")
            self.assertTrue(os.path.exists(moved), msg)

    def test_enable_restores(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "Digital Anarchy" + DISABLED_SUFFIX)
            os.makedirs(d)
            issue = CompatIssue(path=d, vendor="Digital Anarchy", disabled=True)
            ok, msg = enable(issue)
            self.assertTrue(ok, msg)
            self.assertTrue(os.path.exists(os.path.join(td, "Digital Anarchy")))

    def test_disable_idempotent(self):
        issue = CompatIssue(path="/x", vendor="V", disabled=True)
        ok, _ = disable(issue)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
