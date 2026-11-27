import tempfile
import unittest
from unittest.mock import Mock, patch

from app.background_session import AutomaticClose, BackgroundController, BackgroundSession, BackgroundState
from core import engine, settings


class BackgroundSessionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.controller = Mock(spec_set=BackgroundController)
        self.controller.background_state.return_value = BackgroundState(
            shutting_down=False, engine_busy=False, language_disabled=False, language_configured=True,
        )
        self.controller.background_operations_busy.return_value = False
        self.controller.background_needs_effects_controller.return_value = False
        self.controller.background_engine_enabled.return_value = True
        self.session = BackgroundSession(self.controller, self.root)
        self.enterContext(patch.object(engine, "profile_runtime_confirmed", return_value=True))
        self.enterContext(patch.object(engine, "control_status",
                                       return_value={"mode": "on", "language_ready": True}))
        self.running = {"gen": 8, "runtime_pid": 42}
        self.enterContext(patch.object(engine, "active_language_session", return_value=self.running))

    def test_language_handoff_preserves_settings_and_shuts_down_only_once(self):
        self.assertTrue(settings.put(self.root, "other_preference", "kept"))
        self.assertTrue(self.session.request_close())
        self.assertTrue(self.session.request_close())
        self.assertEqual(settings.load(self.root), {"other_preference": "kept",
                                                    "background_language_session": self.running,
                                                    "background_on_ae_startup": True})
        self.controller.shutdown.assert_called_once_with()

    def test_failed_handoff_can_retry_without_shutting_down_controller(self):
        with patch.object(settings, "save", return_value=False):
            self.assertFalse(self.session.request_close())
        self.controller.shutdown.assert_not_called()
        self.controller.notify_background.assert_called_once_with("warn", "无法保存后台会话，设置窗口已保留。")
        self.assertTrue(self.session.request_close())
        self.controller.shutdown.assert_called_once_with()

    def test_automatic_language_close_waits_until_idle_and_saves_handoff(self):
        with patch.object(engine, "background_on_ae_startup", return_value=True):
            self.controller.background_operations_busy.return_value = True
            self.assertIs(self.session.automatic_close(supports_effects_background=False), AutomaticClose.WAIT)
            self.controller.shutdown.assert_not_called()
            self.controller.background_operations_busy.return_value = False
            self.assertIs(self.session.automatic_close(supports_effects_background=False), AutomaticClose.CLOSE)
        self.controller.shutdown.assert_called_once_with()

    def test_effects_background_requires_platform_support_and_keeps_controller(self):
        self.controller.background_needs_effects_controller.return_value = True
        with patch.object(engine, "background_on_ae_startup", return_value=True):
            self.assertIs(self.session.automatic_close(supports_effects_background=False), AutomaticClose.WAIT)
            self.assertIs(self.session.automatic_close(supports_effects_background=True), AutomaticClose.KEEP_BACKGROUND)
        self.controller.shutdown.assert_not_called()
        self.assertTrue(self.session.remember_background_on_startup())
        self.assertEqual(settings.load(self.root), {"background_on_ae_startup": True})

    def test_automatic_close_abandons_wait_when_background_setting_is_cleared(self):
        with patch.object(engine, "background_on_ae_startup", return_value=False):
            self.assertIs(self.session.automatic_close(supports_effects_background=True), AutomaticClose.KEEP_OPEN)
        self.controller.shutdown.assert_not_called()

    def test_workspace_suspend_considers_panel_operations(self):
        self.controller.background_operations_busy.return_value = True
        self.assertTrue(self.session.workspace_busy())
        self.controller.background_operations_busy.assert_called_once_with(include_panel=True)

    def test_ordinary_close_does_not_set_background_preference_after_engine_stop(self):
        self.controller.background_engine_enabled.return_value = False
        self.assertTrue(self.session.request_close())
        self.controller.shutdown.assert_called_once_with()
        self.assertEqual(settings.load(self.root), {})
