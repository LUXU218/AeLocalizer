import os
import unittest
from pathlib import Path

from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow


class WindowGeometryTest(unittest.TestCase):
    def load_window(self, work_area):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QGuiApplication.instance() or QGuiApplication([])
        qml = Path(__file__).resolve().parents[1] / "app" / "qml"
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(qml))
        engine.rootContext().setContextProperty("initialWindowWorkArea", work_area)
        engine.load(QUrl.fromLocalFile(str(qml / "main.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        self.assertIsInstance(window, QQuickWindow)
        app.processEvents()
        self.addCleanup(engine.deleteLater)
        self.addCleanup(window.hide)
        return app, engine, window

    def test_window_starts_inside_small_desktop_with_all_resize_edges(self):
        # Include a nonzero origin, as on a secondary screen or with a side dock.
        work_area = QRect(-1024, 46, 1024, 704)
        _app, _engine, window = self.load_window(work_area)
        self.assertTrue(work_area.contains(window.geometry()), window.geometry())
        self.assertTrue(window.isVisible())
        self.assertLess(window.minimumWidth(), window.width())
        self.assertLess(window.minimumHeight(), window.height())
        self.assertEqual(window.size().toTuple(), (880, 540))
        def resize_grips(item):
            if item.objectName().startswith("windowResizeGrip"):
                yield item.objectName()
            for child in item.childItems():
                yield from resize_grips(child)

        grips = set(resize_grips(window.contentItem()))
        self.assertEqual(grips, {f"windowResizeGrip{index}" for index in range(8)})

    def test_default_size_is_proportional_on_larger_displays(self):
        work_area = QRect(30, 40, 1920, 1040)
        _app, _engine, window = self.load_window(work_area)
        self.assertEqual(window.geometry(), QRect(395, 196, 1190, 728))

    def test_sidebar_width_changes_smoothly_across_the_old_breakpoint(self):
        app, _engine, window = self.load_window(QRect(0, 0, 1920, 1040))
        sidebar = window.findChild(QQuickItem, "appNavigation")
        self.assertIsNotNone(sidebar)
        widths = []
        for width in (999, 1000, 1001):
            window.resize(width, 650)
            app.processEvents()
            widths.append(sidebar.width())
        self.assertLessEqual(max(widths) - min(widths), 1)

    def test_corner_resize_updates_the_requested_edges_and_keeps_opposite_edges_fixed(self):
        _app, _engine, window = self.load_window(QRect(0, 0, 1920, 1040))
        frame = window.findChild(QQuickItem, "windowResizeFrame")
        self.assertIsNotNone(frame)
        start = window.geometry()

        def invoke(name, *values):
            args = [Q_ARG("QVariant", value) for value in values]
            self.assertTrue(QMetaObject.invokeMethod(frame, name, Qt.DirectConnection, *args))

        edges = (Qt.TopEdge | Qt.LeftEdge).value
        invoke("begin", edges, QPointF(100, 100))
        invoke("move", QPointF(120, 120))
        self.assertEqual(window.geometry(), start.adjusted(20, 20, 0, 0))
        invoke("finish", QPointF(131, 118))
        self.assertEqual(window.geometry(), start.adjusted(31, 18, 0, 0))
        invoke("move", QPointF(150, 142))
        self.assertEqual(window.geometry(), start.adjusted(31, 18, 0, 0))
