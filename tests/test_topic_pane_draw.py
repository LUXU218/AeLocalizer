import os
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import build_profile as bp


class TopicPaneDrawTest(unittest.TestCase):
    def test_single_topic_pane_beats_similar_non_pane_draw(self):
        path = Path(r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFXLib.dll")
        if not path.is_file():
            self.skipTest("AE 2025 reference build unavailable")
        with patch.object(bp, "MIN_MARGIN", 0.30):
            profile = bp.draw_candidate(path, allow_unknown=True)
        self.assertEqual(profile["draw_family"], "topic-pane-single")
        self.assertEqual(profile["call_rva"], 0xA93EB5)
        self.assertEqual(profile["additional_sites"], [])

    def test_real_2025_minor_build_distinguishes_close_candidates(self):
        root = os.environ.get("AELOC_TEST_MODERN_MINOR_DIR")
        if not root:
            self.skipTest("set AELOC_TEST_MODERN_MINOR_DIR to the supplied 25.6.4.3 module directory")
        dll = Path(root) / "AfterFXLib.dll"
        profile = bp.draw_candidate(dll, allow_unknown=True)
        self.assertEqual(profile["draw_family"], "topic-pane-single")
        self.assertEqual(profile["call_rva"], 0xAB2B75)
        self.assertEqual((profile["confidence"], profile["runner_up"]), (0.934, 0.757))
        self.assertEqual(profile["additional_sites"], [])

    def test_real_minor_build_validates_both_topic_panes(self):
        value = os.environ.get("AELOC_TEST_MINOR_DRAW_LIB")
        if not value:
            self.skipTest("set AELOC_TEST_MINOR_DRAW_LIB to an AE 24.6.2.2 AfterFXLib.dll")
        path = Path(value)
        profile = bp.draw_candidate(path, allow_unknown=True)
        self.assertEqual(profile["draw_family"], "topic-pane-pair")
        self.assertEqual({profile["call_rva"], *[s["call_rva"] for s in profile["additional_sites"]]},
                         {0xCC65C6, 0xD03866})
        self.assertEqual(len(profile["additional_sites"]), 1)
        with self.assertRaises(bp.BuildMismatch):
            bp.draw_candidate(path)

        image = bp.PEImage(path)
        # Change a valid instruction operand in just one function prologue,
        # before the scoring window. The equal local scores alone are insufficient.
        changed = bytearray(image.raw)
        changed[image.raw_offset(0xCC64B4)] ^= 8
        image.raw = bytes(changed)
        with patch.object(bp, "PEImage", return_value=image):
            with self.assertRaisesRegex(bp.BuildMismatch, "topic-pane function bodies disagree"):
                bp.draw_candidate(path, allow_unknown=True)
