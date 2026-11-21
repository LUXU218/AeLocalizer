import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from core.panel_localization import panel_localizer, signed_cep


class PanelIntegrationTests(unittest.TestCase):
    def test_plain_jsx_copy_and_restore(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root / "example.jsx"
            original = b'var w = new Window("palette", "Demo"); w.add("button", undefined, "Apply");'
            source.write_bytes(original)
            localizer = panel_localizer.Localizer(panel_localizer.inspect(source), root / "data")
            report = localizer.extract()
            words = {"Apply": "应用"}
            copy = root / "copy.jsx"
            localizer.export_jsx(report, words, copy)
            self.assertEqual(source.read_bytes(), original)
            labels = panel_localizer.Localizer(panel_localizer.inspect(copy), root / "data").extract()
            self.assertIn("Demo", [term["text"] for term in labels["terms"]])
            with self.assertRaises(ValueError):
                localizer.export_jsx(report, words, copy)
            localizer.apply(report, words)
            self.assertNotEqual(source.read_bytes(), original)
            localizer.restore()
            self.assertEqual(source.read_bytes(), original)

    def test_cep_overlay_is_packaged_from_our_asset_root(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "CSXS").mkdir()
            (root / "CSXS" / "manifest.xml").write_text(
                '<ExtensionManifest ExtensionBundleId="demo.panel"><MainPath>index.html</MainPath>'
                '</ExtensionManifest>', encoding="utf-8")
            page = root / "index.html"
            page.write_text("<html><body><button>Apply</button></body></html>", encoding="utf-8")
            localizer = panel_localizer.Localizer(panel_localizer.inspect(root), root / "data")
            report = localizer.extract()
            original = page.read_bytes()
            localizer.apply(report, {"Apply": "应用"})
            self.assertIn(panel_localizer.OVERLAY, page.read_text(encoding="utf-8"))
            self.assertIn("MutationObserver", (root / panel_localizer.OVERLAY).read_text())
            localizer.restore()
            self.assertEqual(page.read_bytes(), original)
            self.assertFalse((root / panel_localizer.OVERLAY).exists())

    def test_signed_cep_is_installed_as_a_separate_copy(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root / "original"
            (source / "CSXS").mkdir(parents=True)
            (source / "META-INF").mkdir()
            manifest = source / "CSXS/manifest.xml"
            manifest.write_text(
                '<ExtensionManifest ExtensionBundleId="demo.panel">'
                '<ExtensionList><Extension Id="demo.panel.main"/></ExtensionList>'
                '<DispatchInfoList><Extension Id="demo.panel.main"><DispatchInfo>'
                '<Resources><MainPath>index.html</MainPath></Resources>'
                '<UI><Menu>Demo</Menu></UI></DispatchInfo></Extension></DispatchInfoList>'
                '</ExtensionManifest>', encoding="utf-8")
            signature = source / "META-INF/signatures.xml"
            signature.write_bytes(b"original signature")
            page = source / "index.html"
            page.write_bytes(b"<html><body><button>Apply</button></body></html>")
            localizer = panel_localizer.Localizer(panel_localizer.inspect(source), root / "data")
            report = localizer.extract()
            translations = {"Apply": "应用"}
            with self.assertRaises(ValueError):
                localizer.apply(report, translations)
            before = {file: file.read_bytes() for file in (page, manifest, signature)}

            def signed_fixture(signer, staged, folder):
                package = folder / "fixture.zxp"
                with ZipFile(package, "w") as archive:
                    for file in staged.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(staged))
                    archive.writestr("META-INF/signatures.xml", "new signature")
                return package

            with patch.dict(os.environ, APPDATA=str(root / "profile")), \
                    patch.object(signed_cep, "verified_signer", return_value=root / "fake.exe"), \
                    patch.object(signed_cep, "_sign", side_effect=signed_fixture):
                menus = signed_cep.install_copy(localizer, report, translations, "unused")
                self.assertEqual(menus, "Demo (简体中文)")
                self.assertTrue(signed_cep.is_installed(localizer.target))
                copy, = (root / "profile/Adobe/CEP/extensions").iterdir()
                self.assertNotEqual(panel_localizer.inspect(copy).identifier, localizer.target.identifier)
                self.assertIn(panel_localizer.OVERLAY.encode(), (copy / "index.html").read_bytes())
                self.assertEqual((copy / "META-INF/signatures.xml").read_bytes(), b"new signature")
                signed_cep.install_copy(localizer, report, {"Apply": "确定"}, "unused")
                self.assertIn(r"\u786e\u5b9a", (copy / panel_localizer.OVERLAY).read_text())
                discovered, _ = panel_localizer.discover(extra=[source])
                self.assertIn(localizer.target, discovered)
                self.assertFalse(any(Path(item.root) == copy for item in discovered))
                signed_cep.remove_copy(localizer.target)
                self.assertFalse(signed_cep.is_installed(localizer.target))
            self.assertEqual({file: file.read_bytes() for file in before}, before)
