"""UTF-8 修复层测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.fixer import MIN_VERSION_NEEDING_FIXER, detect  # noqa: E402


def make_ae(root: str, with_fixer: bool) -> str:
    d = os.path.join(root, "Support Files", "Plug-ins")
    os.makedirs(d, exist_ok=True)
    if with_fixer:
        with open(os.path.join(d, "00_UTF8_Fixer.aex"), "wb") as f:
            f.write(b"fake")
    return root


class TestFixerDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _root(self, name, with_fixer):
        return make_ae(os.path.join(self.tmp, name), with_fixer)

    def test_2026_without_fixer_needs_it(self):
        st = detect(self._root("ae2026a", False), "2026")
        self.assertTrue(st.needs_fixer)
        self.assertFalse(st.installed)
        self.assertFalse(st.ok)
        self.assertIn("乱码", st.note)

    def test_2026_with_fixer_ok(self):
        st = detect(self._root("ae2026b", True), "2026")
        self.assertTrue(st.needs_fixer)
        self.assertTrue(st.installed)
        self.assertTrue(st.ok)
        self.assertGreater(st.size, 0)

    def test_2025_never_needs_fixer(self):
        st = detect(self._root("ae2025", False), "2025")
        self.assertFalse(st.needs_fixer)
        self.assertTrue(st.ok)          # 不需要 -> 状态算 OK
        self.assertIn("GBK", st.note)

    def test_unknown_version(self):
        st = detect(self._root("aeunk", False), "")
        self.assertFalse(st.needs_fixer)

    def test_missing_plugin_dir(self):
        st = detect(os.path.join(self.tmp, "nope"), "2026")
        self.assertFalse(st.installed)
        self.assertTrue(st.needs_fixer)

    def test_threshold_constant(self):
        self.assertEqual(MIN_VERSION_NEEDING_FIXER, 2026)


if __name__ == "__main__":
    unittest.main(verbosity=2)
