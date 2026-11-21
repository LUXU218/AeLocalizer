"""core.protect 黑名单/packed 清单 + paramnative.split_packed 的离线测试。"""
import unittest

from core.paramnative import split_packed
from core.protect import blocked, packed


class TestProtect(unittest.TestCase):
    def test_newton_blocked(self):
        self.assertTrue(blocked(r"C:\x\Plug-ins\Newton.aex"))
        self.assertIsNone(blocked(r"C:\x\Plug-ins\Bender.aex"))

    def test_packed_hits_evidence_list(self):
        # ae-i18n 1.1.0 实测 cross-target / 写名字即 addProperty 失败的家族
        for p in (r"C:\Plug-ins\DeepGlow.aex",
                  r"C:\Plug-ins\CSpice Glitchify.aex",
                  r"C:\Program Files\Adobe\Common\Plug-ins\MovieCore\Cineware_AE_Effect.aex",
                  r"C:\Plug-ins\MochaAE.aex",
                  r"C:\Plug-ins\RG Universe 4.aex"):
            self.assertTrue(packed(p), p)

    def test_packed_never_hits_normal(self):
        for p in (r"C:\Plug-ins\Bender.aex",
                  r"C:\Plug-ins\BCC Continuum.dll",
                  r"C:\Plug-ins\Universe\..\DLBS.aex"):
            self.assertIsNone(packed(p), p)

    def test_packed_tolerates_empty(self):
        self.assertIsNone(packed(""))
        self.assertIsNone(packed(None))


class TestSplitPacked(unittest.TestCase):
    OPS = [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_split_by_module(self):
        mod = {1: r"C:\Plug-ins\DeepGlow.aex", 2: r"C:\Plug-ins\Bender.aex"}
        allowed, skipped = split_packed(
            self.OPS, lambda op: mod.get(op["a"]))
        self.assertEqual(allowed, [{"a": 2}, {"a": 3}])
        self.assertEqual(skipped, [{"a": 1}])

    def test_unknown_module_allowed(self):
        allowed, skipped = split_packed(self.OPS, lambda op: None)
        self.assertEqual(len(allowed), 3)
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
