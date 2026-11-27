from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from app import backend as backend_module
from app.settings_workspace import SettingsProcess
from core import settings


class SettingsViewTest(unittest.TestCase):
    def setUp(self):
        self.app = QGuiApplication.instance() or QGuiApplication([])

    def test_settings_transport_writes_through_incumbent_only(self):
        root = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {"AELOC_DATA_ROOT": root}))
        self.enterContext(patch.object(backend_module, "DATA_ROOT", root))
        self.assertTrue(settings.save(root, {"engine_master_intent": True, "background_on_ae_startup": True}))
        backend = backend_module.AppBackend()
        host = SettingsProcess(backend, 0, self.app)
        self.addCleanup(host.deleteLater)
        host._ready = True
        with patch.object(host, "_publish") as reply, patch.object(host, "activate"), \
                patch.object(backend, "setStartupWithAe") as set_startup:
            self.assertTrue(host._dispatch(["theme", "dark"]))
            self.assertTrue(settings.put(root, "background_on_ae_startup", False))
            self.assertTrue(host._dispatch(["auto_scan", False]))
            self.assertTrue(host._dispatch(["startup", True]))
            self.assertTrue(host._dispatch(["text_color", "invalid color input"]))
            self.assertFalse(host._dispatch(["auto_scan", 0]))
            self.assertFalse(host._dispatch(["stopEngine", None]))
            self.assertFalse(host._dispatch(["importPack", "fake.aeloc"]))
            self.assertFalse(host._dispatch(["installUpdate", None]))
            self.assertEqual(reply.call_count, 4)
        set_startup.assert_called_once_with(True)
        self.assertEqual(backend.uiTheme, "dark")
        self.assertEqual(backend.textColor, "")
        self.assertFalse(backend.autoScan)
        loaded = settings.load(root)
        self.assertEqual(loaded["ui_theme"], "dark")
        self.assertFalse(loaded["auto_scan"])
        self.assertFalse(loaded["background_on_ae_startup"])
        self.assertTrue(loaded["engine_master_intent"])

    def test_original_settings_qml_routes_owner_dependent_tab(self):
        class FrontendTransport(QObject):
            updated = Signal()
            activated = Signal()

            @Property("QVariantMap", notify=updated)
            def snapshot(self):
                return {"section": 1, "preferences": {"startupWithAe": False}}

            @Slot(str, "QVariant")
            def request(self, command, value):
                messages.append((command, value))

        messages = []
        transport = FrontendTransport()
        engine = QQmlApplicationEngine()
        qml_dir = Path(__file__).resolve().parents[1] / "app" / "qml"
        engine.addImportPath(str(qml_dir))
        engine.rootContext().setContextProperty("settingsTransport", transport)
        engine.load(QUrl.fromLocalFile(str(qml_dir / "SettingsWorkspace.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        page = window.findChild(QObject, "detachedSettingsPage")
        self.assertEqual(page.property("section"), 1)
        startup = page.findChild(QObject, "startupWithAeToggle")
        startup.toggled.emit(True)
        self.assertEqual(messages, [("startup", True)])
        page.selectSection(5)
        self.assertEqual(page.property("section"), 5)
        self.assertTrue(window.isVisible())
        page.selectSection(3)
        self.assertFalse(window.isVisible())
        self.assertEqual(messages, [("startup", True), ("workbench", 3)])
        engine.deleteLater()
