# -*- coding: utf-8 -*-
"""补词采集落盘（受控 runtime → all_param_names/renamed/rename_miss/enum）。

受控运行时在绘制期替换显示名。若缺少采集快照，补词页无法读到本次会话见过的参数。
验证启动清理和观察结果写入，避免读取过期快照。

这里只锁「落盘契约」，不启动 AE、不注入 frida：
  · _write_capture 写出的四个文件内容/格式正确；
  · 单会话观察为空时不写垃圾、启动时清掉上一会话的残留。
"""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


def _load():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "runtime", "managed_guard.py")
    spec = importlib.util.spec_from_file_location("managed_guard_capture", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class WriteCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_capture_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self, name):
        with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
            return json.load(f)

    def test_writes_all_four_files(self):
        obs = {"rev": 7,
               "seen": {"Amount": 1, " Phase RGB": 1, "Mult": 1},
               "applied": {"Amount": 1},
               "miss": {" Phase RGB": "capacity"},
               "enum_seen": {"Mult": 1}}
        self.assertTrue(self.m._write_capture(self.tmp, obs))
        self.assertEqual(self._read("all_param_names.json"),
                         [" Phase RGB", "Amount", "Mult"])
        self.assertEqual(self._read("renamed_names.json"), ["Amount"])
        self.assertEqual(self._read("rename_miss.json"),
                         [{"en": " Phase RGB", "why": "capacity"}])
        self.assertEqual(self._read("enum_missing.json"), ["Mult"])

    def test_empty_observation_writes_valid_empty_arrays(self):
        # 空观察必须写成合法的空数组：补词页据此区分「采集到 0 个」与
        # 「文件不存在」，不会把上一会话的旧内容继续展示。
        self.assertTrue(self.m._write_capture(self.tmp, {}))
        for name in ("all_param_names.json", "renamed_names.json",
                     "rename_miss.json", "enum_missing.json"):
            self.assertEqual(self._read(name), [], name)

    def test_miss_is_capped_and_ordered(self):
        miss = {"w%04d" % i: "capacity" for i in range(2500)}
        obs = {"seen": {}, "applied": {}, "miss": miss, "enum_seen": {}}
        self.assertTrue(self.m._write_capture(self.tmp, obs))
        rows = self._read("rename_miss.json")
        self.assertEqual(len(rows), 2000)
        self.assertEqual(rows[0]["en"], "w0000")
        self.assertEqual(rows[-1]["en"], "w1999")

    def test_bad_payload_does_not_raise(self):
        self.assertFalse(self.m._write_capture(self.tmp, {"seen": 5}))

    def test_start_clears_previous_session_files(self):
        runtime = _load()
        output = Path(self.tmp)
        runtime.OUT = output
        runtime.CTL = output / "control"
        runtime.TMP = output / "tmp"
        # 模拟启动阶段无法获取互斥锁，验证清理后退出，全程无需连接 AE。
        runtime._mutex = lambda _: None
        captures = ("all_param_names.json", "renamed_names.json",
                    "rename_miss.json", "enum_missing.json", "label_diagnostics.json")
        for name in captures:
            (output / name).write_text("stale", encoding="utf-8")
        unrelated = output / "unrelated.txt"
        unrelated.write_text("keep", encoding="utf-8")

        self.assertEqual(runtime.main([]), 4)
        for name in captures:
            self.assertFalse((output / name).exists(), name)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


