import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import adapter, aescan, proc
from core import livenames
from runtime import build_profile


class LocalMinorProfileTest(unittest.TestCase):
    def test_only_exact_enabled_profile_authorizes_parameter_draw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = replace(adapter.PROFILES["22.6.0.64"], version="22.2.0.120",
                              flt_sha="a" * 64, parameter_draw_sha="b" * 64,
                              enabled=False)
            process = SimpleNamespace(image=str(root / "AfterFX.exe"))
            lib = str(root / "AfterFXLib.dll")
            flt = {"name": "FLT.dll", "path": str(root / "FLT.dll")}
            draw = {"name": "AfterFXLib.dll", "path": lib}
            hashes = {flt["path"]: profile.flt_sha, lib: profile.parameter_draw_sha}
            with patch("core.proc.file_version", return_value=profile.version), \
                    patch("core.proc.module_digest", side_effect=lambda _p, path: hashes[path]):
                adapter.save_local(root, {profile.version: profile})
                self.assertIsNone(adapter.adapted_runtime_profile(process, [flt, draw], root))
                adopted = replace(profile, enabled=True)
                adapter.save_local(root, {profile.version: adopted})
                self.assertEqual(adapter.adapted_runtime_profile(process, [flt, draw], root), adopted)
                hashes[lib] = "c" * 64
                self.assertIsNone(adapter.adapted_runtime_profile(process, [flt, draw], root))
                hashes[lib] = profile.parameter_draw_sha
                hashes[flt["path"]] = "d" * 64
                self.assertIsNone(adapter.adapted_runtime_profile(process, [flt, draw], root))

    def test_disabled_local_candidate_does_not_shadow_builtin(self):
        with tempfile.TemporaryDirectory() as root:
            builtin = adapter.PROFILES["22.6.0.64"]
            candidate = replace(builtin, root_vtable=1, enabled=False)
            adapter.save_local(root, {builtin.version: candidate})
            self.assertEqual(adapter.get_profile(builtin.version, builtin.flt_sha, root), builtin)

    def test_parameter_catalog_recovers_an_overwritten_effect_root(self):
        profile = replace(adapter.PROFILES["22.6.0.64"], version="22.2.0.120",
                          flt_sha="a" * 64, parameter_draw_sha="b" * 64,
                          root_vtable=0x1234)

        def derive(_path, _sha, *, allow_unknown=False):
            if not allow_unknown:
                raise build_profile.BuildMismatch("not in the reference builds")
            return {"vt_rva": 0x9876}

        with patch.object(build_profile, "parade_vtable", side_effect=derive):
            self.assertEqual(livenames._with_derived_root(
                profile, "flt.dll", profile.flt_sha).root_vtable, 0x9876)
            self.assertEqual(livenames._with_derived_root(
                replace(profile, enabled=False), "flt.dll",
                profile.flt_sha).root_vtable, 0x1234)


class ClassicPopupAbiTest(unittest.TestCase):
    def test_classic_abi_matches_installed_reference_build(self):
        module = Path(r"C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\TDB.dll")
        if not module.is_file():
            self.skipTest("AE 2022 not installed")
        self.assertEqual(build_profile.popup_field_layout(module), build_profile.POPUP_FIELD_OFFSET)

    def test_classic_abi_accepts_direct_type_comparisons(self):
        module = Path(r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\TDB.dll")
        if not module.is_file():
            self.skipTest("AE 2024 not installed")
        self.assertEqual(build_profile.popup_field_layout(module), build_profile.POPUP_FIELD_OFFSET)

    def test_real_minor_build_proves_both_popup_modules(self):
        root = os.environ.get("AELOC_TEST_MINOR_POPUP_DIR")
        if not root:
            self.skipTest("set AELOC_TEST_MINOR_POPUP_DIR to AE 24.6.2.x FLT.dll and TDB.dll")
        root = Path(root)
        flt = build_profile.popup_profile(root / "FLT.dll", allow_unknown=True)
        self.assertEqual(flt["field_offset"], build_profile.POPUP_FIELD_OFFSET)
        tdb = root / "TDB.dll"
        self.assertEqual(build_profile.popup_field_layout(tdb), flt["field_offset"])

        image = build_profile.PEImage(tdb)
        writer, = [rva for name, rva in image.exports().items() if name.startswith(b"?PF_ParamDef_SetParamName@@")]
        changed = bytearray(image.raw)
        start = image.raw_offset(writer)
        field = changed.index(bytes.fromhex("b840000000488d1c08"), start, start + 100)
        changed[field + 1] = 0x48
        image.raw = bytes(changed)
        with patch.object(build_profile, "PEImage", return_value=image):
            with self.assertRaisesRegex(build_profile.BuildMismatch, "popup option pointer layout changed"):
                build_profile.popup_field_layout(tdb)


class DelegatedPopupAbiTest(unittest.TestCase):
    def test_media_layout_matches_installed_2025(self):
        root = Path(r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files")
        tdb, media = root / "TDB.dll", root / "MediaFoundation.dll"
        if not tdb.is_file() or not media.is_file():
            self.skipTest("AE 2025 reference build unavailable")
        self.assertEqual(build_profile.media_popup_field_layout(tdb, None, media, None),
                         build_profile.POPUP_FIELD_OFFSET)

    def test_real_new_minor_adopts_complete_module_evidence(self):
        value = os.environ.get("AELOC_TEST_MODERN_MINOR_DIR")
        if not value:
            self.skipTest("set AELOC_TEST_MODERN_MINOR_DIR to the supplied 25.6.4.3 module directory")
        root = Path(value)
        with patch.object(proc, "file_version", return_value="25.6.4.3"):
            report = aescan.analyze_install(root / "AfterFX.exe")
        profile = aescan.candidate_from_report(report)
        self.assertTrue(profile.popup_layout_verified)
        self.assertFalse(build_profile.known_version("afterfxlib", profile.parameter_draw_sha))
        self.assertFalse(build_profile.known_version("flt", profile.flt_sha))
        media = root / "MediaFoundation.dll"
        self.assertEqual(profile.popup_media_sha, build_profile.sha256_file(media))
        with tempfile.TemporaryDirectory() as temp:
            adapter.save_local(temp, {profile.version: profile})
            self.assertEqual(adapter.load_local(temp)[profile.version], profile)
        with self.assertRaisesRegex(build_profile.BuildMismatch, "sha256 mismatch"):
            build_profile.media_popup_field_layout(root / "TDB.dll", profile.tdb_sha, media, "0" * 64)
