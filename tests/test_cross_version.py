# -*- coding: utf-8 -*-
"""Cross-version build gating, guardruntime upgrade boundaries, popup restore.

These tests cover the reviewer R1/R2/R3/R4/R6/R9/R11 findings that must hold
before a frozen candidate is handed to real-machine QA.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import guardruntime
from runtime import build_profile as bp
from runtime.managed_guard import LanguageEngine
from runtime.popup_session import PopupSession

AE_ROOT = r"C:\Program Files\Adobe"
BUILDS = {
    2021: ("18.2.0.37", "0x16f50d4", "0x16f50e5", "0x16f50ea", "f30f11442420",
           0x95668),
    2022: ("22.6.0.64", "0x1782683", "0x1782694", "0x1782699", "f30f11442420",
           0x9e098),
    2023: ("23.0.0.59", "0x178de03", "0x178de14", "0x178de19", "f30f11442420",
           0x9e098),
    2024: ("24.1.0.78", "0x1a2bfa5", "0x1a2bfb6", "0x1a2bfbb", "c5fa11442420",
           0xd7e90),
    2025: ("25.5.0.4", "0xa93ea4", "0xa93eb5", "0xa93eba", "c5fa11442420",
           0xcdc10),
    2026: ("26.5.0.89", "0xb4f133", "0xb4f144", "0xb4f149", "c5fa11442420",
           0xc58f8),
}


def _paths(year):
    support = os.path.join(AE_ROOT, "Adobe After Effects %d" % year,
                           "Support Files")
    return (os.path.join(support, "AfterFXLib.dll"),
            os.path.join(support, "flt.dll"))


def _present(year):
    lib, flt = _paths(year)
    return os.path.isfile(lib) and os.path.isfile(flt)


@unittest.skipUnless(all(_present(y) for y in BUILDS),
                     "six AE installs not present")
class SixBuildMatrixTest(unittest.TestCase):
    """T9: pin the six derived profiles so a regression cannot drift."""

    def test_derived_values_are_exact(self):
        for year, (ver, pre, call, ret, pre_bytes, vt) in BUILDS.items():
            lib, flt = _paths(year)
            draw = bp.draw_candidate(lib, bp.sha256_file(lib))
            self.assertEqual(draw["known_version"], ver)
            self.assertEqual(hex(draw["pre_rva"]), pre)
            self.assertEqual(hex(draw["call_rva"]), call)
            self.assertEqual(hex(draw["ret_rva"]), ret)
            self.assertEqual(draw["pre_bytes"], pre_bytes)
            self.assertEqual(draw["ret_bytes"], "90488b9424800000")
            self.assertEqual(draw["arg_slot"], 0x28)
            self.assertTrue(draw["verified_build"])
            self.assertGreaterEqual(draw["confidence"], 0.70)
            self.assertGreaterEqual(draw["confidence"] - draw["runner_up"], 0.20)
            pop = bp.popup_profile(flt, bp.sha256_file(flt))
            self.assertEqual(pop["known_version"], ver)
            self.assertEqual(pop["vt_rva"], vt)
            self.assertEqual(pop["field_offset"], 0x70)
            self.assertTrue(pop["verified_build"])


@unittest.skipUnless(_present(2025), "AE2025 not present")
class BuildGateTest(unittest.TestCase):
    """R1: unknown or mis-bound SHA must fail closed in production."""

    def test_unknown_sha_is_rejected(self):
        lib, flt = _paths(2025)
        real = bp.sha256_file(lib)
        with mock.patch.dict(bp.VERIFIED_AFTERFXLIB, {}, clear=True):
            with self.assertRaises(bp.BuildMismatch):
                bp.draw_candidate(lib, real)
        with mock.patch.dict(bp.VERIFIED_FLT, {}, clear=True):
            with self.assertRaises(bp.BuildMismatch):
                bp.popup_profile(flt, bp.sha256_file(flt))

    def test_diagnostic_mode_allows_unknown_structure(self):
        lib, flt = _paths(2025)
        with mock.patch.dict(bp.VERIFIED_AFTERFXLIB, {}, clear=True):
            prof = bp.draw_candidate(lib, bp.sha256_file(lib),
                                     allow_unknown=True)
        self.assertEqual(prof["known_version"], "")
        self.assertFalse(prof["verified_build"])

    def test_mixed_sha_is_rejected(self):
        lib, _flt = _paths(2025)
        other = bp.sha256_file(_paths(2021)[0])
        with self.assertRaises(bp.BuildMismatch):
            bp.draw_candidate(lib, other)


class _Draw:
    def mode(self, on, gen):
        self.on = on
        return True

    def drain(self, timeout):
        return True

    def stats(self):
        return {}

    def close(self, progress=None):
        return True


def _engine(popup):
    eng = object.__new__(LanguageEngine)
    eng.mapping = {"Off": "关"}
    eng.host = {"pid": 1}
    eng.draw = _Draw()
    eng.popup = popup
    eng.popup_error = ""
    eng.popup_restore_unknown = False
    eng.popup_reasons = {}
    eng.popup_unsupported = 0
    eng.rows_pending = False
    eng.background_discovery_pending = False
    eng.draw_error = ""
    eng.last_error = ""
    eng.identity_meta = {}
    eng.draw_stats = {}
    eng.map_error = ""
    eng.stop_stage = ""
    eng.stop_reason = ""
    eng.stop_detail = ""
    eng._stop_logged = None
    eng._refresh_mapping = lambda: True
    eng.attach = lambda host: None
    return eng


class _Popup:
    def __init__(self, scan_exc=None, off_exc=None):
        self.records = {"0x1": {}}
        self.scan_exc = scan_exc
        self.off_exc = off_exc
        self.stats = {}
        self.closed = False

    def scan(self, *, prefer_current=False):
        if self.scan_exc:
            raise self.scan_exc

    def apply(self, mode, mapping):
        if mode == "off" and self.off_exc:
            raise self.off_exc
        return {"changed": 1, "skipped": 0, "unchanged": 0, "reasons": {}}

    def close(self):
        self.closed = True


class PopupRestoreSemanticsTest(unittest.TestCase):
    """R3/R4: never report a restore that could not be confirmed."""

    def test_off_scan_failure_restores_from_cache(self):
        pop = _Popup(scan_exc=RuntimeError("transient read"))
        eng = _engine(pop)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, err = eng.apply("off", {"pid": 1}, 4)
        self.assertTrue(ok, err)
        self.assertFalse(eng.popup_restore_unknown)
        self.assertTrue(pop.closed)

    def test_off_restore_failure_is_exposed(self):
        pop = _Popup(scan_exc=RuntimeError("transient read"),
                     off_exc=OSError("write failed"))
        eng = _engine(pop)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, err = eng.apply("off", {"pid": 1}, 4)
        self.assertTrue(ok, err)
        self.assertTrue(eng.popup_restore_unknown)

    def test_unexpected_exception_is_not_swallowed(self):
        pop = _Popup(scan_exc=AttributeError("internal bug"))
        eng = _engine(pop)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, err = eng.apply("on", {"pid": 1}, 5)
        self.assertFalse(ok)
        self.assertIn("internal bug", eng.draw_error)
        self.assertIsNotNone(eng.popup)


class PopupOpenCleanupTest(unittest.TestCase):
    """R6: a failed _open must not leak the process handle."""

    def test_failed_open_closes_handle(self):
        s = PopupSession(1, tempfile.mkdtemp(prefix="aeloc_popup_"), "", deadline=0.01)
        closed = []
        s._close = lambda: closed.append(True)
        s._open_inner = mock.Mock(side_effect=RuntimeError("FLT build not verified"))
        with self.assertRaises(RuntimeError):
            s._open()
        self.assertEqual(closed, [True])


class GuardRuntimeBoundaryTest(unittest.TestCase):
    """R2/R9: external/unknown manifests must never be silently overwritten."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_grt_")
        self.tpl = os.path.join(self.tmp, "managed_guard.py")
        Path(self.tpl).write_text("print('managed')\n", encoding="utf-8")
        for n in ("build_profile.py", "param_draw_hook.py", "popup_session.py",
                  "effect_owner.py"):
            Path(os.path.join(self.tmp, n)).write_text("# dep\n", encoding="utf-8")
        self.p = mock.patch.object(guardruntime, "TEMPLATE", self.tpl)
        self.p.start()
        self.addCleanup(self.p.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        data = os.path.join(self.tmp, "data")
        guardruntime.ensure_runtime(data)
        return data

    def _manifest(self, data):
        with open(os.path.join(guardruntime.runtime_dir(data),
                               "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, data, obj):
        with open(os.path.join(guardruntime.runtime_dir(data),
                               "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_external_without_template_sha_is_rejected(self):
        data = self._seed()
        man = self._manifest(data)
        man["source"] = "external-guard"
        man["template_sha256"] = None
        self._write_manifest(data, man)
        with self.assertRaises(guardruntime.RuntimeUpgradeRequired):
            guardruntime.ensure_runtime(data)
        self.assertEqual(self._manifest(data)["source"], "external-guard")
        self.assertFalse(guardruntime.can_auto_upgrade(data))

    def test_missing_source_is_rejected(self):
        data = self._seed()
        man = self._manifest(data)
        man.pop("source", None)
        self._write_manifest(data, man)
        with self.assertRaises(guardruntime.RuntimeUpgradeRequired):
            guardruntime.ensure_runtime(data)

    def test_partial_manifest_files_are_rederived(self):
        data = self._seed()
        man = self._manifest(data)
        man["files"] = {"guard_uni.py": man["files"]["guard_uni.py"]}
        self._write_manifest(data, man)
        guardruntime.ensure_runtime(data)
        self.assertEqual(set(self._manifest(data)["files"]),
                         set(guardruntime._DEPLOY_NAMES))


PARADE_VT = {
    2021: 0x8F0E0,
    2022: 0x978F8,
    2023: 0x978F8,
    2024: 0xCFCE8,
    2025: 0xC5A70,
    2026: 0xBF110,
}


@unittest.skipUnless(all(_present(y) for y in BUILDS),
                     "six AE installs not present")
class ParadeVtableTest(unittest.TestCase):
    """2021-2024 built-in profiles left root_vtable=0; derive it per binary."""

    def test_derived_parade_vtable_is_exact(self):
        for year, expected in PARADE_VT.items():
            _lib, flt = _paths(year)
            prof = bp.parade_vtable(flt, bp.sha256_file(flt))
            self.assertEqual(prof["vt_rva"], expected, year)
            self.assertTrue(prof["verified_build"])
            self.assertEqual(prof["class_name"], bp.PARADE_CLASS)

    def test_unknown_sha_is_rejected(self):
        _lib, flt = _paths(2025)
        with mock.patch.dict(bp.VERIFIED_FLT, {}, clear=True):
            with self.assertRaises(bp.BuildMismatch):
                bp.parade_vtable(flt, bp.sha256_file(flt))

    def test_registry_fills_missing_root_vtable(self):
        from core import adapter, livenames
        from dataclasses import replace

        _lib, flt = _paths(2022)
        sha = bp.sha256_file(flt)
        base = replace(adapter.PROFILES["22.6.0.64"], root_vtable=0,
                       flt_sha=sha)
        filled = livenames._with_derived_root(base, flt, sha)
        self.assertEqual(filled.root_vtable, 0x978F8)
        # A mismatched hash must leave the profile untouched (fail closed).
        wrong = livenames._with_derived_root(base, flt, "0" * 64)
        self.assertEqual(wrong.root_vtable, 0)


class ProfilePreferenceTest(unittest.TestCase):
    """Same FLT hash: prefer the richer description instead of builtin-zero."""

    def test_richer_local_candidate_wins(self):
        from core import adapter
        from dataclasses import replace

        builtin = adapter.PROFILES["22.6.0.64"]
        local = replace(builtin, root_vtable=0x978F8, display_cache=0xE8)
        with mock.patch.object(adapter, "candidates",
                               return_value=[builtin, local]):
            got = adapter.get_profile("22.6.0.64", builtin.flt_sha,
                                      data_root="/tmp")
        self.assertEqual(got.root_vtable, 0x978F8)
        self.assertEqual(got.display_cache, 0xE8)

    def test_builtin_used_when_local_is_not_richer(self):
        from core import adapter

        builtin = adapter.PROFILES["22.6.0.64"]
        with mock.patch.object(adapter, "candidates",
                               return_value=[builtin]):
            got = adapter.get_profile("22.6.0.64", builtin.flt_sha,
                                      data_root="/tmp")
        self.assertIs(got, builtin)


class _FakeParamProcess:
    def __init__(self, cache):
        self.cache = cache

    def read(self, address, count):
        if self.cache is None:
            raise OSError("gone")
        return self.cache[:count]


class DisplayNameFallbackTest(unittest.TestCase):
    """A bad/absent display cache must not abort the whole parameter scan."""

    def test_no_offset_returns_pf_name(self):
        from core.paramcatalog import _display_name

        self.assertEqual(_display_name(_FakeParamProcess(None), 0x1000, 0,
                                       "Filter"), ("Filter", False))

    def test_invalid_cache_falls_back(self):
        import struct

        from core.paramcatalog import _display_name

        bad = b"\x00" * 16 + struct.pack("<QQ", 5, 3)
        self.assertEqual(
            _display_name(_FakeParamProcess(bad), 0x1000, 0xE8, "Filter"),
            ("Filter", False))

    def test_inline_cache_reads_utf16(self):
        from core.paramcatalog import _display_name
        import struct

        text = "滤镜".encode("utf-16le") + b"\x00\x00"
        cache = text + b"\x00" * (16 - len(text)) + struct.pack("<QQ", 2, 3)
        self.assertEqual(
            _display_name(_FakeParamProcess(cache), 0x1000, 0xE8, "Filter"),
            ("滤镜", True))


class LanguageDiagSyncTest(unittest.TestCase):
    """QA defect A: runtime status must expose identity/popup diagnostics."""

    def test_sync_copies_all_channel_diag(self):
        from runtime.managed_guard import ControlState, _sync_language_diag

        class _Lang:
            def diagnostics(self):
                return {
                    "draw": {"pre_hooked": 3},
                    "draw_error": "",
                    "map_error": "",
                    "popup_reasons": {"cap": 1},
                    "popup_unsupported": 1,
                    "popup_error": "",
                    "popup_restore_unknown": True,
                    "identity": {"host": "22.6.0.64", "source": "host"},
                    "popup": {"vt": 0x9E098},
                    "rows_pending": True,
                    "background_discovery_pending": False,
                    "stop_stage": "",
                    "stop_reason": "",
                    "stop_detail": "",
                }

        state = ControlState(lambda host: True)
        _sync_language_diag(state, _Lang())
        self.assertEqual(state.identity["source"], "host")
        self.assertEqual(state.popup_diag["vt"], 0x9E098)
        self.assertTrue(state.popup_restore_unknown)
        self.assertTrue(state.rows_pending)


def _dvaui(year):
    support = os.path.join(AE_ROOT, "Adobe After Effects %d" % year,
                           "Support Files")
    return os.path.join(support, "dvaui.dll")


class DvauiTextProfileTest(unittest.TestCase):
    """2026+ name labels are dvaui UI_StaticText; the sink is derived.

    The offset is read from the exported GetTextString getter and must match
    the historical value for a known build, so a patched/unknown dvaui cannot
    silently become a write target.
    """

    TEXT_OFFSETS = {2021: 0xE0, 2022: 0xE0, 2023: 0x100,
                    2024: 0x100, 2025: 0x100, 2026: 0x100}

    def test_six_builds_derive_exact_text_profile(self):
        for year, ver in ((y, v[0]) for y, v in BUILDS.items()):
            path = _dvaui(year)
            if not os.path.isfile(path):
                self.skipTest("dvaui.dll for AE%d missing" % year)
            prof = bp.text_draw_profile(path, bp.sha256_file(path))
            self.assertEqual(prof["known_version"], ver)
            self.assertEqual(prof["text_offset"], self.TEXT_OFFSETS[year])
            self.assertEqual(prof["module"], "dvaui.dll")
            self.assertTrue(prof["draw_export"].startswith(
                "?Draw@UI_StaticText@"))
            self.assertTrue(prof["gettext_export"].startswith(
                "?GetTextString@DrawStaticTextParameters@"))
            self.assertEqual(prof["len_offset"], 0x10)
            self.assertEqual(prof["cap_offset"], 0x18)
            self.assertTrue(prof["verified_build"])

    def test_dvaui_export_table_is_not_truncated(self):
        path = _dvaui(2026)
        if not os.path.isfile(path):
            self.skipTest("AE2026 dvaui.dll missing")
        exports = bp.PEImage(path).exports()
        self.assertGreater(len(exports), 20000)
        self.assertTrue(any(n.startswith(b"?SetValue@UI_StaticText")
                            for n in exports))

    def test_mixed_dvaui_sha_is_rejected(self):
        a, b = _dvaui(2021), _dvaui(2025)
        if not (os.path.isfile(a) and os.path.isfile(b)):
            self.skipTest("dvaui.dll missing")
        with self.assertRaises(bp.BuildMismatch):
            bp.text_draw_profile(b, bp.sha256_file(a))

    def test_unknown_dvaui_is_fail_closed(self):
        path = _dvaui(2026)
        if not os.path.isfile(path):
            self.skipTest("AE2026 dvaui.dll missing")
        with mock.patch.dict(bp.VERIFIED_DVAUI, clear=True):
            with self.assertRaises(bp.BuildMismatch):
                bp.text_draw_profile(path)
            prof = bp.text_draw_profile(path, allow_unknown=True)
        self.assertFalse(prof["verified_build"])
        self.assertEqual(prof["text_offset"], 0x100)

    def test_known_offset_tamper_is_rejected(self):
        path = _dvaui(2026)
        if not os.path.isfile(path):
            self.skipTest("AE2026 dvaui.dll missing")
        bad = dict(bp.KNOWN_TEXT_OFFSET)
        bad["26.5.0.89"] = 0xE0
        with mock.patch.object(bp, "KNOWN_TEXT_OFFSET", bad):
            with self.assertRaises(bp.BuildMismatch) as cm:
                bp.text_draw_profile(path)
        self.assertIn("text offset", str(cm.exception))

    def test_known_store_tamper_is_rejected(self):
        path = _dvaui(2026)
        if not os.path.isfile(path):
            self.skipTest("AE2026 dvaui.dll missing")
        bad = dict(bp.KNOWN_TEXT_STORE)
        bad["26.5.0.89"] = 0x10
        with mock.patch.object(bp, "KNOWN_TEXT_STORE", bad):
            with self.assertRaises(bp.BuildMismatch) as cm:
                bp.text_draw_profile(path)
        self.assertIn("store", str(cm.exception))

    def test_missing_dvaui_is_build_mismatch(self):
        missing = os.path.join(tempfile.gettempdir(),
                               "aeloc-no-such-dvaui.dll")
        with self.assertRaises(bp.BuildMismatch):
            bp.text_draw_profile(missing)

    def test_text_hook_only_enabled_for_2026_profile(self):
        from runtime import param_draw_hook as pdh
        self.assertEqual(tuple(bp.TEXT_LABEL_VERSIONS), ("26.5.0.89",))
        self.assertIn("__TEXT__", pdh.JS)
        self.assertIn("dvaui.dll", pdh.JS)
        self.assertIn("text_replaced", pdh.JS)


if __name__ == "__main__":
    unittest.main()
