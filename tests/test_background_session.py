import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeyEvent

from app import backend, main
from app.background_session import AutomaticClose
from core import engine, proc, settings, windows_startup
from core.inventory import CH_PARAM


class BackgroundSessionTests(unittest.TestCase):
    def setUp(self):
        self.app = QGuiApplication.instance() or QGuiApplication([])
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, AELOC_DATA_ROOT=str(self.root)))
        self.enterContext(patch.object(engine, "DATA_ROOT", str(self.root)))
        self.enterContext(patch.object(backend, "DATA_ROOT", str(self.root)))
        self.enterContext(patch.object(engine, "check", return_value=engine.EngineStatus(running=True, profile_confirmed=True)))
        self.host = {"pid": 12, "ctime": 100, "image": r"C:\QA\AfterFX.exe"}
        self.receipt = dict(protocol="aeloc-managed-guard/1", control_ready=True, mode="on", language_ready=True,
                            gen=8, applied_gen=8, ack_gen=8, runtime_pid=42, runtime_ctime=200,
                            host=self.host, updated=time.time())
        self.live = dict(pid=12, creation_time=100, image=self.host["image"])
        self.side = self.root / "guard_runtime/out/guard.lock.json"
        self.side.parent.mkdir(parents=True)
        self.side.write_text(json.dumps(dict(protocol=self.receipt["protocol"], pid=42, ctime=200,
                                             image=r"C:\QA\AeLocalizer.exe", token="test")), encoding="utf-8")
        self.enterContext(patch.object(engine, "control_status", return_value=self.receipt))
        self.enterContext(patch.object(engine, "_proc_creation_time", return_value=200))
        self.profile_owner = self.enterContext(patch.object(engine, "_profile_runtime_mutex_exists", return_value=True))
        self.enterContext(patch.object(engine, "read_guard_lock", return_value=42))
        self.hosts = self.enterContext(patch.object(proc, "list_ae_hosts", return_value=[self.live]))
        self.enterContext(patch.object(proc, "main_window", return_value=123))
        self.assertTrue(settings.save(str(self.root), {"language_intent":True, "engine_master_intent":True}))

    def ui(self):
        window = backend.AppBackend()
        g = SimpleNamespace(selected=True, channels={CH_PARAM})
        window._inv = SimpleNamespace(groups=[g], selected=lambda: [g])
        return window

    def test_handoff_and_manual_reopen_keep_original_service_and_generation(self):
        window = self.ui()
        with patch.object(engine, "stop_engine") as stop, patch.object(engine, "start_engine") as start:
            self.assertTrue(window.requestWindowClose())
            self.assertTrue(window.requestWindowClose())
            expected = engine.background_language_session()
            self.assertEqual(expected["runtime_pid"], 42)
            self.assertTrue(engine.background_on_ae_startup())
            opened = self.ui()
            with patch.object(engine, "send_control") as send:
                self.assertTrue(opened._send_language_control([self.live]))
                send.assert_not_called()
            self.assertEqual(opened._langGen, 8)
            stop.assert_not_called()
            start.assert_not_called()
        with patch.object(engine, "engine_running", return_value=False):
            opened.stopEngine()
        self.assertIsNone(engine.background_language_session())
        self.assertFalse(engine.background_on_ae_startup())

    def test_handoff_rechecks_current_process_profile_host_and_generation(self):
        window = self.ui()
        self.assertIsNotNone(engine.active_language_session())
        for change in ({"updated":0}, {"mode":"off"}, {"runtime_ctime":199}, {"runtime_pid":43},
                       {"applied_gen":7}, {"language_ready":False}):
            with self.subTest(change=change), patch.dict(self.receipt, change):
                self.assertFalse(window.closeToBackground())
                self.assertFalse(window._liveShutdown)
        for hosts in ([], [dict(self.live, creation_time=99)], [self.live,self.live]):
            with self.subTest(hosts=hosts):
                self.hosts.return_value = hosts
                self.assertIsNone(engine.active_language_session())
        self.hosts.return_value = [self.live]
        self.profile_owner.return_value = False
        self.assertIsNone(engine.active_language_session())
        self.profile_owner.return_value = True
        self.side.unlink()
        self.assertIsNone(engine.active_language_session())
        self.assertIsNone(settings.get(str(self.root), "background_language_session"))

    def test_reopened_language_session_displays_actual_channels_before_scan(self):
        self.assertTrue(self.ui().closeToBackground())
        reopened = backend.AppBackend()
        self.assertIsNone(reopened._inv)
        self.assertEqual(reopened.channelState(), [False, True, True])

    def test_in_progress_or_effects_keep_the_window_until_a_deliberate_stop(self):
        window = self.ui()
        window._engineBusy = True
        self.assertFalse(window.requestWindowClose())
        window._engineBusy = False
        window._effWant = True
        self.assertFalse(window.requestWindowClose())
        self.assertFalse(window._liveShutdown)
        settings.put(str(self.root), "engine_master_intent", False)
        self.assertTrue(window.requestWindowClose())

    def test_logged_in_launcher_only_suppresses_confirmed_saved_session(self):
        self.assertTrue(self.ui().closeToBackground())
        automatic = ["AeLocalizer.exe", "--from-ae-startup"]
        with patch.object(main.sys, "argv", automatic), patch.object(windows_startup,"acquire_ui") as acquire:
            self.assertEqual(main._main_inner(), 0)
            acquire.assert_not_called()
            with patch.dict(self.receipt, runtime_ctime=199), patch.object(main, "_run_gui", return_value=0) as gui:
                self.assertEqual(main._main_inner(), 0)
                gui.assert_called_once()
        with patch.object(main.sys,"argv",["AeLocalizer.exe"]), patch.object(windows_startup, "acquire_ui"), \
                patch.object(main, "_run_gui", return_value=0) as gui:
            self.assertEqual(main._main_inner(), 0)
            gui.assert_called_once()
        self.assertFalse(engine.background_on_ae_startup())

    def test_automatic_window_only_closes_after_regular_restore_confirms(self):
        window = QObject()
        window.close = Mock()
        session = Mock()
        session.automatic_close.side_effect = [AutomaticClose.WAIT, AutomaticClose.CLOSE]
        self.assertTrue(settings.put(str(self.root), "background_on_ae_startup", True))
        main._close_after_language_restore(self.app, window, session)
        timer = window.findChild(QTimer)
        timer.timeout.emit()
        window.close.assert_not_called()
        timer.timeout.emit()
        self.assertEqual(session.automatic_close.call_count, 2)
        window.close.assert_called_once()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_using_the_automatic_window_cancels_its_pending_close(self):
        window = QObject()
        window.close = Mock()
        session = Mock()
        self.assertTrue(settings.put(str(self.root), "background_on_ae_startup", True))
        main._close_after_language_restore(self.app, window, session)
        timer = window.findChild(QTimer)
        QCoreApplication.sendEvent(window, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(timer.isActive())
        session.automatic_close.assert_not_called()
        window.close.assert_not_called()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