class LoopPublishTest(unittest.TestCase):
    """守护主循环真的把观察结果落盘（真起进程，假 LanguageEngine）。"""

    def test_loop_writes_capture_when_host_observes(self):
        m = _load()
        tmp = tempfile.mkdtemp(prefix="aeloc_looppub_")
        try:
            root = Path(tmp)
            out, ctl = root / "out", root / "control"
            ctl.mkdir()
            for k, v in {"DATA_ROOT": root, "RUNTIME_DIR": root, "OUT": out,
                         "CTL": ctl, "TMP": root / "tmp", "DICT": root / "dict",
                         "STOP_FLAG": root / "dict" / "STOP_NOW.flag",
                         "LOG": out / "g.log", "LOCK": out / "lock",
                         "LOCK_ID": out / "lock.json",
                         "REQ": ctl / "request.json", "ST": ctl / "status.json",
                         "DUMP_NOW": out / "DUMP_NOW.flag"}.items():
                setattr(m, k, str(v))
            m._mutex = lambda r: 1
            m._proc_identity = lambda pid: {"pid": pid, "ctime": 1,
                                            "image": r"C:\test\python.exe"}
            obs = {"rev": 3, "seen": {"Amount": 1, "Radius": 1},
                   "applied": {"Amount": 1}, "miss": {"Radius": "capacity"},
                   "enum_seen": {}}
            stop = threading.Event()

            class Language:
                def __init__(self, *a):
                    self.host = None
                    # draw.stats() 是主循环判断「有没有新观察」的唯一来源；
                    # 用空实现会让采集永远停在 rev=0（真实实现的回归点）。
                    self.draw = type("D", (), {
                        "stats": staticmethod(lambda: {"seen_rev": 3})})()
                    self.popup = None
                    self.mapping = {}
                    self.popup_reasons = {}
                    self.popup_unsupported = 0

                def observe(self):
                    return obs

                def popup_activity(self, mode):
                    return None

                def refresh(self, mode):
                    return True, ""

                def apply(self, mode, host, gen):
                    return True, ""

                def close(self):
                    stop.set()
                    return True

            m.LanguageEngine = Language
            (ctl / "request.json").write_text(json.dumps({
                "protocol": m.PROTOCOL, "mode": "stop", "gen": 1,
                "request_id": "stop"}), encoding="utf-8")
            t = threading.Thread(target=m.main, args=([],), daemon=True)
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive(), "guard did not exit")
            self.assertTrue(stop.is_set())
            self.assertEqual(self._load_json(out / "all_param_names.json"),
                             ["Amount", "Radius"])
            self.assertEqual(self._load_json(out / "renamed_names.json"),
                             ["Amount"])
            self.assertEqual(self._load_json(out / "rename_miss.json"),
                             [{"en": "Radius", "why": "capacity"}])
            self.assertEqual(self._load_json(out / "enum_missing.json"), [])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _load_json(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)


class PopupEnumDerivationTest(unittest.TestCase):
    """下拉缺词来自 typed 弹出记录（旧 guard 的 enum_missing.json 同源）。"""

    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def _engine(self, records, mapping=None, obs=None):
        eng = object.__new__(self.m.LanguageEngine)
        eng.draw = type("D", (), {"observe": staticmethod(
            lambda: obs if obs is not None else
            {"rev": 1, "seen": {}, "applied": {}, "miss": {},
             "enum_seen": {}})})()
        eng.popup = type("P", (), {"records": records})()
        eng.mapping = mapping or {}
        eng.draw_error = ""
        return eng

    def test_untranslated_options_are_reported_translated_are_not(self):
        recs = [{"text": "Off|On|Custom Thing"},
                {"text": "关|开"}]
        eng = self._engine(recs, mapping={"Off": "关", "On": "开"})
        out = eng.observe()
        self.assertEqual(sorted(out["enum_seen"]), ["Custom Thing"])

    def test_already_chinese_options_are_skipped(self):
        eng = self._engine([{"text": "关|On"}], mapping={})
        self.assertEqual(sorted(eng.observe()["enum_seen"]), ["On"])

    def test_no_draw_hook_returns_empty(self):
        eng = object.__new__(self.m.LanguageEngine)
        eng.draw = None
        self.assertEqual(eng.observe(), {})

    def test_revision_tracks_popup_records(self):
        eng = self._engine([{"text": "A Thing|B Thing"}], mapping={})
        first = eng.observe()["rev"]
        eng.popup = type("P", (), {"records": [{"text": "A Thing|B Thing"},
                                              {"text": "C Thing|D Thing"}]})()
        self.assertGreater(eng.observe()["rev"], first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
