"""引擎管理层测试（不真的启动 AE / 不真的注入）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import engine  # noqa: E402


class TestNeedsFixer(unittest.TestCase):
    def test_2026_needs(self):
        self.assertTrue(engine.needs_fixer("2026"))

    def test_2025_does_not(self):
        self.assertFalse(engine.needs_fixer("2025"))

    def test_2021_does_not(self):
        self.assertFalse(engine.needs_fixer("2021"))

    def test_garbage_does_not(self):
        self.assertFalse(engine.needs_fixer(""))
        self.assertFalse(engine.needs_fixer("abc"))


class TestFindGuard(unittest.TestCase):
    def test_found_when_exists(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "guard_uni.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# stub")
            with mock.patch.object(engine, "GUARD_SRC_CANDIDATES", [p]):
                path, reason = engine.find_guard()
                self.assertEqual(path, p)
                self.assertEqual(reason, "")

    def test_missing(self):
        with mock.patch.object(engine, "GUARD_SRC_CANDIDATES", ["Z:/nope.py"]):
            path, reason = engine.find_guard()
            self.assertEqual(path, "")
            self.assertIn("未找到", reason)


class TestFindFixer(unittest.TestCase):
    def test_present(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "Support Files", "Plug-ins")
            os.makedirs(d)
            p = os.path.join(d, engine.FIXER_NAME)
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            path, size = engine.find_fixer(td)
            self.assertEqual(path, p)
            self.assertEqual(size, 100)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(engine.find_fixer(td), ("", 0))


class TestStartEngineGuards(unittest.TestCase):
    def test_missing_guard_rejected(self):
        ok, msg = engine.start_engine("Z:/nope.py", "Z:/nodict")
        self.assertFalse(ok)
        self.assertIn("引擎不存在", msg)

    def test_missing_dict_dir_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "g.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
            ok, msg = engine.start_engine(p, os.path.join(td, "nodict"))
            self.assertFalse(ok)
            self.assertIn("词典目录不存在", msg)


class TestBuildEngineCommand(unittest.TestCase):
    def test_shape(self):
        cmd = engine.build_engine_command("g.py", "d", hold_seconds=30)
        self.assertEqual(cmd[1], "g.py")
        self.assertEqual(cmd[2], "apply")
        self.assertEqual(cmd[3], "30")


class TestPrepareDict(unittest.TestCase):
    def test_filters_by_selection(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(engine, "ENGINE_DICT_DIR", td):
                res = engine.prepare_dict(
                    {"Blur": "模糊", "Amount": "数量"},
                    attribution={"Blur": ["g1"]},
                    selected_groups={"g1"},
                    keep_unowned=False,
                )
                self.assertEqual(res.entries, 1)
                self.assertEqual(res.dropped, 1)
                self.assertEqual(engine.check().engine_dict_entries, 1)


class TestCheck(unittest.TestCase):
    def test_check_does_not_raise_with_bad_root(self):
        st = engine.check("Z:/nope", "2026")
        self.assertFalse(st.fixer_installed)
        self.assertTrue(st.fixer_needed)
