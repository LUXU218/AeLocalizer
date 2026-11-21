# -*- coding: utf-8 -*-
"""Stage-2 native boundary: per-build parameter callsite + popup type/offset.

The old single-build constants are gone; this test pins the *evidence-based*
boundary instead: a stable EDB_DrawString signature hash, a structural code
template, the RTTI popup class and the verified field offset.  Exact RVAs are
asserted only when the real AE2025 binaries are installed (historical values).
"""
import os
import unittest

from runtime import build_profile as bp
from runtime import param_draw_hook as pdh
from runtime import popup_session as pps

AE2025 = r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files"
AE2025_LIB = os.path.join(AE2025, "AfterFXLib.dll")
AE2025_FLT = os.path.join(AE2025, "flt.dll")


class Stage2BoundaryTest(unittest.TestCase):
    def test_draw_hook_has_no_single_build_constants(self):
        for name in ("AFTERFXLIB_SHA", "PRE_RVA", "CALL_RVA", "RET_RVA",
                     "PRE_BYTES", "CALL_BYTES", "RET_PREFIX"):
            self.assertFalse(hasattr(pdh, name), name)
        # The JS placeholder must be injected per build, not hardcoded.
        self.assertIn("__SLOT__", pdh.JS)
        self.assertIn("rsp.add(SLOT)", pdh.JS)
        self.assertIn("PENDING", pdh.JS)
        self.assertIn("callIntact", pdh.JS)
        self.assertIn("Memory.alloc", pdh.JS)
        self.assertNotIn("0xa90c3f", pdh.JS.lower())
        self.assertIn("__PRE_RVA__", pdh.JS)

    def test_build_profile_stable_evidence(self):
        self.assertEqual(
            bp.EDB_SIGNATURE_SHA256,
            "95bb916b50c14d4d653acc81e4f856b20c90557beb3cd5ef2e522607a34e896a")
        self.assertEqual(len(bp.DRAW_ANCHOR), 304)
        self.assertEqual(bp.DRAW_EXPORT, b"EDB_DrawString")
        self.assertEqual(bp.DRAW_ARG_SLOT, 0x28)
        self.assertGreaterEqual(bp.MIN_SCORE, 0.7)

    def test_popup_profile_reads_rtti_not_hardcoded_sha(self):
        self.assertFalse(hasattr(pps, "FLT_SHA"))
        self.assertEqual(pps.POPUP_FIELD_OFFSET, 0x70)
        self.assertEqual(bp.POPUP_FIELD_OFFSET, 0x70)
        with open(pps.__file__, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("FLT hash mismatch", text)
        self.assertIn("popup_profile", text)
        self.assertNotIn("0x500", text)

    @unittest.skipUnless(os.path.exists(AE2025_LIB)
                         and os.path.exists(AE2025_FLT),
                         "AE2025 binaries unavailable")
    def test_ae2025_derivation_matches_historical_acceptance(self):
        draw = bp.draw_candidate(AE2025_LIB, bp.sha256_file(AE2025_LIB))
        self.assertEqual(draw["known_version"], "25.5.0.4")
        self.assertEqual(draw["pre_rva"], 0xA93EA4)
        self.assertEqual(draw["call_rva"], 0xA93EB5)
        self.assertEqual(draw["ret_rva"], 0xA93EBA)
        self.assertEqual(draw["pre_bytes"], "c5fa11442420")
        self.assertEqual(draw["ret_bytes"], "90488b9424800000")
        self.assertEqual(draw["arg_slot"], 0x28)
        pop = bp.popup_profile(AE2025_FLT, bp.sha256_file(AE2025_FLT))
        self.assertEqual(pop["known_version"], "25.5.0.4")
        self.assertEqual(pop["vt_rva"], 0xCDC10)
        self.assertEqual(pps.POPUP_VT_RVA, 0xCDC10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
