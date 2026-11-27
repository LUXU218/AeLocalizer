"""Engine lifecycle and the evidence exposed to the workbench."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.engine_session import EngineOperation, EngineSessionState, host_identity


class EngineSessionStateTests(unittest.TestCase):
    def setUp(self):
        self.host = {"pid": 8, "ctime": 19, "image": r"C:\QA\AfterFX.exe"}
        self.identity = host_identity(self.host)
        self.state = EngineSessionState()

    def test_language_control_separates_request_acknowledgement_and_application(self):
        self.state.request_language(True, 70)
        command = self.state.language_command(self.identity, True, 80)

        def phase(receipt, confirmed=True):
            return self.state.language_phase(True, receipt, running=True, confirmed=confirmed)

        receipt = {
            "host": self.host,
            "mode": "on",
            "gen": command.generation,
            "ack_gen": command.generation,
            "applied_gen": command.generation,
            "language_ready": True,
        }
        self.assertEqual(phase(receipt, confirmed=False), "waiting-receipt")
        self.assertEqual(phase(dict(receipt, host=dict(self.host, ctime=20))), "waiting-receipt")
        self.assertEqual(phase(dict(receipt, ack_gen=79)), "waiting-receipt")
        self.assertEqual(phase(dict(receipt, applied_gen=79)), "acknowledged")
        self.assertEqual(phase(dict(receipt, language_ready=False)), "acknowledged")
        self.assertEqual(phase(receipt), "applied")
        self.state.request_language(False, 81)
        self.assertEqual(self.state.language_phase(False, receipt, running=True, confirmed=True), "waiting-receipt")

    def test_reopen_reuses_the_background_language_command(self):
        session = {"gen": 12, "host_pid": self.identity[0], "host_ctime": self.identity[1],
                   "host_image": self.identity[2]}
        self.state.resume_background(session)
        self.assertEqual(self.state.channels(False, True), [False, True, True])
        self.assertEqual(self.state.language_command(self.identity, True, 80), self.state.sent_language_command)
        self.assertEqual(self.state.language_generation, 12)
        self.state.language_command((8, 20, self.identity[2]), True, 81)
        self.assertEqual(self.state.language_generation, 81)
        self.assertIsNone(self.state.sent_language_command)

    def test_effect_confirmation_is_limited_to_current_host_and_generation(self):
        generation = self.state.request_effect(True)

        def phase(host):
            return self.state.effect_phase(True, running=True, confirmed=True, host=host)

        self.assertEqual(phase(self.identity), "waiting-receipt")
        self.state.effect_receipt = (generation, self.identity, True)
        self.assertEqual(phase(self.identity), "ready")
        self.assertEqual(phase((8, 20, self.identity[2])), "waiting-receipt")
        self.state.request_effect(False)
        self.state.request_effect(True)
        self.assertEqual(phase(self.identity), "waiting-receipt")


class EngineWorkbenchStateTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtGui import QGuiApplication
        from app import backend

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.app = QGuiApplication.instance() or QGuiApplication([])
        root = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, AELOC_DATA_ROOT=root))
        self.engine = backend.engine
        self.enterContext(patch.object(backend, "DATA_ROOT", root))
        self.enterContext(patch.object(self.engine, "DATA_ROOT", root))
        self.enterContext(patch.object(self.engine, "check", return_value=self.engine.EngineStatus(running=True)))
        self.read_status = self.enterContext(patch.object(self.engine, "control_status", return_value={}))
        self.backend = backend.AppBackend()

    def test_stop_transitions_replace_stale_effect_status_and_allow_retry(self):
        from PySide6.QtQuick import QQuickItem

        self.backend._status = "效果名同步中，稍后自动补写"
        window = self.open_workbench()
        status = window.findChild(QQuickItem, "workbenchStatus")
        self.assertEqual(status.property("text"), "效果名同步中，稍后自动补写")
        jobs = Mock()
        with patch.object(self.engine, "engine_running", return_value=True), patch.object(self.backend, "_run", jobs):
            self.backend.stopEngine()
            self.assertIs(self.backend._engineSession.operation, EngineOperation.STOPPING)
            self.assertTrue(self.backend.engineSessionView["busy"])
            self.assertEqual(status.property("text"), "正在停止引擎")
            jobs.call_args.kwargs["_on_fail"]("worker failed")
            self.assertFalse(self.backend.engineSessionView["busy"])
            self.assertEqual(self.backend.engineSessionView["summary"], "未确认引擎已停止")
            self.assertEqual(status.property("text"), "未确认引擎已停止")
            self.backend.stopEngine()
            self.assertEqual(jobs.call_count, 2)
            jobs.call_args.args[1]((False, "stop not confirmed"))
            self.assertEqual(status.property("text"), "未确认引擎已停止")
            self.backend.stopEngine()
            jobs.call_args.args[1]((True, "引擎已停止"))
            self.assertEqual(status.property("text"), "引擎已停止")

    def test_close_waits_for_the_latest_language_generation(self):
        host = (8, 19, r"c:\qa\afterfx.exe")
        self.backend._engine_intent_set(True)
        self.backend._effWant = False
        session = self.backend._engineSession
        session.request_language(True, 70)
        session.language_command(host, True, 80)
        previous = {"gen": 71, "host_pid": host[0], "host_ctime": host[1], "host_image": host[2]}
        with patch.object(self.engine, "active_language_session", return_value=previous), \
                patch.object(self.backend, "shutdown") as shutdown:
            self.assertFalse(self.backend.closeToBackground())
            self.assertEqual(session.language_generation, 80)
            shutdown.assert_not_called()

    def open_workbench(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QUrl
        from PySide6.QtQml import QQmlApplicationEngine

        qml = Path(__file__).resolve().parents[1] / "app/qml"
        workbench = QQmlApplicationEngine()
        workbench.addImportPath(str(qml))
        workbench.rootContext().setContextProperty("appBackend", self.backend)
        workbench.setInitialProperties({"pageIndex": 2})
        workbench.load(QUrl.fromLocalFile(str(qml / "main.qml")))
        self.assertTrue(workbench.rootObjects())
        window = workbench.rootObjects()[0]

        def close_workbench():
            window.hide()
            workbench.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        self.addCleanup(close_workbench)
        self.app.processEvents()
        return window

    def test_workbench_reads_requests_and_emits_only_user_toggles(self):
        from PySide6.QtCore import Qt
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest

        with patch.object(self.backend, "setChannelAuto", wraps=self.backend.setChannelAuto) as request:
            window = self.open_workbench()
            switch = window.findChild(QQuickItem, "engineEffectSwitch")
            self.assertIsNotNone(switch)
            request.assert_not_called()
            self.assertTrue(switch.property("checked"))
            switch.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(window, Qt.Key_Space)
            request.assert_called_once_with("effect", False)
            self.assertFalse(self.backend.engineSessionView["effectRequested"])
            self.backend._live_intent_set(True)
            self.backend.engineSessionChanged.emit()
            self.assertTrue(switch.property("checked"))
            request.assert_called_once()

    def test_visible_workbench_updates_for_a_late_receipt_with_no_channel_request(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest

        host = {"pid": 8, "ctime": 19, "image": r"C:\QA\AfterFX.exe"}
        session = self.backend._engineSession
        session.request_language(True, 70)
        command = session.language_command(host_identity(host), True, 80)
        self.assertFalse(self.backend._live_auto_allowed())
        with patch.object(self.engine, "profile_runtime_confirmed", side_effect=bool), \
                patch.object(self.engine, "send_control") as send, \
                patch.object(self.backend, "setChannelAuto") as request:
            window = self.open_workbench()
            status = window.findChild(QQuickItem, "engineLanguageStatus")
            self.assertIn("等待确认", status.property("text"))

            def write_receipt():
                self.read_status.return_value = {
                    "host": host, "mode": "on", "gen": command.generation,
                    "ack_gen": command.generation, "applied_gen": command.generation,
                    "language_ready": True, "draw_stats": {"replaced": 1},
                }

            QTimer.singleShot(350, self.backend.stateChanged.emit)
            QTimer.singleShot(1200, self.backend.stateChanged.emit)
            QTimer.singleShot(1400, write_receipt)
            QTest.qWait(2500)
            self.assertIn("绘制替换回执", status.property("text"))
            self.assertEqual(session.language_generation, command.generation)

            window.hide()
            self.app.processEvents()
            reads = self.read_status.call_count
            QTest.qWait(1200)
            self.assertEqual(self.read_status.call_count, reads)
            request.assert_not_called()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
