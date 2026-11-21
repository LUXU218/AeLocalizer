import tempfile
import unittest
from pathlib import Path

from core.panel_localization import panel_localizer


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
