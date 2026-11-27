import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime.managed_guard import LanguageEngine


class GuardMappingCacheTest(unittest.TestCase):
    scoped_key = "effect\x1fparameter\x1fOff"

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.base = self.root / "extra_overrides.json"
        self.scoped = self.root / "scoped_popup.json"
        self.engine = LanguageEngine("", str(self.root), str(self.root))
        self.engine.draw = mock.Mock()
        self.engine.popup_error = "params-only"

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def refresh(self):
        self.assertEqual(self.engine.refresh("on"), (True, ""))

    def test_unchanged_documents_are_parsed_once(self):
        self.write(self.base, {"Off": "关"})
        self.write(self.scoped, {self.scoped_key: "关闭"})
        with mock.patch("json.loads", wraps=json.loads) as parse:
            self.refresh()
            self.assertEqual(parse.call_count, 2)
            for _ in range(8):
                self.refresh()
            self.assertEqual(parse.call_count, 2)
        self.engine.draw.set_map.assert_called_once_with(
            {"Off": "关", self.scoped_key: "关闭"})

    def test_replacement_and_in_place_edit_with_same_size_and_time(self):
        self.write(self.base, {"Off": "甲"})
        self.write(self.scoped, {self.scoped_key: "乙"})
        self.refresh()

        replacement = self.root / "pending.json"
        stamp = self.base.stat()
        self.write(replacement, {"Off": "丙"})
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        os.replace(replacement, self.base)
        self.refresh()
        self.assertEqual(self.engine.mapping, {"Off": "丙", self.scoped_key: "乙"})

        stamp = self.scoped.stat()
        self.write(self.scoped, {self.scoped_key: "丁"})
        os.utime(self.scoped, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.refresh()
        self.assertEqual(self.engine.mapping, {"Off": "丙", self.scoped_key: "丁"})

    def test_unavailable_or_invalid_base_retains_last_good_map_and_retries(self):
        self.write(self.base, {"Off": "关"})
        self.refresh()
        self.base.unlink()
        for invalid in (None, b"{", b"[]", b"\xef\xbb\xbf{}"):
            with self.subTest(invalid=invalid):
                if invalid is not None:
                    self.base.write_bytes(invalid)
                ready, detail = self.engine.refresh("on")
                self.assertFalse(ready)
                self.assertIn("dictionary-unavailable:", detail)
                self.assertTrue(self.engine.map_error)
                self.assertEqual(self.engine.mapping, {"Off": "关"})
        self.engine.draw.set_map.assert_called_once()
        self.write(self.base, {})
        self.refresh()
        self.assertEqual(self.engine.map_error, "")
        self.assertEqual(self.engine.mapping, {})
        self.engine.draw.set_map.assert_called_with({})

    def test_scoped_file_optional_and_reloaded_after_read_failure(self):
        self.write(self.base, {"Off": "关"})
        self.refresh()
        self.write(self.scoped, {self.scoped_key: "专属"})
        self.refresh()
        self.assertEqual(self.engine.mapping[self.scoped_key], "专属")
        self.scoped.write_bytes(b"{")
        self.refresh()
        self.assertEqual(self.engine.mapping, {"Off": "关"})
        self.write(self.scoped, {self.scoped_key: "专属"})
        self.refresh()
        self.assertEqual(self.engine.mapping[self.scoped_key], "专属")
        self.scoped.unlink()
        self.refresh()
        self.assertEqual(self.engine.mapping, {"Off": "关"})

    def test_equivalent_input_does_not_redraw_or_publish_again(self):
        self.engine.host = {"pid": 123}
        self.write(self.base, {"Off": "关"})
        with mock.patch("runtime.managed_guard._redraw_host") as redraw:
            self.refresh()
            self.base.write_text('{"Off":"关"}', encoding="utf-8")
            self.refresh()
            self.refresh()
        self.engine.draw.set_map.assert_called_once()
        redraw.assert_called_once()

    def test_transient_scoped_parse_failure_retries_identical_bytes(self):
        self.write(self.base, {"Off": "关"})
        self.write(self.scoped, {self.scoped_key: "专属"})
        parse = json.loads

        def reject_scoped_once(text):
            if "effect" in text:
                raise ValueError("temporary failure")
            return parse(text)

        with mock.patch("json.loads", side_effect=reject_scoped_once):
            self.refresh()
        self.assertEqual(self.engine.mapping, {"Off": "关"})
        self.refresh()
        self.assertEqual(self.engine.mapping[self.scoped_key], "专属")

    def test_warm_cache_still_checks_read_access(self):
        self.write(self.base, {"Off": "关"})
        self.refresh()
        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            ready, detail = self.engine.refresh("on")
        self.assertFalse(ready)
        self.assertIn("dictionary-unavailable:", detail)
        self.assertEqual(self.engine.mapping, {"Off": "关"})
        self.refresh()
        self.assertEqual(self.engine.map_error, "")
        self.engine.draw.set_map.assert_called_once()
