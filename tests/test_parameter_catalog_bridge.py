import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from app.backend import AppBackend
from core import adapter, paramcatalog, paramnative, proc


class ParameterCatalogBridgeTests(unittest.TestCase):
    def test_read_only_request_succeeds_with_only_the_identity_helper(self):
        process = SimpleNamespace(pid=123, creation_time=456, image="AfterFX.exe")
        profile = SimpleNamespace(version="26.3.0.87")
        parameters = [{"id": '["CC Light Wipe","Shape"]', "name": "Shape"}]
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity = root / "ae_host_identity.dll"
            identity.touch()

            def start(args, **_kwargs):
                source = Path(args[2]).read_text(encoding="ascii")
                request, _ = json.JSONDecoder().raw_decode(source.split("var request=", 1)[1])
                self.assertEqual(request["hostVersion"], "26.3x87")
                self.assertEqual(request["identityHelper"], identity.as_posix())
                self.assertNotIn("helper", request)
                self.assertNotIn("adapter", request)
                Path(request["output"]).write_text(json.dumps({
                    "hostIdentity": paramnative.target(process),
                    "requestId": request["requestId"], "action": "catalog",
                    "parameters": parameters,
                }), encoding="utf8")
                return Mock(pid=124, poll=Mock(return_value=0))

            with patch.object(paramnative, "native_root", return_value=root), \
                    patch.object(paramnative, "relay_script", side_effect=lambda folder: folder / "request.jsx"), \
                    patch.object(paramnative.subprocess, "Popen", side_effect=start):
                reply = paramnative.run_native(process, [], action="catalog", data_root=root, profile=profile)
                self.assertEqual(reply["parameters"], parameters)
                with self.assertRaisesRegex(RuntimeError, "参数辅助模块缺失"):
                    paramnative.run_native(process, [], action="labels", data_root=root)
                identity.unlink()
                with self.assertRaisesRegex(RuntimeError, "宿主身份模块缺失"):
                    paramnative.run_native(process, [], action="catalog", data_root=root, profile=profile)
            self.assertEqual(list((root / "ae-bridge").iterdir()), [])

    def test_full_enumeration_reports_script_errors_instead_of_an_empty_success(self):
        backend = SimpleNamespace(_run=Mock(), _store=Mock())
        backend._store.resolve.return_value = {"Shape": SimpleNamespace(zh="形状")}
        editor = {"pid": 123, "fileVersion": "26.3.0.87"}
        with patch.object(proc, "list_ae_hosts", return_value=[editor]), \
                patch.object(proc, "Process", return_value=MagicMock()), \
                patch.object(paramcatalog, "enumerate_parameters", side_effect=RuntimeError("未适配参数枚举")), \
                patch.object(adapter, "get_profile", return_value=object()), \
                patch.object(paramnative, "run_native") as bridge:
            AppBackend.captureCatalog(backend)
            work = backend._run.call_args.args[0]
            bridge.return_value = {"parameters": [
                {"id": '["CC Light Wipe","Shape"]', "name": "Shape"},
                {"id": '["S_Posterize","Dither"]', "name": "Dither"},
            ]}
            self.assertEqual(work(), {
                "seen": 2, "items": [{"en": "Dither", "kind": "param", "effect": "S_Posterize"}],
                "source": "脚本桥·工程内已应用效果",
            })
            bridge.return_value["error"] = "AE 构建不匹配"
            with self.assertRaisesRegex(RuntimeError, "读取 AE 参数名失败：AE 构建不匹配"):
                work()
