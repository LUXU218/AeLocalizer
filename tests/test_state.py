"""汉化状态检测测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.state import CJK_RE, detect, should_skip  # noqa: E402


class TestDetect(unittest.TestCase):
    def test_missing_file(self):
        st = detect("Z:/nope.aex")
        self.assertFalse(st.localized)
        self.assertIn("不存在", st.reason)

    def test_plain_file_not_localized(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.aex")
            with open(p, "wb") as f:
                f.write(b"Blur\x00Amount\x00Opacity\x00")
            st = detect(p, {"Blur": "模糊", "Amount": "数量"})
            self.assertFalse(st.localized)

    def test_fingerprint_hits(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.dll")
            with open(p, "wb") as f:
                f.write("模糊".encode("gbk"))
                f.write("数量".encode("gbk"))
                f.write("透明".encode("gbk"))
            st = detect(p, {"Blur": "模糊", "Amount": "数量", "Opacity": "透明"})
            self.assertTrue(st.localized)
            self.assertGreaterEqual(st.hits, 3)

    def test_below_threshold_not_localized(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.dll")
            with open(p, "wb") as f:
                f.write("模糊".encode("gbk"))
            st = detect(p, {"Blur": "模糊", "Amount": "数量", "Opacity": "透明"})
            self.assertFalse(st.localized)

    def test_no_entries_only_pipl(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.aex")
            with open(p, "wb") as f:
                f.write(b"Blur\x00")
            st = detect(p)
            self.assertFalse(st.localized)
            self.assertIn("无词典", st.reason)

    def test_should_skip_shape(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.aex")
            with open(p, "wb") as f:
                f.write(b"x")
            skip, reason = should_skip(p, {})
            self.assertIsInstance(skip, bool)
            self.assertIsInstance(reason, str)


class TestCjkRegex(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(CJK_RE.search("模糊"))
        self.assertTrue(CJK_RE.search("Blur 模糊"))

    def test_not_matches(self):
        self.assertFalse(CJK_RE.search("Blur Amount"))
        self.assertFalse(CJK_RE.search("12345"))


if __name__ == "__main__":
    unittest.main()
