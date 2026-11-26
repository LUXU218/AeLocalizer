from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest import mock

from runtime import build_profile
from runtime.param_draw_hook import ParamDrawHook


class CheckboxLabelProfileTests(unittest.TestCase):
    def test_two_draws_in_one_caption_helper_are_ambiguous_in_both_orders(self):
        base, target, owner = 0x1000, 0x3000, 0x1100
        block = bytes.fromhex("48895c2428c5fa11442420")
        cleanup = bytes.fromhex("48837b1807")
        helper = bytearray()
        offsets = []
        for _ in range(2):
            helper.extend(block)
            offsets.append(len(helper))
            helper.extend(b"\xe8" + struct.pack("<i", target - base - len(helper) - 5))
            helper.extend(cleanup)
        helper.extend(b"\xc3" + b"\x90" * 16)
        caller = b"\xe8" + struct.pack("<i", base - owner - 5) + b"\xc3"
        pdata = struct.pack("<IIIIII", base, base + len(helper), 0,
                            owner, owner + len(caller), 0)
        blob = bytes(helper).ljust(owner - base, b"\x90") + caller
        image = SimpleNamespace(
            exception_dir=(0x800, len(pdata)), image_base=0x400000,
            text=lambda: (blob, base),
            read_rva=lambda rva, size: pdata if rva == 0x800 else blob[rva-base:rva-base+size],
            u64_rva=lambda rva: 0x400000 + owner if rva == 0x500 else 0,
        )
        with mock.patch.object(build_profile, "_rtti_vt_candidates", return_value=[0x500]):
            self.assertEqual(len(build_profile._verified_checkbox_label(image, target, offsets[:1])), 1)
            for order in (offsets, offsets[::-1]):
                self.assertEqual(build_profile._verified_checkbox_label(image, target, order), [])

    def test_only_verified_checkbox_call_is_supplemental(self):
        root = Path(r"C:\Program Files\Adobe")
        for year in range(2021, 2027):
            with self.subTest(year=year):
                dll = root / f"Adobe After Effects {year}" / "Support Files" / "AfterFXLib.dll"
                if not dll.is_file():
                    self.skipTest("AE DLL sample not installed")
                profile = build_profile.draw_candidate(dll, allow_unknown=True)
                sites = profile["checkbox_sites"]
                self.assertEqual(len(sites), 1)
                site = sites[0]
                self.assertEqual(site["pre_rva"] + len(bytes.fromhex(site["pre_bytes"])), site["call_rva"])
                self.assertEqual(site["call_rva"] + 5, site["ret_rva"])
                self.assertNotIn(site["call_rva"], {profile["call_rva"], *(
                    s["call_rva"] for s in profile["additional_sites"])})
                scope = site["checkbox_scope"]
                owners = {owner["rva"] for owner in scope["owners"]}
                self.assertTrue(scope["calls"])
                self.assertLess(scope["entry_rva"], site["pre_rva"])
                for call in scope["calls"]:
                    self.assertIn(call["owner_rva"], owners)
                    rel = int.from_bytes(bytes.fromhex(call["bytes"])[1:], "little", signed=True)
                    self.assertEqual(call["rva"] + 5 + rel, scope["entry_rva"])
                if profile["known_version"] == "24.1.0.78":
                    # Captured for both target captions on this exact local sample.
                    self.assertEqual(site["ret_rva"], 0x13CA724)

    def test_unlisted_classic_build_uses_only_the_checkbox_site(self):
        dll = Path(r"C:\Program Files\Adobe\Adobe After Effects 2021\Support Files\AfterFXLib.dll")
        if not dll.is_file():
            self.skipTest("AE 2021 DLL sample not installed")
        hook = ParamDrawHook(123, {"Halo Alpha": "光晕透明度"}, str(dll),
                             checkbox_version="18.2.0.37")
        with mock.patch.dict(build_profile.VERIFIED_AFTERFXLIB, clear=True), \
                mock.patch.object(build_profile, "MIN_SCORE", 2.0), \
                mock.patch.object(hook, "_attach_agent", return_value=True) as attach:
            with self.assertRaisesRegex(build_profile.BuildMismatch, "unverified AE build"):
                build_profile.draw_candidate(dll)
            with self.assertRaisesRegex(build_profile.BuildMismatch, "draw callsite not recognized"):
                build_profile.draw_candidate(dll, allow_unknown=True)
            self.assertTrue(hook.attach())
        profile, widget, layer_popup = attach.call_args.args
        self.assertEqual(profile["kind"], "afterfxlib_checkbox")
        self.assertEqual(len(profile["checkbox_sites"]), 1)
        self.assertNotIn("additional_sites", profile)
        self.assertTrue(profile["arg6_bytes"])
        self.assertIsNone(widget)
        self.assertIsNone(layer_popup)
        self.assertFalse(hook.popup_paint_profile)

    def test_unlisted_build_requires_a_unique_native_checkbox_site(self):
        with mock.patch.object(build_profile, "_require_verified", return_value="check"), \
                mock.patch.object(build_profile, "_edb_draw_calls", return_value=(None, {}, b"", "", 0, [])), \
                mock.patch.object(build_profile, "_verified_checkbox_label", return_value=[]):
            with self.assertRaisesRegex(build_profile.BuildMismatch, "not unique"):
                build_profile.checkbox_draw_candidate("AfterFXLib.dll", "check")
