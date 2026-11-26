# -*- coding: utf-8 -*-
"""PopupSession row-budget cursor: late popup is not starved forever."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from runtime.popup_session import PopupSession, POPUP_VT_RVA
from runtime.managed_guard import LanguageEngine

BASE = 0x400000
VT = BASE + POPUP_VT_RVA
GUID = (b"ab" * 8).hex()


class _P:
    def read(self, address, size):
        if address in (0x108, 0x148) and size == 16:
            return b"ab" * 8
        if address in (0x120, 0x160) and size == 8:
            return (0x200).to_bytes(8, "little")
        raise OSError("bad read")

    def u64(self, address):
        if address in (0x100, 0x140):
            return VT
        if address in (0x120, 0x160):
            return 0x200
        if address == 0x170:
            return 0x300
        if address == 0x1B0:
            return 0x310
        raise OSError("bad u64")


def _flt():
    return {"base": BASE}


def _rows():
    return [
        {"address": 0x100, "guid": GUID, "parent": 0x200,
         "effectMatchName": "BCC", "matchName": "p1"},
        {"address": 0x140, "guid": GUID, "parent": 0x200,
         "effectMatchName": "BCC", "matchName": "p2"},
    ]


class PopupRowOwnershipTest(unittest.TestCase):
    def _session(self):
        s = PopupSession(1, tempfile.mkdtemp(prefix="aeloc_rows_own_"), "")
        s._open = lambda: (_P(), _flt())
        s._close = lambda: None
        s.plugin_hashes = {}
        return s

    def test_warm_popup_scan_reserves_time_for_existing_rows(self):
        session = self._session()
        result = (SimpleNamespace(version="v", flt_sha="f", tdb_sha="t", encoding="gbk"),
                  [], set(), False)
        with mock.patch("core.paramdiscovery.parameter_rows", return_value=result) as rows, \
             mock.patch("runtime.popup_session.time.monotonic", return_value=10):
            session.scan()
            self.assertEqual(rows.call_args.kwargs["deadline"], 11.0)
            session.scan_stats["rows"] = 2
            session.scan()
            self.assertEqual(rows.call_args.kwargs["deadline"], 11.0)
            self.assertEqual(rows.call_args.kwargs["parade_scan_budget"], 0.20)
            self.assertEqual(rows.call_args.kwargs["rescan_interval"], 2.0)

    def test_only_enabled_popup_uses_fast_polling(self):
        from runtime import managed_guard as guard

        self.assertEqual(guard._language_poll_interval("on", self._session()), 0.30)
        self.assertEqual(guard._language_poll_interval("on", None), 1.0)
        self.assertEqual(guard._language_poll_interval("off", self._session()), 1.0)

    def test_mode_switch_can_request_current_rows_first(self):
        session = self._session()
        session.scan_stats["rows"] = 2
        result = (SimpleNamespace(version="v", flt_sha="f", tdb_sha="t", encoding="gbk"),
                  [], set(), True)
        with mock.patch("core.paramdiscovery.parameter_rows", return_value=result) as rows:
            session.scan(prefer_current=True)
        self.assertTrue(rows.call_args.kwargs["prefer_current"])

    def test_late_unlinked_record_cannot_apply(self):
        s = self._session()
        rows = _rows()
        # first partial pass retains row1
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      rows, set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 100]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            s.scan()
        self.assertEqual(len(s.records), 1)
        # row1/current owner disappears while its heap bytes remain readable
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      [rows[1]], set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            s.scan()
        self.assertNotIn(s._rec_owner(s.records[0]), s.current_owners)

    def test_alias_never_rebases_existing_english_baseline(self):
        s = self._session()
        s.baseline[0x300] = {"raw": b"Off|On", "last": None,
                             "original": b"Off|On", "capacity": 7,
                             "address": 0x100, "guid": GUID,
                             "parent": 0x200, "pointer": 0x300}
        s.baseline_key[0x300] = ("BCC", "p1", "")
        class PShared(_P):
            def u64(self, address):
                if address in (0x100, 0x140):
                    return VT
                if address in (0x120, 0x160):
                    return 0x200
                if address in (0x170, 0x1B0):
                    return 0x300
                raise OSError("bad u64")
        s._open = lambda: (PShared(), _flt())
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      _rows(), set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: "关|开".encode("gbk")):
            s.scan()
        self.assertEqual(s.baseline[0x300]["raw"], b"Off|On")
        self.assertIn(0x300, s._blocked_current)

    def test_prepass_time_does_not_pin_cursor_at_zero(self):
        s = self._session()
        clock = [0.0]
        orig = s._build_current_ownership

        def slow_prepass(p, rows, vt):
            clock[0] += 5.0
            return orig(p, rows, vt)
        s._build_current_ownership = slow_prepass
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      _rows(), set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=lambda: clock[0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            s.scan()
        self.assertGreater(s.scan_stats["processed"], 0)

    def test_cross_chunk_shared_pointer_blocked_before_write(self):
        class PShared(_P):
            def u64(self, address):
                if address in (0x100, 0x140):
                    return VT
                if address in (0x120, 0x160):
                    return 0x200
                if address in (0x170, 0x1B0):
                    return 0x300
                raise OSError("bad u64")

        s = self._session()
        s._open = lambda: (PShared(), _flt())
        rows = _rows()
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      rows, set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            s.scan()
        self.assertEqual(s.records, [])
        self.assertIn(0x300, s._blocked_current)


class PopupRowCursorTest(unittest.TestCase):
    def _session(self):
        s = PopupSession(1, tempfile.mkdtemp(prefix="aeloc_rows_"), "")
        s._open = lambda: (_P(), _flt())
        s._close = lambda: None
        s.plugin_hashes = {}
        return s

    def test_row_cursor_advances_and_pending_clears(self):
        s = self._session()
        rows = _rows()
        # deadline/disc/row_deadline then first row check 0, second check 100
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      rows, set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 100]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            r = s.scan()
        self.assertEqual(len(r), 1)
        self.assertTrue(s.pending)
        self.assertTrue(s.rows_pending)
        self.assertFalse(s.discovery_pending)
        self.assertTrue(s.rows_truncated)
        self.assertTrue(s.scan_stats["rows_pending"])
        self.assertEqual(s.scan_stats["processed"], 1)
        # second scan resumes at cursor 1 and completes the pass
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      rows, set(), False)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 0, 0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            r = s.scan()
        self.assertEqual(len(r), 2)
        self.assertFalse(s.pending)
        self.assertFalse(s.rows_pending)
        self.assertFalse(s.discovery_pending)
        self.assertEqual(s._row_cursor, 0)


class PopupDiscoverySplitTest(unittest.TestCase):
    def _session(self):
        s = PopupSession(1, tempfile.mkdtemp(prefix="aeloc_rows_split_"), "")
        s._open = lambda: (_P(), _flt())
        s._close = lambda: None
        s.plugin_hashes = {}
        return s

    def test_complete_rows_with_background_discovery_is_not_rows_pending(self):
        s = self._session()
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      _rows(), set(), True)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0, 0, 0]), \
             mock.patch("core.proc.virtual_query",
                        return_value=SimpleNamespace(Type=0x20000, Protect=4)), \
             mock.patch("core.popup_fields.read_cstring",
                        side_effect=lambda p, ptr, n: b"Off|On"):
            s.scan()
        self.assertFalse(s.rows_pending)
        self.assertTrue(s.discovery_pending)
        self.assertTrue(s.pending)   # compatibility union
        self.assertFalse(s.scan_stats["rows_pending"])
        self.assertTrue(s.scan_stats["discovery_pending"])

    def test_cold_zero_rows_plus_first_discovery_is_not_ready(self):
        s = self._session()
        with mock.patch("core.paramdiscovery.parameter_rows",
                        return_value=(SimpleNamespace(version="v", flt_sha="f",
                                                      tdb_sha="t", encoding="gbk"),
                                      [], set(), True)), \
             mock.patch("runtime.popup_session.time.monotonic",
                        side_effect=[0, 0, 0]):
            s.scan()
        self.assertFalse(s.rows_pending)
        self.assertTrue(s.discovery_pending)


class _Draw:
    def mode(self, on, gen):
        self.on = on
        return True

    def drain(self, timeout):
        return True

    def stats(self):
        return {}


class _ProgressPopup:
    def __init__(self, rows, processed, rows_pending=False,
                 discovery_pending=False, reasons=None, changed=0):
        self.scan_stats = {
            "rows": rows, "processed": processed,
            "truncated": bool(rows_pending),
            "rows_pending": bool(rows_pending),
            "discovery_pending": bool(discovery_pending),
            "pending": bool(rows_pending or discovery_pending),
            "records": 0,
        }
        self.rows_pending = bool(rows_pending)
        self.discovery_pending = bool(discovery_pending)
        self.reasons = dict(reasons or {})
        self.changed = int(changed)
        self.scans = 0
        self.applies = 0

    def scan(self, *, prefer_current=False):
        self.scans += 1
        self.prefer_current = prefer_current
        return []

    def apply(self, mode, mapping):
        self.applies += 1
        return {"changed": self.changed, "skipped": sum(self.reasons.values()),
                "unchanged": 0, "reasons": dict(self.reasons)}


class LanguageProgressSplitTest(unittest.TestCase):
    def _engine(self, popup):
        eng = object.__new__(LanguageEngine)
        eng.mapping = {}
        eng.host = {"pid": 1}
        eng.draw = _Draw()
        eng.popup = popup
        eng._refresh_mapping = lambda: True
        eng.attach = lambda host: None
        return eng

    def test_switch_redraws_updated_classic_popup_without_blocking_retries(self):
        for mode in ("on", "off"):
            with self.subTest(mode=mode):
                popup = _ProgressPopup(2, 2, changed=1)
                eng = self._engine(popup)
                with mock.patch("runtime.managed_guard._redraw_host",
                                side_effect=lambda pid, **kw: seen.append((popup.applies, kw))):
                    seen = []
                    ok, reason = eng.apply(mode, {"pid": 1}, 7)
                self.assertTrue(ok, reason)
                self.assertTrue(popup.prefer_current)
                self.assertEqual(seen, [(1, {"attempts": 1})])

    def test_complete_rows_with_background_discovery_is_ready(self):
        eng = self._engine(_ProgressPopup(2, 2, discovery_pending=True))
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 7)
        self.assertTrue(ok, reason)
        self.assertFalse(eng.rows_pending)
        self.assertTrue(eng.background_discovery_pending)

    def test_zero_rows_with_discovery_pending_is_not_ready(self):
        eng = self._engine(_ProgressPopup(0, 0, discovery_pending=True))
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 7)
        self.assertFalse(ok)
        self.assertIn("discovery-pending", reason)

    def test_row_truncation_is_not_ready(self):
        eng = self._engine(_ProgressPopup(2, 1, rows_pending=True))
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 7)
        self.assertFalse(ok)
        self.assertIn("row-pass-pending", reason)

    def test_real_write_critical_reason_is_not_ready(self):
        eng = self._engine(_ProgressPopup(2, 2, reasons={"write": 1}))
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 7)
        self.assertFalse(ok)
        self.assertIn("popup-partial", reason)

    def test_background_rescan_does_not_flip_applied_generation(self):
        p = _ProgressPopup(2, 2, discovery_pending=True)
        eng = self._engine(p)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok1, reason1 = eng.apply("on", {"pid": 1}, 7)
            ok2, reason2 = eng.refresh("on")
        self.assertTrue(ok1, reason1)
        self.assertTrue(ok2, reason2)
        self.assertEqual(p.applies, 2)
        self.assertTrue(eng.background_discovery_pending)

    def test_off_restore_partial_is_not_ready(self):
        eng = self._engine(_ProgressPopup(2, 2, reasons={"write": 1}))
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok1, reason1 = eng.apply("off", {"pid": 1}, 8)
            ok2, reason2 = eng.refresh("off")
        self.assertFalse(ok1)
        self.assertIn("popup-restore-partial", reason1)
        self.assertFalse(ok2)
        self.assertIn("popup-restore-partial", reason2)

    def test_off_ready_with_background_discovery_keeps_scanning(self):
        p = _ProgressPopup(2, 2, discovery_pending=True)
        eng = self._engine(p)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok1, reason1 = eng.apply("off", {"pid": 1}, 8)
            ok2, reason2 = eng.refresh("off")
        self.assertTrue(ok1, reason1)
        self.assertTrue(ok2, reason2)
        self.assertEqual(p.applies, 2)
        self.assertTrue(eng.background_discovery_pending)

    def test_new_owner_is_actually_applied_on_later_refresh(self):
        p = _ProgressPopup(2, 2, discovery_pending=True, changed=1)
        eng = self._engine(p)
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.refresh("on")
        self.assertTrue(ok, reason)
        self.assertEqual(p.applies, 1)
        self.assertEqual(p.changed, 1)


class RefreshPendingTest(unittest.TestCase):
    def test_refresh_applies_partial_records_but_stays_pending(self):
        eng = object.__new__(LanguageEngine)
        eng.mapping = {"Off": "关"}
        eng.host = {"pid": 1}
        calls = {"apply": 0}

        class D:
            def set_map(self, m):
                return True
            def stats(self):
                return {}

        class P:
            pending = True
            scan_stats = {"rows": 2, "processed": 1, "truncated": True}
            def scan(self):
                return []
            def apply(self, mode, mapping):
                calls["apply"] += 1
                return {"changed": 0, "skipped": 0,
                        "unchanged": 1, "reasons": {}}

        eng.draw = D()
        eng.popup = P()
        eng._refresh_mapping = lambda: True
        ok, reason = eng.refresh("on")
        self.assertFalse(ok)
        self.assertIn("pending", reason)
        self.assertEqual(calls["apply"], 1)


class PartialChannelTest(unittest.TestCase):
    """Popup 不可验证时不得拖垮已验证的参数绘制通道。"""

    def _engine(self):
        eng = object.__new__(LanguageEngine)
        eng.mapping = {"Off": "关"}
        eng.host = {"pid": 1}
        eng.draw = _Draw()
        eng.popup = None
        eng.popup_error = "FLT build not verified: no popup profile"
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

    def test_draw_only_on_is_ready_and_popup_error_reported(self):
        eng = self._engine()
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 3)
        self.assertTrue(ok, reason)
        self.assertIn("FLT build", eng.diagnostics()["popup_error"])

    def test_scan_time_popup_failure_drops_popup_not_params(self):
        class _Exploding:
            def scan(self, *, prefer_current=False):
                raise RuntimeError("FLT build not verified: popup RTTI class not found")

            def close(self):
                pass

        eng = self._engine()
        eng.popup = _Exploding()
        eng.popup_error = ""
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("on", {"pid": 1}, 5)
        self.assertTrue(ok, reason)
        self.assertIsNone(eng.popup)
        self.assertIn("FLT build not verified", eng.popup_error)
        ok2, reason2 = eng.refresh("on")
        self.assertTrue(ok2, reason2)

    def test_draw_only_off_is_ready(self):
        eng = self._engine()
        with mock.patch("runtime.managed_guard._redraw_host",
                        return_value=True):
            ok, reason = eng.apply("off", {"pid": 1}, 4)
        self.assertTrue(ok, reason)

    def test_direct_stop_redraws_live_draw_only_host_after_unload(self):
        eng = self._engine()
        eng._host_status = mock.Mock(return_value="alive")
        eng.draw = mock.Mock(_gen=5)
        events = []
        eng.draw.mode.side_effect = lambda *_: events.append("disable") or True
        eng.draw.close.side_effect = lambda **_: events.append("unload") or True
        with mock.patch("runtime.managed_guard._redraw_host",
                        side_effect=lambda *_a, **_kw: events.append("redraw")) as redraw:
            self.assertTrue(eng.close())
        self.assertEqual(events, ["disable", "unload", "redraw"])
        redraw.assert_called_once_with(1, attempts=1)
        self.assertIsNone(eng.host)

    def test_direct_stop_scans_only_when_popup_restore_is_owed(self):
        for pending in (False, True):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as temp:
                eng = self._engine()
                eng._host_status = mock.Mock(return_value="alive")
                eng.draw = mock.Mock(_gen=5)
                eng.popup = session = PopupSession(1, temp, "")
                session.records = [{"raw": b"localized" if pending else b"original",
                                    "original": b"original"}]
                with mock.patch.object(session, "scan", side_effect=RuntimeError("unverified layout")) as scan, \
                        mock.patch.object(session, "close") as close, \
                        mock.patch("runtime.managed_guard._redraw_host") as redraw:
                    self.assertEqual(eng.close(), not pending)
                if pending:
                    scan.assert_called_once_with()
                    close.assert_not_called()
                    eng.draw.close.assert_not_called()
                    redraw.assert_not_called()
                    self.assertEqual(eng.stop_stage, "popup-scan")
                else:
                    scan.assert_not_called()
                    close.assert_called_once_with()
                    redraw.assert_called_once_with(1, attempts=1)
                    self.assertIsNone(eng.draw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
