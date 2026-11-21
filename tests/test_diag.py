"""诊断模块测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.diag import Diagnosis, diagnose, is_placeholder  # noqa: E402


class TestPlaceholder(unittest.TestCase):
    def test_detects_generic_names(self):
        for s in ("Generic Checkbox 1", "Generic Slider 12", "Float 10",
                  "Bool 16", "Floatxy 14 X", "Intxy 4 Y",
                  "Norm Scalexy 15 X", "Mocha Data0"):
            self.assertTrue(is_placeholder(s), s)

    def test_keeps_real_names(self):
        for s in ("Blur Amount", "Screen Colour", "Threshold",
                  "Variation % (Size)", "Ugly in the Dark"):
            self.assertFalse(is_placeholder(s), s)


class TestDiagnose(unittest.TestCase):
    def _write_log(self, lines):
        f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False,
                                        encoding="utf-8")
        f.write("\n".join(lines))
        f.close()
        self.addCleanup(lambda: os.unlink(f.name))
        return f.name

    def test_counts_writes(self):
        p = self._write_log([
            "  [写入] A -> 甲   读回=(bytes ok 1)",
            "  [写入] B -> 乙   读回=None",
        ])
        d = diagnose(p)
        self.assertEqual(d.writes, 2)
        self.assertEqual(d.verified, 1)
        self.assertEqual(d.readback_fail, 1)

    def test_collects_missing(self):
        p = self._write_log([
            "  [未命中] Screen Colour           (ptr=0x123)",
            "  [未命中] Generic Checkbox 5       (ptr=0x456)",
        ])
        d = diagnose(p)
        self.assertEqual(d.missing, 2)
        self.assertEqual(d.placeholder, 1)
        self.assertIn("Screen Colour", d.missing_real)

    def test_layout_flag(self):
        p = self._write_log([
            "  [布局确定] ANSI 名字偏移 = ret+0x40",
        ])
        self.assertTrue(diagnose(p).layout_ok)

    def test_missing_log(self):
        d = diagnose("Z:/nope/none.log")
        self.assertEqual(d.writes, 0)

    def test_advice_when_no_layout(self):
        p = self._write_log(["  [写入] A -> 甲   读回=None"])
        d = diagnose(p)
        text = " ".join(d.advice())
        self.assertIn("布局", text)

    def test_advice_lists_missing(self):
        p = self._write_log(["  [未命中] Screen Colour           (ptr=0x1)"])
        d = diagnose(p)
        text = " ".join(d.advice())
        self.assertIn("Screen Colour", text)

    def test_success_rate(self):
        d = Diagnosis(writes=10, verified=3)
        self.assertAlmostEqual(d.success_rate, 0.3)

    def test_success_rate_zero(self):
        self.assertEqual(Diagnosis().success_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
