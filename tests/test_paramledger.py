# -*- coding: utf-8 -*-
"""typed param baseline：只捕获英文，不猜测中文。"""
import os
import tempfile
import unittest

from core.paramledger import ParamBaseline


class ParamBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_paramledger_")
        self.b = ParamBaseline(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, effect="BCC3Unsharp Mask", param="BCC3-1", name="Host Layer"):
        return {"effectMatchName": effect, "matchName": param,
                "name": name, "pfName": name, "guid": "00" * 16}

    def test_capture_english_and_skip_chinese(self):
        r = self.b.capture_rows([self._row(), self._row(param="BCC3-2", name="宿主图层")])
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["skipped_cjk"], 1)
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-1"),
                         "Host Layer")
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-2"), "")
        self.assertIn("Host Layer", self.b.param_names())

    def test_original_whitespace_is_preserved(self):
        row = self._row(name="       Amount")
        self.b.capture_rows([row])
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-1"),
                         "       Amount")
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-1", "Amount"), "")

    def test_identity_change_drops_old_entries(self):
        self.b.set_identity({"version": "P1", "flt": "a", "tdb": "b"})
        self.b.capture_rows([self._row()])
        self.b.set_identity({"version": "P2", "flt": "a", "tdb": "b"})
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-1"), "")

    def test_chinese_unknown_is_deduplicated(self):
        rows = [self._row(param="p%d" % i, name="宿主图层") for i in range(20)]
        self.b.capture_rows(rows)
        self.b.capture_rows(rows)
        self.assertLessEqual(len(self.b.unknown), 20)

    def test_chinese_never_overwrites_existing_english(self):
        self.b.capture_rows([self._row()])
        r = self.b.capture_rows([self._row(name="宿主图层")])
        self.assertEqual(r["added"], 0)
        self.assertEqual(self.b.english_for("BCC3Unsharp Mask", "BCC3-1"),
                         "Host Layer")

    def test_identity_and_reload(self):
        self.b.set_identity({"version": "25.5", "flt": "a", "tdb": "b"})
        self.b.capture_rows([self._row()])
        self.b.mark_chinese("BCC3Unsharp Mask", "BCC3-1", "宿主图层")
        self.assertEqual(
            self.b.english_for("BCC3Unsharp Mask", "BCC3-1", "宿主图层"),
            "Host Layer")
        self.assertEqual(
            self.b.english_for("BCC3Unsharp Mask", "BCC3-1", "别的名字"), "")
        p = ParamBaseline(self.tmp)
        self.assertTrue(p.identity_ok({"version": "25.5", "flt": "a", "tdb": "b"}))
        self.assertFalse(p.identity_ok({"version": "25.5", "flt": "x", "tdb": "b"}))
        self.assertEqual(p.english_for("BCC3Unsharp Mask", "BCC3-1"), "Host Layer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
