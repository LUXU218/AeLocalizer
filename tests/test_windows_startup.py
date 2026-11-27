"""Login entry ownership, portable moves and UI mutex lifetime."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from core import windows_startup as startup


class Registry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE, KEY_QUERY_VALUE, REG_SZ = 2, 1, 1

    def __init__(self):
        self.values = {"UserOtherApp": ("leave unchanged", self.REG_SZ)}
        self.writes = []

    def OpenKey(self, root, path, *args):
        assert root is self.HKEY_CURRENT_USER and path == startup.RUN_KEY
        return nullcontext(self)

    CreateKeyEx = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append(("set", name))
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        self.writes.append(("delete", name))
        del self.values[name]


class LoginEntryTest(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.helper = folder / "new copy" / startup.HELPER_NAME
        self.helper.parent.mkdir()
        self.helper.touch()
        self.root = folder / "app profile"
        self.registry = Registry()
        self.enterContext(mock.patch.dict(sys.modules, {"winreg": self.registry}))
        self.enterContext(mock.patch.object(startup, "_helper", return_value=self.helper))
        self.cancel = self.enterContext(mock.patch.object(startup, "cancel_standby"))

    def test_disabled_by_default_and_toggle_idempotent(self):
        self.assertFalse(startup.is_enabled(self.root))
        for _ in range(2):
            startup.set_enabled(True, self.root)
        self.assertTrue(startup.is_enabled(self.root))
        command, kind = self.registry.values["AeLocalizer"]
        self.assertEqual(command, f'"{self.helper}" --wait-for-ae {startup.scope_id(self.root)}')
        for _ in range(2):
            startup.set_enabled(False, self.root)
        self.assertFalse(startup.is_enabled(self.root))
        self.assertEqual(self.registry.writes, [("set", "AeLocalizer"), ("delete", "AeLocalizer")])
        self.assertEqual(self.registry.values, {"UserOtherApp": ("leave unchanged", kind)})
        self.cancel.assert_called_with(self.root)

    def test_portable_move_explicit_enable_retakes_same_product_and_scope(self):
        old_helper = self.root.parent / "old copy" / startup.HELPER_NAME
        with mock.patch.object(startup, "_helper", return_value=old_helper):
            old_command = startup._command(self.root)
        self.registry.values["AeLocalizer"] = (old_command, self.registry.REG_SZ)
        self.assertFalse(startup.is_enabled(self.root))
        with self.assertRaises(ValueError):
            startup.set_enabled(False, self.root)
        self.assertEqual(self.registry.writes, [])
        startup.set_enabled(True, self.root)
        self.assertTrue(startup.is_enabled(self.root))
        self.assertNotEqual(self.registry.values["AeLocalizer"][0], old_command)

    def test_foreign_entries_and_modified_arguments_are_preserved(self):
        ours = startup._command(self.root)
        for value in [("another-program.exe", 1), (ours + " extra", 1),
                      (ours.replace("Startup.exe", "Other.exe"), 1),
                      (startup._command(self.root.parent), 1), (ours, 2)]:
            with self.subTest(value=value):
                self.registry.values["AeLocalizer"] = value
                for enabled in (True, False):
                    with self.assertRaises(ValueError):
                        startup.set_enabled(enabled, self.root)
                self.assertEqual(self.registry.values["AeLocalizer"], value)
                self.assertFalse(startup.is_enabled(self.root))
                self.assertEqual(self.registry.writes, [])

    def test_missing_helper_does_not_create_entry_but_old_entry_can_be_disabled(self):
        startup.set_enabled(True, self.root)
        self.helper.unlink()
        self.assertTrue(startup.is_enabled(self.root))
        startup.set_enabled(False, self.root)
        with self.assertRaises(ValueError):
            startup.set_enabled(True, self.root)
        self.assertFalse(startup.is_enabled(self.root))


@unittest.skipUnless(sys.platform == "win32", "Requires Windows session objects")
class UiLeaseTest(unittest.TestCase):
    def test_only_one_window_lease_per_profile_and_session(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        first = startup.acquire_ui(folder)
        self.assertIsNotNone(first)
        self.addCleanup(first.close)
        self.assertIsNone(startup.acquire_ui(folder, automatic=True))
        other = startup.acquire_ui(folder / "other")
        self.assertIsNotNone(other)
        self.addCleanup(other.close)
        first.close()
        replacement = startup.acquire_ui(folder)
        self.assertIsNotNone(replacement)
        replacement.close()

    def test_permission_error_preserves_manual_launch_and_suppresses_auto(self):
        with mock.patch.object(startup, "_native", return_value=lambda *args: 0):
            self.assertIsNotNone(startup.acquire_ui("unused"))
            self.assertIsNone(startup.acquire_ui("unused", automatic=True))
