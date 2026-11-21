import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import adapter
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
