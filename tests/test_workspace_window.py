import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, PropertyMock, call, patch

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEvent, QObject
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid

from app import backend as backend_module
from app.background_session import AutomaticClose
from app.workspace_window import WorkspaceWindow


class WorkspaceWindowTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.app = QGuiApplication.instance() or QGuiApplication([])
        profile = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"AELOC_DATA_ROOT": profile}))
        self.enterContext(patch.object(backend_module, "DATA_ROOT", profile))
        self.backend = backend_module.AppBackend()
        self.lease = Mock()
        self.lease.publish_window.return_value = True
        self.workspace = WorkspaceWindow(self.app, self.backend, self.lease,
                                         str(Path(__file__).resolve().parents[1] / "app/qml"))
        self.enterContext(patch.object(WorkspaceWindow, "effectsCanStay", new_callable=PropertyMock, return_value=True))
        self.addCleanup(self.cleanup_workspace)

    def pump(self):
        for _ in range(4):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def cleanup_workspace(self):
        if self.workspace.window:
            self.workspace.window.hide()
            self.workspace.engine.deleteLater()
        if self.workspace.entry:
            self.workspace.entry.hide()
            self.workspace.entry.deleteLater()
        self.workspace.deleteLater()
        self.pump()

    def test_release_and_restore_use_the_same_controller_without_shutdown(self):
        self.assertTrue(self.workspace.open())
        old_window, old_engine = self.workspace.window, self.workspace.engine
        old_window.setProperty("pageIndex", 4)
        old_window.setProperty("settingsSection", 2)
        original_geometry = old_window.geometry()
        live_at_release = []

        def check_released():
            live_at_release.append((isValid(old_window), isValid(old_engine)))

        with patch.object(self.backend, "shutdown") as shutdown, \
                patch.object(self.backend, "release_workspace_dictionary", side_effect=check_released) as release:
            self.assertFalse(old_window.close())
            release.assert_not_called()
            self.pump()
            self.assertIsNone(self.workspace.window)
            self.assertFalse(isValid(old_window))
            self.assertFalse(isValid(old_engine))
            release.assert_called_once_with()
            self.assertEqual(live_at_release, [(False, False)])
            self.assertTrue(isValid(self.backend))
            self.workspace.entry.showNormal()
            self.pump()
            self.assertIs(self.workspace.window.property("backend"), self.backend)
            self.assertEqual(self.workspace.window.property("pageIndex"), 4)
            self.assertEqual(self.workspace.window.property("settingsSection"), 2)
            self.assertEqual(self.workspace.window.geometry(), original_geometry)
            self.assertIsNone(self.workspace.window.findChild(QObject, "translationWorkspace"))
            self.assertIsNone(self.workspace.entry)
            self.workspace.window.setProperty("pageIndex", 0)
            self.pump()
            self.assertIsNotNone(self.workspace.window.findChild(QObject, "translationWorkspace"))
            self.assertFalse(self.workspace.window.close())
            self.pump()
            self.workspace.entry.close()
            self.pump()
            self.assertEqual(self.workspace.window.property("pageIndex"), 2)
            shutdown.assert_not_called()

    def test_reopening_before_release_keeps_the_current_dictionary(self):
        self.assertTrue(self.workspace.open())
        old_engine = self.workspace.engine
        with patch.object(self.backend, "release_workspace_dictionary") as release:
            self.workspace._suspend()
            self.workspace._finish_restore(activate=False)
            self.pump()
            self.assertFalse(isValid(old_engine))
            self.assertIsNotNone(self.workspace.window)
            release.assert_not_called()

    def test_windows_heap_cleanup_runs_after_releasing_the_dictionary(self):
        cleanup = Mock()
        with patch.object(self.app, "platformName", return_value="windows"), \
                patch.object(self.backend, "release_workspace_dictionary", cleanup.release_dictionary), \
                patch("app.workspace_window._optimize_free_heap", cleanup.optimize_heap):
            self.workspace._release_workspace_data()
        self.assertEqual(cleanup.mock_calls, [call.release_dictionary(), call.optimize_heap()])

    def test_busy_or_unregistered_entry_keeps_workbench(self):
        self.assertTrue(self.workspace.open())
        old_window = self.workspace.window
        self.backend._engineBusy = True
        self.assertFalse(old_window.close())
        self.pump()
        self.assertIs(old_window, self.workspace.window)
        self.backend._engineBusy = False
        self.lease.publish_window.return_value = False
        self.assertFalse(old_window.close())
        self.pump()
        self.assertIs(old_window, self.workspace.window)
        self.assertIsNone(self.workspace.entry)

    def test_other_close_modes_use_existing_decision(self):
        self.assertTrue(self.workspace.open())
        with patch.object(WorkspaceWindow, "effectsCanStay", new_callable=PropertyMock, return_value=False), \
                patch.object(self.backend.background_session, "request_close", return_value=False) as close:
            self.assertFalse(self.workspace.window.close())
            self.pump()
            close.assert_called_once()
            self.assertIsNone(self.workspace.entry)

    def test_background_first_keeps_native_entry_until_manual_restore(self):
        with patch.object(self.workspace.session, "automatic_close",
                          side_effect=[AutomaticClose.WAIT, AutomaticClose.KEEP_BACKGROUND]) as check:
            self.assertTrue(self.workspace.start_background())
            self.assertIn("正在恢复汉化", self.workspace.entry.title())
            self.assertIsNone(self.workspace.engine)
            self.workspace._startup_timer.timeout.emit()
            self.assertIsNone(self.workspace.window)
            self.workspace._startup_timer.timeout.emit()
            self.assertIn("后台汉化", self.workspace.entry.title())
            self.assertIsNone(self.workspace.engine)
            self.assertFalse(self.workspace._startup_timer.isActive())
            self.workspace.entry.showNormal()
            self.pump()
            self.assertIs(self.workspace.window.property("backend"), self.backend)
            self.assertEqual(check.call_count, 2)

    def test_timeout_keeps_the_workbench_closed_until_user_opens_engine_status(self):
        self.assertTrue(self.workspace.start_background())
        self.workspace._startup_deadline = QDeadlineTimer(0)
        with patch.object(self.workspace.session, "automatic_close", return_value=AutomaticClose.WAIT):
            self.workspace._startup_timer.timeout.emit()
        self.assertIsNone(self.workspace.engine)
        self.assertFalse(self.workspace._startup_timer.isActive())
        self.assertIn("自动恢复超时", self.workspace.entry.title())
        self.workspace.entry.showNormal()
        self.pump()
        self.assertEqual(self.workspace.window.property("pageIndex"), 2)
        self.assertIs(self.workspace.window.property("backend"), self.backend)
        self.assertIsNone(self.workspace.entry)

    def test_ready_on_the_last_check_counts_as_ready(self):
        self.assertTrue(self.workspace.start_background())
        self.workspace._startup_deadline = QDeadlineTimer(0)
        with patch.object(self.workspace.session, "automatic_close", return_value=AutomaticClose.KEEP_BACKGROUND):
            self.workspace._startup_timer.timeout.emit()
        self.assertIn("后台汉化", self.workspace.entry.title())
        self.assertIsNone(self.workspace.engine)

    def test_explicit_keep_open_still_restores_the_workbench(self):
        self.assertTrue(self.workspace.start_background())
        self.workspace._startup_deadline = QDeadlineTimer(0)
        with patch.object(self.workspace.session, "automatic_close", return_value=AutomaticClose.KEEP_OPEN):
            self.workspace._startup_timer.timeout.emit()
        self.assertIs(self.workspace.window.property("backend"), self.backend)
        self.assertIsNone(self.workspace.entry)
