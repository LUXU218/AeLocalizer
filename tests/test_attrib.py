"""名字归属 + 引擎词典过滤测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import attrib  # noqa: E402
from core.enginedict import (  # noqa: E402
    build_engine_dict, clear_engine_dict, engine_dict_ready, filter_entries,
)


class TestExtractWords(unittest.TestCase):
    def test_basic(self):
        words = attrib.extract_words(b"Blur Amount\x00Rotate 90")
        self.assertEqual(words, ["Blur", "Amount", "Rotate"])

    def test_skips_leading_digits(self):
        self.assertEqual(attrib.extract_words(b"12 34 abc"), ["abc"])

    def test_keeps_inner_punct(self):
        words = attrib.extract_words(b"Use Layer&Matte+Source")
        self.assertEqual(words, ["Use", "Layer&Matte+Source"])

    def test_keeps_underscore(self):
        # 实测：dissolve_speed 这类带下划线的名字必须整词保留
        words = attrib.extract_words(b"dissolve_speed glint")
        self.assertEqual(words, ["dissolve_speed", "glint"])

    def test_empty(self):
        self.assertEqual(attrib.extract_words(b""), [])


class TestFindNames(unittest.TestCase):
    def test_single_word(self):
        hit = attrib.find_names_in_words(["Blur", "Amount"], {"Blur"})
        self.assertEqual(hit, {"Blur"})

    def test_multi_word_ngram(self):
        hit = attrib.find_names_in_words(["Load", "Preset", "Now"], {"Load Preset"})
        self.assertEqual(hit, {"Load Preset"})

    def test_no_false_prefix(self):
        hit = attrib.find_names_in_words(["Load", "Other"], {"Load Preset"})
        self.assertEqual(hit, set())

    def test_max_words_respected(self):
        hit = attrib.find_names_in_words(["a", "b", "c", "d"], {"a b c d"}, max_words=2)
        self.assertEqual(hit, set())

    def test_embedded_in_longer_text(self):
        data = b"dissolve_amt ( shared percent ) ) dissolve_speed glint_brightness"
        words = attrib.extract_words(data)
        hit = attrib.find_names_in_words(words, {"dissolve_speed"})
        self.assertIn("dissolve_speed", hit)


class TestScanFile(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(attrib.scan_file("Z:/nope/none.bin", {"A"}), set())

    def test_real_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.aex")
            with open(p, "wb") as f:
                f.write(b"\x00\x00Blur Amount\x00Rotate\x00")
            hit = attrib.scan_file(p, {"Blur Amount", "Rotate", "Nope"})
            self.assertEqual(hit, {"Blur Amount", "Rotate"})


class TestBuildIndex(unittest.TestCase):
    def _mk(self, td, name, text):
        p = os.path.join(td, name)
        with open(p, "wb") as f:
            f.write(text)
        return p

    def test_shared_name_gets_both_groups(self):
        with tempfile.TemporaryDirectory() as td:
            a = self._mk(td, "a.aex", b"Amount Blur")
            b = self._mk(td, "b.aex", b"Amount Blur")
            idx = attrib.build_index({"ga": [a], "gb": [b]}, {"Amount", "Blur"})
            self.assertEqual(sorted(idx["Amount"]), ["ga", "gb"])

    def test_exclusive_name(self):
        with tempfile.TemporaryDirectory() as td:
            a = self._mk(td, "a.aex", b"OnlyA")
            b = self._mk(td, "b.aex", b"OnlyB")
            idx = attrib.build_index({"ga": [a], "gb": [b]}, {"OnlyA", "OnlyB"})
            self.assertEqual(idx["OnlyA"], ["ga"])
            self.assertEqual(idx["OnlyB"], ["gb"])

    def test_keys_normalized(self):
        # ★ 回归：词典里大量条目带首尾空格（" Amount "），
        #   不归一化会永远归属不到 -> 被当成未归属放行
        with tempfile.TemporaryDirectory() as td:
            a = self._mk(td, "a.aex", b"Amount")
            idx = attrib.build_index({"ga": [a]}, {" Amount ", "Aspect"})
            self.assertIn("Amount", idx)

    def test_shared_files_apply_to_all_vendor_groups(self):
        with tempfile.TemporaryDirectory() as td:
            a = self._mk(td, "a.aex", b"UniqueA")
            dll = self._mk(td, "engine.dll", b"Shared Param")
            idx = attrib.build_index(
                {"ga": [a], "gb": [], "gc": []},
                {"UniqueA", "Shared Param"},
                shared_files={"ga|gb|gc": [dll]},
            )
            self.assertEqual(sorted(idx["Shared Param"]), ["ga", "gb", "gc"])

    def test_stats(self):
        st = attrib.index_stats({"A": ["g1"], "B": ["g1", "g2"]})
        self.assertEqual(st["names"], 2)
        self.assertEqual(st["shared"], 1)
        self.assertEqual(st["exclusive"], 1)


class TestDiscoverSharedFiles(unittest.TestCase):
    def test_missing_dirs_ignored(self):
        out = attrib.discover_shared_files(
            [("g1", "NoVendor")], vendor_dirs={"NoVendor": ["Z:/nope"]}
        )
        self.assertEqual(out, {})

    def test_collects_dll_and_aex(self):
        with tempfile.TemporaryDirectory() as td:
            for n in ("a.dll", "b.aex", "c.txt"):
                with open(os.path.join(td, n), "wb") as f:
                    f.write(b"x")
            out = attrib.discover_shared_files(
                [("g1", "V"), ("g2", "V")], vendor_dirs={"V": [td]}
            )
            self.assertEqual(len(out), 1)
            key = next(iter(out))
            self.assertEqual(key, "g1|g2")
            self.assertEqual(len(out[key]), 2)


class TestIndexCache(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "sub", "attrib.json")
            attrib.save_index(p, {"A": ["g1"]}, scanned=3)
            self.assertEqual(attrib.load_index(p), {"A": ["g1"]})

    def test_missing_returns_none(self):
        self.assertIsNone(attrib.load_index("Z:/nope/attrib.json"))

    def test_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "bad.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertIsNone(attrib.load_index(p))

    def test_wrong_schema_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "old.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"schema": "other/1", "index": {}}')
            self.assertIsNone(attrib.load_index(p))


class TestFilterEntries(unittest.TestCase):
    ENTRIES = {"Blur": "模糊", "Amount": "数量", "Unknown": "未知"}

    def test_owned_and_selected_kept(self):
        out = filter_entries(self.ENTRIES, {"Blur": ["g1"]}, {"g1"})
        self.assertIn("Blur", out)

    def test_owned_but_not_selected_dropped(self):
        out = filter_entries(self.ENTRIES, {"Blur": ["g1"]}, {"g2"})
        self.assertNotIn("Blur", out)

    def test_unowned_kept_by_default(self):
        out = filter_entries(self.ENTRIES, {}, {"g1"})
        self.assertIn("Unknown", out)

    def test_unowned_dropped_when_strict(self):
        out = filter_entries(self.ENTRIES, {}, {"g1"}, keep_unowned=False)
        self.assertNotIn("Unknown", out)

    def test_multi_owner_any_selected(self):
        out = filter_entries({"Blur": "模糊"}, {"Blur": ["g1", "g2"]}, {"g2"})
        self.assertIn("Blur", out)

    def test_whitespace_normalized_lookup(self):
        out = filter_entries({"  Blur  ": "模糊"}, {"Blur": ["g1"]}, {"g1"})
        self.assertIn("  Blur  ", out)

    def test_empty_values_skipped(self):
        self.assertEqual(filter_entries({"A": ""}, {}, {"g1"}), {})


class TestBuildEngineDict(unittest.TestCase):
    def test_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            res = build_engine_dict({"Blur": "模糊"}, td)
            self.assertEqual(res.entries, 1)
            self.assertTrue(os.path.exists(res.path))
            self.assertEqual(engine_dict_ready(td), 1)

    def test_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            res = build_engine_dict(
                {"Blur": "模糊", "Amount": "数量"}, td,
                attribution={"Blur": ["g1"]},
                selected_groups={"g1"}, keep_unowned=False,
            )
            self.assertEqual(res.entries, 1)
            self.assertEqual(res.dropped, 1)

    def test_clear(self):
        with tempfile.TemporaryDirectory() as td:
            build_engine_dict({"A": "甲"}, td)
            self.assertTrue(clear_engine_dict(td))
            self.assertEqual(engine_dict_ready(td), 0)

    def test_clear_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(clear_engine_dict(td))

    def test_ready_on_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(engine_dict_ready(td), 0)
