import unittest
from unittest.mock import Mock, patch

from app import main
from core import engine, windows_startup


class DesktopLaunchTest(unittest.TestCase):
    def test_confirmed_language_session_needs_no_workspace_or_ui_lease(self):
        with patch.object(main.sys, "argv", ["AeLocalizer.exe", "--from-ae-startup"]), \
             patch.object(engine, "background_on_ae_startup", return_value=True), \
             patch.object(engine, "background_language_session", return_value={"gen": 1}), \
             patch.object(windows_startup, "acquire_ui") as acquire, \
             patch.object(main, "_run_gui") as run_gui:
            self.assertEqual(main._main_inner(), 0)
        acquire.assert_not_called()
        run_gui.assert_not_called()

    def test_manual_launch_activates_existing_window_but_automatic_launch_leaves_it_alone(self):
        for automatic in (False, True):
            args = ["AeLocalizer.exe"] + (["--from-ae-startup"] if automatic else [])
            with self.subTest(automatic=automatic), patch.object(main.sys, "argv", args), \
                 patch.object(windows_startup, "acquire_ui", return_value=None) as acquire, \
                 patch.object(windows_startup, "activate_ui") as activate, \
                 patch.object(main, "_run_gui") as run_gui:
                self.assertEqual(main._main_inner(), 0)
                acquire.assert_called_once_with(automatic=automatic)
                self.assertEqual(activate.call_count, int(not automatic))
                run_gui.assert_not_called()

    def test_window_initialization_failure_releases_ownership(self):
        lease = Mock()
        with patch.object(main.sys, "argv", ["AeLocalizer.exe", "--from-ae-startup"]), \
             patch.object(windows_startup, "acquire_ui", return_value=lease), \
             patch.object(main, "_run_gui", side_effect=RuntimeError("QML")):
            with self.assertRaisesRegex(RuntimeError, "QML"):
                main._main_inner()
        lease.close.assert_called_once_with()
