"""提取未汉化参数（captureMissingParams / commitCapturedWords）测试。

★★ 沙盒纪律：DATA_ROOT 换临时目录；引擎采集文件也用假的 <hook>/out/，
   绝不碰真实 %LOCALAPPDATA%\\AeLocalizer 和真实 hook 目录。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestCapture(unittest.TestCase):
    """补词面板：提取过滤规则 + 记入词典。"""

    @classmethod
    def setUpClass(cls):
        import unittest.mock as mock

        import app.backend as backend_mod
        from app.backend import AppBackend
        from core import engine

        cls._tmpdir = tempfile.mkdtemp(prefix="aeloc_test_capture_")
        cls._old_root = getattr(backend_mod, "DATA_ROOT", "")
        backend_mod.DATA_ROOT = cls._tmpdir

        # ---- 假的 hook/out/all_param_names.json ----
        cls._hookdir = os.path.join(cls._tmpdir, "hook")
        outdir = os.path.join(cls._hookdir, "out")
        os.makedirs(outdir, exist_ok=True)
        cls._names = [
            "Output Size", "Custom Width", "Generic Slider",      # 前2缺词, 占位名
            "S_Glow", "Blur Amount", "Spaced   Name",             # 效果名, 已有译名, 多余空格
        ]
        with open(os.path.join(outdir, "all_param_names.json"), "w",
                  encoding="utf-8") as f:
            json.dump(cls._names, f, ensure_ascii=False)
        # 引擎采集的枚举缺词（下拉值）
        with open(os.path.join(outdir, "enum_missing.json"), "w",
                  encoding="utf-8") as f:
            json.dump(["Quick", "Better", "1024", "Blur Amount", "Output Size"],
                      f, ensure_ascii=False)
        cls._guard = os.path.join(cls._hookdir, "guard_uni.py")
        with open(cls._guard, "w", encoding="utf-8") as f:
            f.write("# fake guard\n")

        cls._patcher = mock.patch.object(engine, "find_guard",
                                         return_value=(cls._guard, ""))
        cls._patcher.start()

        cls.b = AppBackend()
        # 效果名行（captureMissingParams 用它排除插件名）
        cls.b._nameRows = [SimpleNamespace(en="S_Glow")]
        # 词典里已有译名的参数
        cls.b._store.set_builtin({"Blur Amount": "模糊量"})

    @classmethod
    def tearDownClass(cls):
        import app.backend as backend_mod
        from core import engine

        cls._patcher.stop()
        if cls._old_root:
            backend_mod.DATA_ROOT = cls._old_root
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_capture_filters(self):
        r = self.b.captureMissingParams()
        self.assertEqual(r["seen"], 6)
        ens = [it["en"] for it in r["items"]]
        # 缺词保留（多余空格被归一）
        self.assertIn("Output Size", ens)
        self.assertIn("Custom Width", ens)
        self.assertIn("Spaced Name", ens)
        # 占位名排除; 词典已有译名的词 2026-11-02 起改判 stuck
        # (词典有词 ≠ AE 里真改了名 —— 排尾「未生效」,不再直接隐藏)
        self.assertNotIn("Generic Slider", ens)
        by_en = {it["en"]: it for it in r["items"]}
        self.assertTrue(by_en["Blur Amount"].get("stuck"))
        # ★ 2026-11-01:效果名碰撞不再硬排除(会误杀同名真参数)→ 排尾隔离
        self.assertTrue(by_en["S_Glow"].get("junk"))
        self.assertEqual(ens[-1], "S_Glow")

    def test_checkbox_channel_evidence(self):
        from unittest.mock import patch
        classic = {"profile": {"checkbox_sites": [{}]}, "pre_hooked": True,
                   "post_hooked": True, "call_intact": True}
        for draw, expected in [
            ({}, "未验证"), ({"checkbox_widget": False}, "未验证"),
            (dict(classic, checkbox_error="caption changed", checkbox_observed=False), "未通过校验"),
            (dict(classic, post_hooked=False), "未验证"),
            (dict(classic, checkbox_observed=False), "尚未观察"),
            ({"checkbox_widget": True, "checkbox_observed": False}, "尚未观察"),
            ({"checkbox_widget": True}, "尚未提供"),
            ({"checkbox_widget": True, "checkbox_observed": True}, None),
        ]:
            with self.subTest(draw=draw), patch("app.backend.engine.control_status",
                    return_value={"control_ready": True, "draw_stats": draw}):
                messages = self.b._capture_channel_status()
                if expected is None:
                    self.assertEqual(messages, [])
                else:
                    self.assertIn(expected, messages[0])

    def test_capture_preserves_independent_widget_evidence(self):
        from unittest.mock import patch
        with patch.object(self.b, "_read_capture_labels", return_value=[
            {"kind": "param", "en": "Blur Amount", "painted": True},
            {"kind": "checkbox", "en": "Blur Amount", "painted": False},
            {"kind": "enum", "en": "New Option", "painted": False},
        ]):
            rows = {(x["en"], x["kind"]): x for x in self.b.captureMissingParams()["items"]}
        self.assertNotIn(("Blur Amount", "param"), rows)
        self.assertEqual(rows["Blur Amount", "checkbox"]["status"], "not_applied")
        self.assertEqual(rows["New Option", "enum"]["status"], "missing")

    def test_scoped_override_roundtrip_keeps_global_dictionary_unchanged(self):
        path = Path(self._hookdir, "out", "label_diagnostics.json")
        path.write_text(json.dumps({"scopes": [
            {"effect": "A", "parameter": "A-1", "en": "Screen"},
            {"effect": "B", "parameter": "B-1", "en": "Screen"},
        ]}), encoding="utf-8")
        try:
            self.assertTrue(self.b.saveEffectOption("A", "A-1", "Screen", "仅甲"))
            self.assertFalse(self.b.saveEffectOption("A", "A-2", "Screen", "不保存"))
            values = {(x["effect"], x["parameter"]): x["zh"] for x in self.b.captureEffectOptions()}
            self.assertEqual(values, {("A", "A-1"): "仅甲", ("B", "B-1"): ""})
            self.assertTrue(self.b.saveEffectOption("A", "A-1", "Screen", ""))
            self.assertEqual(self.b._read_scoped_popup(), {})
        finally:
            path.unlink()

    def test_verified_menu_keys_require_explicit_lookup_and_do_not_cross_effects(self):
        path = Path(self._hookdir, "out", "label_diagnostics.json")
        try:
            self.assertFalse(path.exists())
            empty = self.b.effectOptionState(False)
            self.assertEqual(empty["options"], [])
            self.assertIn("没有可读取", empty["message"])
            path.write_text(json.dumps({"scopes": [], "rows": [{"kind": "enum"}]}))
            self.assertIn("尚无可确认", self.b.effectOptionState(False)["message"])
            keys = self.b.effectOptionState(True)["options"]
            self.assertEqual([(r["effect"], r["parameter"], r["en"]) for r in keys],
                             [("S_Glow", "S_Glow-0530", "Union"),
                              ("S_Mosaic", "S_Mosaic-0530", "Union")])
            self.assertFalse(self.b.saveEffectOption("S_Mosaic", "S_Glow-0530", "Union", "错误"))
            self.assertFalse(self.b.saveEffectOption("S_Glow", "S_Glow-0530", "1", "错误"))
            self.assertTrue(self.b.saveEffectOption("S_Glow", "S_Glow-0530", "Union", "仅发光"))
            self.assertEqual([r["zh"] for r in self.b.effectOptionState(True)["options"]],
                             ["仅发光", ""])
            self.assertTrue(self.b.saveEffectOption("S_Glow", "S_Glow-0530", "Union", ""))
            self.assertEqual(self.b.captureEffectOptions(), [])
        finally:
            path.unlink(missing_ok=True)

    def test_capture_includes_enum_missing(self):
        """★ 2026-10-26 通用下拉：枚举缺词并入提取列表（kind=enum）。"""
        r = self.b.captureMissingParams()
        kinds = {it["en"]: it["kind"] for it in r["items"]}
        # 下拉缺词进入列表
        self.assertEqual(kinds.get("Quick"), "enum")
        self.assertEqual(kinds.get("Better"), "enum")
        # 参数名带 kind=param
        self.assertEqual(kinds.get("Output Size"), "param")
        # 纯数字（无字母）不收；与参数名重复只出现一次
        self.assertNotIn("1024", kinds)
        # 已有译名的词 2026-11-02 起作为 stuck 参数列出(kind 仍是 param,
        # 枚举通道不重复收它)
        self.assertNotEqual(kinds.get("Blur Amount"), "enum")
        self.assertEqual([it["en"] for it in r["items"]].count("Output Size"), 1)

    def test_capture_stuck_engine_dict_not_renamed(self):
        """★ 2026-11-02 DeepClear: 词典有词但 AE 改名未生效 → 继续列出(stuck 排尾)。

        词典有词 ≠ AE 里真改了名 —— 守护 renamed_names.json 是 ANSI 改名
        成功的白名单; 不在名单里的引擎已覆盖词列为 stuck(AI 补译跳过,
        手动入口保留), 排在正常缺词之后、junk 之前。
        """
        outdir = os.path.join(self._hookdir, "out")
        names_path = os.path.join(outdir, "all_param_names.json")
        with open(names_path, encoding="utf-8") as f:
            old_names = f.read()
        with open(os.path.join(outdir, "renamed_names.json"), "w",
                  encoding="utf-8") as f:
            json.dump(["Denoise"], f)
        eng_dir = os.path.join(self._tmpdir, "engine_dict")
        try:
            with open(names_path, "w", encoding="utf-8") as f:
                json.dump(["Deblur Mode", "Denoise", "Custom Width",
                           "Blur Amount"], f, ensure_ascii=False)
            from core.enginedict import build_engine_dict

            build_engine_dict({"Deblur Mode": "去模糊模式", "Denoise": "降噪"},
                              eng_dir)
            r = self.b.captureMissingParams()
            by_en = {it["en"]: it for it in r["items"]}
            # 词典有 + 不在改名名单 → stuck, kind 仍是 param
            self.assertTrue(by_en["Deblur Mode"].get("stuck"))
            self.assertEqual(by_en["Deblur Mode"]["kind"], "param")
            # 改名名单里有 → 不再列
            self.assertNotIn("Denoise", by_en)
            # store 已有译名 → 2026-11-02 起也走 stuck 判定(不在改名名单)
            self.assertTrue(by_en["Blur Amount"].get("stuck"))
            # 正常缺词在前, stuck 排尾(本夹具无 junk; stuck 之间按采集顺序)
            ens = [it["en"] for it in r["items"]]
            self.assertEqual(ens[-2:], ["Deblur Mode", "Blur Amount"])
            self.assertLess(ens.index("Custom Width"),
                            ens.index("Deblur Mode"))
            # 正常缺词不带 stuck 标记
            self.assertFalse(by_en["Custom Width"].get("stuck"))
        finally:
            with open(names_path, "w", encoding="utf-8") as f:
                f.write(old_names)
            rp = os.path.join(outdir, "renamed_names.json")
            if os.path.exists(rp):
                os.remove(rp)
            shutil.rmtree(eng_dir, ignore_errors=True)

    def test_commit_writes_locked_and_recapture_marks_stuck(self):
        r = self.b.captureMissingParams()
        n = self.b.commitCapturedWords([
            {"en": "Output Size", "zh": "输出尺寸"},
            {"en": "Custom Width", "zh": ""},      # 空译名跳过
            {"en": "", "zh": "孤儿"},               # 空英文名跳过
        ])
        self.assertEqual(n, 1)
        from core.dictstore import DictStore

        st = DictStore.load(os.path.join(self._tmpdir, "store.json"))
        self.assertEqual(st.lookup("Output Size").zh, "输出尺寸")
        # 记入后再提取:Output Size 从正常缺词变为 stuck 排尾 —— 词典有词
        # 但本会话没在 AE 里改名成功,用户应看到「未生效」而不是凭空消失
        r2 = self.b.captureMissingParams()
        by = {it["en"]: it for it in r2["items"]}
        self.assertTrue(by["Output Size"].get("stuck"))
        self.assertIn("Custom Width", by)

    def test_capture_blocked_when_ae_not_running(self):
        """★ 2026-11-02:AE 没开时不得把上一次会话的采集快照当缺词。

        采集文件是守护每会话一份、启动时才清 —— AE 关掉后文件还在，旧逻辑
        会照读，用户实测「不打开 AE 点提取也能提取」。生产闸门用
        aelaunch.ae_running() 把关；沙盒/测试进程不受影响。
        """
        import unittest.mock as mock
        from core import aelaunch
        from app.backend import AppBackend

        with mock.patch.object(AppBackend, "_live_auto_allowed",
                               return_value=True), \
                mock.patch.object(aelaunch, "ae_running", return_value=False):
            r = self.b._capture_missing_core()
        self.assertEqual(r["seen"], 0)
        self.assertEqual(r["items"], [])
        self.assertIn("AE 没在运行", r["note"][1])

    def test_capture_without_file(self):
        from core import engine
        import unittest.mock as mock

        empty = os.path.join(self._tmpdir, "nohook", "guard_uni.py")
        os.makedirs(os.path.dirname(empty), exist_ok=True)
        with open(empty, "w", encoding="utf-8") as f:
            f.write("# fake\n")
        with mock.patch.object(engine, "find_guard", return_value=(empty, "")):
            r = self.b.captureMissingParams()
        self.assertEqual(r, {"seen": 0, "items": []})

    def test_capture_skips_renamed_without_dict(self):
        """★ 2026-11-02:引擎已改名成功的词不再列出(词典层查不到也一样)。

        补词行集是快照;引擎随后把词改好了,旧行还挂着「参数」。改名名单
        renamed_names.json 是引擎亲眼改成功的白名单 → 命中即跳过。
        """
        outdir = os.path.join(self._hookdir, "out")
        names_path = os.path.join(outdir, "all_param_names.json")
        rn_path = os.path.join(outdir, "renamed_names.json")
        with open(names_path, encoding="utf-8") as f:
            old_names = f.read()
        try:
            with open(names_path, "w", encoding="utf-8") as f:
                json.dump(["Output Size", "Fit"], f, ensure_ascii=False)
            with open(rn_path, "w", encoding="utf-8") as f:
                json.dump(["Fit"], f)
            r = self.b.captureMissingParams()
            ens = [it["en"] for it in r["items"]]
            self.assertNotIn("Fit", ens)
            self.assertIn("Output Size", ens)
        finally:
            with open(names_path, "w", encoding="utf-8") as f:
                f.write(old_names)
            if os.path.exists(rn_path):
                os.remove(rn_path)

    def test_capture_effect_name_prefix_goes_junk(self):
        """★ 2026-11-02:带插件品牌前缀的效果名 → junk 排尾,不再混进参数。

        守护 onLeave 读 +0x40 无法区分参数名与流里的其它字符串,BCC 的
        参数流会把效果名(BCC Z-Blur / BCC+Glow)带进采集。效果名带品牌
        前缀、参数名不带 —— 隔离到 junk,标「效果名」,仍可手动翻译。
        """
        outdir = os.path.join(self._hookdir, "out")
        names_path = os.path.join(outdir, "all_param_names.json")
        with open(names_path, encoding="utf-8") as f:
            old_names = f.read()
        try:
            with open(names_path, "w", encoding="utf-8") as f:
                json.dump(["Instability State", "BCC Z-Blur", "BCC+Glow"],
                          f, ensure_ascii=False)
            r = self.b.captureMissingParams()
            by = {it["en"]: it for it in r["items"]}
            self.assertFalse(by["Instability State"].get("junk"))
            self.assertTrue(by["BCC Z-Blur"].get("junk"))
            self.assertTrue(by["BCC+Glow"].get("junk"))
            # 效果名排在所有非 junk 行之后(其余行来自夹具的枚举/缺词)
            ens = [it["en"] for it in r["items"]]
            self.assertEqual(set(ens[-2:]), {"BCC Z-Blur", "BCC+Glow"})
            self.assertLess(ens.index("Instability State"), ens.index("BCC Z-Blur"))
        finally:
            with open(names_path, "w", encoding="utf-8") as f:
                f.write(old_names)

    def test_capture_same_form_placeholder_is_missing(self):
        """★ 2026-11-02 同形占位(zh==en)不算已译,必须重新列出。

        用户实测 BCC 单润的 PreBlur/PostBlur 一直是英文,补词却只有 5 行:
        引擎把 PreBlur 写回 PreBlur 也算改名成功,renamed_names.json 声称
        已译,旧逻辑命中改名名单直接跳过。zh==en 的条目其实是「没有翻译」,
        AE 里仍是英文 → 必须当成缺词列出来。纯缩写(RGB)保留原文,不算缺词。
        """
        outdir = os.path.join(self._hookdir, "out")
        names_path = os.path.join(outdir, "all_param_names.json")
        rn_path = os.path.join(outdir, "renamed_names.json")
        eng_dir = os.path.join(self._tmpdir, "engine_dict")
        os.makedirs(eng_dir, exist_ok=True)
        ov_path = os.path.join(eng_dir, "extra_overrides.json")
        with open(names_path, encoding="utf-8") as f:
            old_names = f.read()
        try:
            with open(ov_path, "w", encoding="utf-8") as f:
                json.dump({"PreBlur": "PreBlur", "PostBlur": "PostBlur",
                           "RGB": "RGB"}, f, ensure_ascii=False)
            with open(names_path, "w", encoding="utf-8") as f:
                json.dump(["PreBlur", "PostBlur", "RGB", "Output Size"],
                          f, ensure_ascii=False)
            with open(rn_path, "w", encoding="utf-8") as f:
                json.dump(["PreBlur", "PostBlur", "RGB"], f)
            r = self.b.captureMissingParams()
            by = {it["en"]: it for it in r["items"]}
            self.assertTrue(by["PreBlur"].get("same"))
            self.assertTrue(by["PostBlur"].get("same"))
            self.assertNotIn("RGB", by)          # 缩写保留原文,不算缺词
            self.assertIn("Output Size", by)
        finally:
            with open(names_path, "w", encoding="utf-8") as f:
                f.write(old_names)
            for p in (rn_path, ov_path):
                if os.path.exists(p):
                    os.remove(p)

if __name__ == "__main__":
    unittest.main()
