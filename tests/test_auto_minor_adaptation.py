import json
import subprocess
import unittest
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.backend import AppBackend
from core import adapter, aescan, paramnative, proc
from tests.fake_ae import FakeAE


class BackendFixture(SimpleNamespace):
    def __getattr__(self, name):
        method = getattr(AppBackend, name, None)
        if callable(method) and name.startswith("_auto_adapt_"):
            return method.__get__(self)
        raise AttributeError(name)


class AutoMinorAdaptationTest(unittest.TestCase):
    def test_catalog_relay_is_closed_before_adoption_and_version_mismatch_is_rejected(self):
        for version in ("24.6x2", "24.7x8"):
            with self.subTest(version=version), TemporaryDirectory() as temp:
                process = SimpleNamespace(pid=3, image="AfterFX.exe")
                launcher = Mock(pid=4)
                launcher.wait.side_effect = [subprocess.TimeoutExpired("AfterFX.exe", 3), 0]
                catalog = [{"matchName": "Example"}]

                def start(_args, **_kwargs):
                    folder, = (Path(temp) / "ae-bridge").iterdir()
                    (folder / "probe.json").write_text(
                        json.dumps({"aeVersion": version, "catalog": catalog}), encoding="utf8")
                    return launcher

                with patch.object(paramnative, "data_root", return_value=temp), \
                        patch.object(paramnative.subprocess, "Popen", side_effect=start):
                    if version == "24.6x2":
                        self.assertEqual(paramnative.probe_catalog(
                            process, expected_version="24.6x2"), catalog)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "构建与当前编辑会话不一致"):
                            paramnative.probe_catalog(process, expected_version="24.6x2")
                self.assertEqual([call[0] for call in launcher.method_calls],
                                 ["wait", "terminate", "wait"])
                self.assertEqual(list((Path(temp) / "ae-bridge").iterdir()), [])

    def test_failed_popup_verification_is_reported_for_new_and_cached_profiles(self):
        version = "26.3.0.87"
        host = {"pid": 400, "creation_time": 500, "image": "AfterFX.exe", "fileVersion": version}
        error = "MediaFoundation popup option pointer layout changed"
        adopted = {"adopted": True, "version": version, "encoding": "utf8", "popup_error": error}
        for result in (adopted, {"popup_blocked": error}):
            with self.subTest(result=result), patch("app.backend.QTimer.singleShot") as timer:
                backend = BackendFixture(_busy=False, _run=Mock(), toast=Mock(), stateChanged=Mock(),
                                         _live_watch_log=Mock(), _auto_restore_engine_async=Mock())
                backend._auto_adapt_async(version, hosts=[host])
                done = backend._run.call_args.args[1]
                done(result)
                level, message = backend.toast.emit.call_args.args
                self.assertEqual(level, "warn")
                self.assertIn(version, message)
                self.assertIn("展开选项保留原文", message)
                self.assertIn(error, message)
                self.assertEqual(backend._autoAdaptStatus, message)
                self.assertEqual(backend._autoAdaptPopupError, error)
                if result.get("adopted"):
                    timer.call_args.args[1]()
                    backend._auto_restore_engine_async.assert_called_once_with(
                        refresh_runtime=True, refresh_popup=False)
                else:
                    timer.assert_not_called()

        backend = BackendFixture(_running_ae_version=Mock(return_value=version),
                                 _adapt_running_ae=Mock(return_value=adopted), toast=Mock())
        with patch.object(adapter, "is_supported", return_value=False):
            AppBackend._ensure_adapted(backend)
        level, message = backend.toast.emit.call_args.args
        self.assertEqual(level, "warn")
        self.assertIn(error, message)

    def test_catalog_not_ready_can_retry_in_same_session(self):
        host = {"pid": 400, "creation_time": 500, "image": "AfterFX.exe", "fileVersion": "22.2.0.120"}
        adapt = Mock(side_effect=[TimeoutError("目录尚未准备好"),
                                  {"adopted": True, "version": host["fileVersion"], "encoding": "utf8"}])
        backend = BackendFixture(_busy=False, _liveShutdown=False,
                                  _adapt_running_ae=adapt, _run=Mock(),
                                  toast=Mock(), stateChanged=Mock(), _live_watch_log=Mock(),
                                  _auto_restore_engine_async=Mock())

        def tick():
            backend._auto_adapt_async(host["fileVersion"])

        def complete_job():
            args, kwargs = backend._run.call_args
            work, done = args
            try:
                result = work()
            except Exception as exc:
                if kwargs.get("_on_fail"):
                    kwargs["_on_fail"](str(exc))
            else:
                done(result)

        with patch("app.backend.time.monotonic") as clock, \
                patch("app.backend.QTimer.singleShot"), \
                patch("core.proc.list_ae_processes", return_value=[host]):
            clock.return_value = 100
            tick()
            tick()
            self.assertEqual(backend._run.call_count, 1)
            complete_job()
            tick()
            self.assertEqual(backend._run.call_count, 1)
            clock.return_value = 170
            tick()
            self.assertEqual(backend._run.call_count, 2)
            complete_job()
            clock.return_value = 310
            tick()
            self.assertEqual(backend._run.call_count, 2)
            self.assertEqual(adapt.call_count, 2)
            host["creation_time"] += 1
            tick()
            self.assertEqual(backend._run.call_count, 3)

    def test_headless_process_does_not_hide_the_editor(self):
        editor = {"pid": 400, "fileVersion": "24.6.2.2"}
        other = {"pid": 401, "fileVersion": "24.6.2.2"}
        with patch.object(proc, "list_ae_processes", return_value=[editor, other]), \
                patch.object(proc, "_ae_windows", return_value=[(400, 100)]) as windows:
            self.assertEqual(proc.list_ae_hosts(), [editor])
            windows.return_value = [(400, 100), (401, 200)]
            self.assertEqual(proc.list_ae_hosts(), [editor, other])
            windows.return_value = []
            self.assertEqual(proc.list_ae_hosts(), [editor, other])

    def test_unknown_build_adopts_profile_consumed_by_parameter_runtime(self):
        with TemporaryDirectory() as tmp:
            fake = FakeAE(tmp, version="24.6.2.2", save=False)
            fake.image = str(fake.flt_path.parent / "AfterFX.exe")
            draw = fake.flt_path.parent / "AfterFXLib.dll"
            draw.write_bytes(b"AfterFXLib fixture")
            process = fake.process()
            fake.modules.append({"base": 0x1234, "name": draw.name, "path": str(draw)})
            profile = replace(fake.profile, enabled=False, registry_vector=0,
                              parameter_draw_sha=proc.module_digest(process, draw))
            report = {"kind": "aeloc-adapter-analysis", "state": "candidate",
                      "candidate": aescan._profile_to_candidate(profile), "paramsSupported": True}
            host = {"pid": fake.pid, "image": fake.image,
                    "fileVersion": fake.version, "creation_time": fake.creation_time}
            helper = {**host, "pid": fake.pid + 1}
            self.assertFalse(adapter.is_supported(fake.version, data_root=tmp))
            with patch("app.backend.DATA_ROOT", tmp), \
                    patch.object(proc, "list_ae_processes", return_value=[host, helper]), \
                    patch.object(proc, "_ae_windows", return_value=[(fake.pid, 100)]), \
                    patch.object(proc, "main_window", return_value=100), \
                    patch.object(proc, "file_version", return_value=fake.version), \
                    patch.object(proc, "Process", return_value=process) as opened, \
                    patch.object(aescan, "analyze_install", return_value=report), \
                    patch("core.paramnative.probe_catalog", return_value=fake.host_catalog) as probe:
                result = AppBackend._adopt_current_ae(object())
                self.assertTrue(result["adopted"])
                self.assertEqual({call.args[0] for call in opened.call_args_list}, {fake.pid})
                validated = adapter.adapted_runtime_profile(process, fake.modules, tmp)
                self.assertEqual(validated.version, fake.version)
                self.assertEqual(validated.registry_vector, fake.profile.registry_vector)
                self.assertFalse(validated.popup_layout_verified)
                report["candidate"] = aescan._profile_to_candidate(replace(profile, popup_layout_verified=True))
                upgraded = AppBackend._adopt_current_ae(object())
                self.assertTrue(upgraded["popup_upgraded"])
                self.assertEqual(probe.call_count, 1)
                self.assertEqual(adapter.adapted_runtime_profile(process, fake.modules, tmp),
                                 replace(validated, popup_layout_verified=True))
                self.assertEqual(AppBackend._adopt_current_ae(object()), {})
                draw.write_bytes(b"different binary")
                self.assertIsNone(adapter.adapted_runtime_profile(process, fake.modules, tmp))
