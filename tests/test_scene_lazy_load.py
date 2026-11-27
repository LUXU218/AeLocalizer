import os
import sys
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Qt

from app import main
from app.backend_parts.wallpaper_ui import WallpaperUiMixin


class SceneBackend(WallpaperUiMixin, QObject):
    pass


class SceneLazyLoadTest(unittest.TestCase):
    binding = "PySide6.QtWebEngineQuick"

    def test_gui_sets_graphics_attribute_without_importing_webengine(self):
        with patch.dict(os.environ, {"AELOC_NO_WEBENGINE": "", "QTWEBENGINE_CHROMIUM_FLAGS": "--custom"}), \
             patch.dict(sys.modules, {self.binding: None}), \
             patch("importlib.util.find_spec", return_value=object()) as spec, \
             patch.object(QCoreApplication, "setAttribute") as set_attribute:
            self.assertTrue(main._prepare_web_engine())
            spec.assert_called_once_with(self.binding)
            set_attribute.assert_called_once_with(Qt.AA_ShareOpenGLContexts)
            self.assertEqual(os.environ["AELOC_WEBENGINE"], "1")
            self.assertIn("--custom", os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split())
            self.assertFalse(SceneBackend().webEngineAvailable)

    def test_missing_binding_and_disabled_mode_keep_the_fallback(self):
        for disabled in ("0", "1"):
            with self.subTest(disabled=disabled), patch.dict(os.environ, {"AELOC_NO_WEBENGINE": disabled}), \
                 patch("importlib.util.find_spec", return_value=None) as spec, \
                 patch.object(QCoreApplication, "setAttribute") as set_attribute:
                self.assertFalse(main._prepare_web_engine())
                self.assertEqual(spec.call_count, int(disabled == "0"))
                self.assertEqual(os.environ["AELOC_WEBENGINE"], "0")
                self.assertFalse(SceneBackend().webEngineAvailable)
                set_attribute.assert_not_called()
