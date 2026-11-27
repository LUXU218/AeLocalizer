import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from core.guardruntime import runtime_guard
from tools.build_exe import write_qt_runtime_hook


class FrozenQtStartupTest(unittest.TestCase):
    def test_only_the_managed_guard_skips_the_upstream_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            package = types.ModuleType("PyInstaller")
            package.__file__ = str(directory / "__init__.py")
            upstream = directory / "hooks/rthooks/pyi_rth_pyside6.py"
            upstream.parent.mkdir(parents=True)
            upstream.write_text("qt_configured = True\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"PyInstaller": package}):
                hooks = write_qt_runtime_hook(directory / "build")
            hook = hooks / "rthooks/aelocalizer_pyside6.py"
            for args, expected in (([], True), (["--snapshot", "window.png"], True),
                                   (["--run-guard", "other_script.py"], True),
                                   (["--run-guard", runtime_guard(tmp), "apply", "600"], False)):
                with self.subTest(args=args), mock.patch.object(sys, "argv", ["AeLocalizer.exe", *args]):
                    self.assertEqual("qt_configured" in runpy.run_path(str(hook)), expected)
