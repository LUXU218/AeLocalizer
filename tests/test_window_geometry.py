import os
import unittest
from pathlib import Path

from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest


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

    def test_grouped_navigation_and_translation_actions_at_minimum_size(self):
        app, engine, window = self.load_window(QRect(0, 0, 1024, 704))
        theme = engine.singletonInstance("AeLocalizer", "Theme")
        theme.setProperty("density", "relaxed")
        theme.setProperty("fontScale", "large")
        window.resize(window.minimumWidth(), window.minimumHeight())
        app.processEvents()

        def named_items(item):
            if item.objectName():
                yield item.objectName(), item
            for child in item.childItems():
                yield from named_items(child)

        items = dict(named_items(window.contentItem()))
        for index in (1, 3, 5, 2, 4, 0):
            nav = items[f"navPage{index}"]
            self.assertTrue(nav.isVisible())
            label = next(child for child in nav.property("contentItem").childItems()
                         if child.property("text") == nav.property("text"))
            self.assertFalse(label.property("truncated"), nav.property("text"))
            nav.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(window, Qt.Key_Space)
            app.processEvents()
            self.assertEqual(window.property("pageIndex"), index)
            self.assertEqual(items["startTranslationButton"].isVisible(), index == 0)

        # Both the version picker and the primary action must fit the compact header.
        version = items["versionBox"]
        start = items["startTranslationButton"]
        self.assertLessEqual(version.mapToScene(QPointF(version.width(), 0)).x(),
                             start.mapToScene(QPointF()).x())
        self.assertLessEqual(start.mapToScene(QPointF(start.width(), 0)).x(), window.width())
        self.assertFalse(start.isEnabled())  # No backend in this window.

    def test_wallpaper_card_and_tab_rail_follow_same_translucent_palette(self):
        app, engine, window = self.load_window(QRect(0, 0, 1200, 800))
        window.setProperty("pageIndex", 4)
        component = QQmlComponent(engine)
        component.setData(b'import QtQml; import AeLocalizer; QtObject { property QtObject theme: Theme }', QUrl())
        probe = component.create()
        self.assertIsNotNone(probe, component.errorString())
        theme = probe.property("theme")
        card = window.findChild(QQuickItem, "wallpaperSettings")
        tint = next(item for item in card.childItems() if item.inherits("QQuickRectangle"))
        # Use a valid image without touching the user's appearance settings.
        from PySide6.QtGui import QImage
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            path = Path(folder) / "wallpaper.png"
            image = QImage(8, 8, QImage.Format_RGB32)
            image.fill(Qt.blue)
            self.assertTrue(image.save(str(path)))
            theme.setProperty("bgImage", QUrl.fromLocalFile(str(path)).toString())
            app.processEvents()
            for mode in ("light", "dark"):
                theme.setProperty("themeMode", mode)
                previous = 0
                for plate in (0.1, 0.9):
                    theme.setProperty("plate", plate)
                    color = tint.property("color")
                    self.assertEqual(color, theme.property("insetFill"))
                    self.assertLess(color.alpha(), 255)
                    self.assertGreater(color.alpha(), previous)
                    previous = color.alpha()
            theme.setProperty("bgImage", "")
            self.assertEqual(tint.property("color").alpha(), 255)

    def test_inactive_panels_allocate_no_wallpaper_sampling_textures(self):
        _app, _engine, window = self.load_window(QRect(0, 0, 1200, 800))
        window.setProperty("pageIndex", 4)
        card = window.findChild(QQuickItem, "wallpaperSettings")
        def samples(item):
            return int(item.inherits("QQuickShaderEffectSource")) + sum(samples(c) for c in item.childItems())
        self.assertEqual(samples(card), 0)

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
