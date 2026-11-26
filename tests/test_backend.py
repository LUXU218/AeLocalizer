"""界面后端测试：不启动 Qt 事件循环，只测纯逻辑与信号。

用 QT_QPA_PLATFORM=offscreen 创建 QGuiApplication，但不进 exec()。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


def backend_mod_root() -> str:
    """当前生效的数据根（测试里已被换成沙盒目录）。"""
    import app.backend as backend_mod

    return backend_mod.DATA_ROOT


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestBackend(unittest.TestCase):
    """测试 AppBackend。

    ★★ 2026-10-23 严重事故修复：
      这里原来直接 AppBackend()，而它用的是【真实】的
      %LOCALAPPDATA%\\AeLocalizer 目录 —— 于是后面那条
          self.b.setAiKey("sk-1234567890abcd")
      把用户真实的 API Key（DPAPI 密文）覆盖成了测试用的假 key！
      用户感受就是「退出重进密钥就被自动替换」，
      因为每跑一次测试就写一次假 key。

      现在：把 backend 模块级的 DATA_ROOT 指向临时目录，
      store.json / settings.json / ai_key.dat / selection-*.json
      全落在沙盒里，绝不碰用户真实数据。
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile

        import app.backend as backend_mod

        # ★★ 2026-10-23 回归护栏：
        #   跑测试前先给【真实】用户目录里的 ai_key.dat 拍个快照，
        #   tearDownClass 里逐字节比对 —— 只要有任何一个测试
        #   又把真 Key 覆盖成测试假 Key，这里立刻炸出来。
        cls._real_key_path = os.path.join(backend_mod.DATA_ROOT, "ai_key.dat")
        cls._real_key_blob = None
        try:
            with open(cls._real_key_path, "rb") as f:
                cls._real_key_blob = f.read()
        except OSError:
            pass

        cls._tmpdir = tempfile.mkdtemp(prefix="aeloc_test_backend_")
        cls._old_root = getattr(backend_mod, "DATA_ROOT", "")
        backend_mod.DATA_ROOT = cls._tmpdir

        from app.backend import AppBackend

        cls.b = AppBackend()

    @classmethod
    def tearDownClass(cls):
        import shutil

        import app.backend as backend_mod

        # ★ 真实密钥文件必须原封不动
        try:
            with open(cls._real_key_path, "rb") as f:
                now = f.read()
        except OSError:
            now = None
        if cls._real_key_blob is None:
            assert now is None, "测试往真实用户目录写了 ai_key.dat！"
        else:
            assert now == cls._real_key_blob, "测试改动了真实用户目录的 ai_key.dat！"

        if cls._old_root:
            backend_mod.DATA_ROOT = cls._old_root
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_versions_detected(self):
        self.assertIsInstance(self.b.versions, list)

    def test_world_label(self):
        # ★ 2026-10-23：中文链路恒 GBK（见 core/encoder.py 实机采样记录），
        #   不再按版本返回 UTF-8。
        v = self.b.currentVersion
        if v:
            self.assertEqual(self.b.worldLabel, "GBK")

    def test_ai_providers_listed(self):
        provs = self.b.aiProviders
        self.assertGreaterEqual(len(provs), 9)
        keys = [p["key"] for p in provs]
        self.assertIn("deepseek", keys)
        self.assertIn("ollama", keys)

    def test_default_provider_is_deepseek(self):
        self.assertEqual(self.b.aiProvider, "deepseek")
        self.assertIn("deepseek", self.b.aiBaseUrl)
        self.assertEqual(self.b.aiModel, "deepseek-chat")

    def test_import_pack_reports_failure_without_saving_or_reloading(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from PySide6.QtTest import QSignalSpy

        import app.backend as backend_mod
        from core.dictpack import build_pack
        from core.dictstore import DictStore
        from tests.test_dictpack import rewrite_pack_json

        store = DictStore()
        store.upsert_user("Glow", "我的光晕")
        previous = store.import_pack(build_pack(
            pack_id="com.test", name="旧测试包", version="1", author="test",
            entries={"Old": {"zh": "旧词条"}},
        ))
        original = build_pack(
            pack_id="com.test", name="测试包", version="2", author="test",
            entries={"Glow": {"zh": "光晕"}, "Blur": {"zh": "模糊"}},
        )
        invalid = rewrite_pack_json(
            original, "dict.json", lambda obj: obj["entries"]["Blur"].update(zh="不同的译名"),
        )
        with TemporaryDirectory(dir=self._tmpdir) as tmp:
            existing = Path(tmp, "store.json")
            store.save(str(existing))
            snapshot = existing.read_bytes()
            source = Path(tmp, "test.aeloc")
            source.write_bytes(invalid)
            toast = QSignalSpy(self.b.toast)
            with (
                patch.object(backend_mod, "DATA_ROOT", tmp),
                patch.object(self.b, "_store", store),
                patch.object(self.b, "_hot_reload_notify") as reload_engine,
                patch.object(self.b, "_bump_dict") as refresh_dict,
            ):
                self.b.importPack(str(source))
                self.assertEqual(toast.count(), 1)
                self.assertEqual(toast.at(0), ["error", "导入失败：dict.json 校验失败（内容被修改）"])
                self.assertEqual(existing.read_bytes(), snapshot)
                self.assertEqual(store.packs, [previous])
                reload_engine.assert_not_called()
                refresh_dict.assert_not_called()

                source.write_bytes(original)
                self.b.importPack(str(source))
                self.assertEqual(toast.count(), 2)
                level, message = toast.at(1)
                self.assertEqual(level, "info")
                self.assertIn("已导入 测试包", message)
                self.assertIn("未签名", message)
                self.assertIn("译名不同 1 条", message)
                persisted = DictStore.load(str(existing))
                self.assertEqual(persisted.lookup("Glow").zh, "我的光晕")
                self.assertEqual(persisted.lookup("Blur").zh, "模糊")
                self.assertIsNone(persisted.lookup("Old"))
                reload_engine.assert_called_once()
                refresh_dict.assert_called_once()

    # ---------------- 引擎相关 ----------------

    def test_engine_properties_exist(self):
        self.assertIsInstance(self.b.engineRunning, bool)
        self.assertIsInstance(self.b.guardAvailable, bool)
        self.assertIsInstance(self.b.fixerInstalled, bool)
        self.assertIsInstance(self.b.fixerNeeded, bool)
        self.assertIsInstance(self.b.engineDictEntries, int)
        self.assertIsInstance(self.b.keepUnowned, bool)

    def test_engine_report_shape(self):
        rep = self.b.engineReport()
        self.assertGreaterEqual(len(rep), 5)
        for item in rep:
            self.assertIn("ok", item)
            self.assertIn("warn", item)
            self.assertIn("label", item)
            self.assertIn("detail", item)

    def test_keep_unowned_toggle(self):
        old = self.b.keepUnowned
        try:
            self.b.setKeepUnowned(not old)
            self.assertEqual(self.b.keepUnowned, not old)
        finally:
            self.b.setKeepUnowned(old)

    def test_attrib_info_before_build(self):
        self.b._attribLoaded = False
        self.b._attrib = {}
        self.assertIn("尚未建立", self.b.attribInfo)

    def test_attrib_info_after_build(self):
        self.b._attribLoaded = True
        self.b._attrib = {"A": ["g1"], "B": ["g1", "g2"]}
        info = self.b.attribInfo
        self.assertIn("2 个名字", info)
        self.assertIn("共享 1", info)

    def test_rescue_stop_all(self):
        """★ 止血按钮：停引擎 + 强杀守护 + 结束 AE，一步到位。

        ⚠️ 必须 mock _kill_ae_msg —— 不 mock 的话跑测试会 taskkill
        真机上正在运行的 AE（2026-10-25 差点翻车）。
        """
        from unittest import mock

        from core import engine as eng

        with mock.patch.object(eng, "stop_engine",
                               return_value=(True, "引擎已停止")), \
                mock.patch.object(eng, "force_kill_guards", return_value=2), \
                mock.patch.object(type(self.b), "_kill_ae_msg",
                                  return_value="AE 本来没在运行"):
            msg = self.b.rescueStopAll()
        self.assertIn("引擎已停止", msg)
        self.assertIn("强杀守护 2 个", msg)
        self.assertIn("AE", msg)

    def test_switch_provider_updates_defaults(self):
        self.b.setAiProvider("qwen")
        self.assertIn("dashscope", self.b.aiBaseUrl)
        self.assertEqual(self.b.aiModel, "qwen-plus")
        self.b.setAiProvider("deepseek")   # 复原

    def test_user_can_override_base_url(self):
        self.b.setAiBaseUrl("http://127.0.0.1:8000/v1")
        self.assertEqual(self.b.aiBaseUrl, "http://127.0.0.1:8000/v1")
        self.b.setAiBaseUrl("")

    def test_key_is_masked(self):
        self.b.setAiKey("sk-1234567890abcd")
        self.assertTrue(self.b.aiHasKey)
        self.assertIn("abcd", self.b.aiKeyMasked)
        self.assertNotIn("sk-123", self.b.aiKeyMasked)
        self.b.setAiKey("")

    def test_empty_key_does_not_wipe_saved_key(self):
        """★ 用户报障回归：空输入绝不能把磁盘上已保存的 Key 冲掉。

        现场：QML 的 TextInput.onEditingFinished 在任何失焦时都调
        setAiKey(text)，而输入框平时是空的 —— 于是"点一下输入框再点别处"
        就会把 Key 清掉，用户看到的就是「Key 突然没用了」。
        """
        from core import secrets

        kp = secrets.default_path(os.path.join(backend_mod_root(), ""))
        secrets.save_key("sk-keepme-1234567890", kp)
        self.b.setAiKey("")                      # 空 -> 只清内存
        self.assertFalse(self.b.aiHasKey)
        self.assertEqual(secrets.load_key(kp), "sk-keepme-1234567890")
        self.b.setAiKey("sk-keepme-1234567890")  # 复原内存

    def test_key_survives_restart(self):
        """★ 用户报障回归：退出重进后 Key 必须还在、还能用。"""
        from app.backend import AppBackend
        from core import secrets

        b1 = AppBackend()
        b1.setAiKey("sk-restart-test-9876zyxw")
        b2 = AppBackend()                        # 模拟"退出重进"
        self.assertTrue(b2.aiHasKey)
        self.assertEqual(b2.aiKeyMasked[-4:], "zyxw")
        self.assertTrue(b2.aiKeySaved)
        self.assertEqual(
            secrets.load_key(secrets.default_path(os.path.join(backend_mod_root(), ""))),
            "sk-restart-test-9876zyxw")

    def test_changed_key_keeps_previous_for_restore(self):
        """换 Key 时旧 Key 要留备份，且能一键恢复（防止被覆盖后再也找不回）。"""
        from app.backend import AppBackend

        b = AppBackend()
        b.setAiKey("sk-first-key-1111aaaa")
        b.setAiKey("sk-second-key-2222bbbb")
        self.assertTrue(b.aiKeyCanRestore)
        self.assertEqual(b.restoreAiKey(), "已恢复")
        self.assertEqual(b.aiKeyMasked[-4:], "aaaa")

    def test_ai_provider_choice_survives_restart(self):
        """★ 服务商/端点/模型也必须记住 —— 否则重进后 Key 被打到错误的服务上。"""
        from app.backend import AppBackend

        b1 = AppBackend()
        b1.setAiProvider("custom")
        b1.setAiBaseUrl("http://127.0.0.1:9999/v1")
        b1.setAiModel("my-model")

        b2 = AppBackend()                        # 模拟"退出重进"
        self.assertEqual(b2.aiProvider, "custom")
        self.assertEqual(b2.aiBaseUrl, "http://127.0.0.1:9999/v1")
        self.assertEqual(b2.aiModel, "my-model")

        # 复原，避免影响同一沙盒里的其它用例
        b2.setAiProvider("deepseek")

    def test_ai_key_not_required_for_ollama(self):
        self.b.setAiProvider("ollama")
        self.b.setAiKey("")
        cfg = self.b._ai_config()
        self.assertEqual(cfg.validate(), [])
        self.b.setAiProvider("deepseek")

    # ---------------- 效果名：按插件名称批量选择（2026-10-23） ----------------

    def _seed_name_rows(self, rows):
        from core.nameeditor import NameRow

        self.b._nameRows = [NameRow(**r) for r in rows]
        self.b._nameModel.setRows(self.b._nameRows)

    def test_name_filter_by_vendor_and_select_all(self):
        self._seed_name_rows([
            {"en": "BatchA1", "zh": "", "slot": 8, "vendor": "Boris FX"},
            {"en": "BatchA2", "zh": "", "slot": 8, "vendor": "Boris FX"},
            {"en": "BatchB1", "zh": "", "slot": 8, "vendor": "Sapphire"},
        ])
        n = self.b.filterNames("", False, "Boris FX")
        self.assertEqual(n, 2)
        self.assertEqual(self.b.selectAllNames(True), 2)
        self.assertEqual(self.b.nameSelectedCount, 2)
        # 勾选只包含筛选出来的两组
        self.assertEqual(sorted(self.b.nameModel.selectedEn()),
                         ["BatchA1", "BatchA2"])
        # 关键词 + 厂商叠加
        self.assertEqual(self.b.filterNames("BatchB", False, "Sapphire"), 1)
        self.b.clearNameSelection()
        self.assertEqual(self.b.nameSelectedCount, 0)
        self.b.filterNames("", False, "")

    def test_batch_clear_selected_names(self):
        self._seed_name_rows([
            {"en": "BatchClearX", "zh": "批量测试", "slot": 16, "vendor": "Boris FX"},
        ])
        self.b._store.upsert_user("BatchClearX", "批量测试", locked=True)
        self.b._nameModel.setSelectedByEn("BatchClearX", True)
        msg = self.b.batchClearNames()
        self.assertIn("1", msg)
        self.assertIsNone(self.b._store.lookup("BatchClearX"))
        self.assertEqual(self.b.nameSelectedCount, 0)

    def test_name_vendors_listed(self):
        self._seed_name_rows([
            {"en": "V1", "zh": "", "slot": 8, "vendor": "Boris FX"},
            {"en": "V2", "zh": "", "slot": 8, "vendor": "Sapphire"},
        ])
        vendors = {v["value"]: v["count"] for v in self.b.nameVendors()}
        self.assertEqual(vendors.get("Boris FX"), 1)
        self.assertEqual(vendors.get("Sapphire"), 1)

    def test_draft_accept_writes_to_store(self):
        # 用唯一键，避免被上次运行落盘的词条干扰（acceptDraft 会存盘）
        import time as _t

        tag = "T%d" % int(_t.time() * 1000 % 1_000_000)
        k1, k2 = "Effect" + tag + "A", "Effect" + tag + "B"
        self.assertIsNone(self.b._store.lookup(k1))
        self.b._aiDraft = [
            {"en": k1, "zh": "测试甲", "status": "ok"},
            {"en": k2, "zh": "测试乙", "status": "ok"},
        ]
        self.b.acceptDraft()
        self.assertIsNotNone(self.b._store.lookup(k1))
        self.assertEqual(self.b._store.lookup(k1).zh, "测试甲")
        self.assertEqual(self.b.aiDraftCount, 0)
        self.assertIn("已接受 2 条", self.b.aiDraftSummary)
        # 清理（内存 + 落盘）
        self.b._store.remove_user(k1)
        self.b._store.remove_user(k2)
        # ★ 2026-10-23：这里原来拼的是【真实】%LOCALAPPDATA% 路径，
        #   测试会去改写用户真实 store.json；改成沙盒根。
        self.b._store.save(os.path.join(backend_mod_root(), "store.json"))

    def test_draft_discard(self):
        self.b._aiDraft = [{"en": "X", "zh": "叉", "status": "ok"}]
        self.b.discardDraft()
        self.assertEqual(self.b.aiDraftCount, 0)

    def test_describe_entry(self):
        self.b._store.upsert_user("GlowThreshold", "发光阈值")
        s = self.b.describeEntry("GlowThreshold")
        # 2026-11-15：describe 改成「运行时能不能装下」的结论（不再列编码细节）。
        self.assertIn("可写入", s)
        self.b._store.remove_user("GlowThreshold")

    def test_describe_miss(self):
        self.assertEqual(self.b.describeEntry("不存在的名字"), "未命中")

    def test_set_channel_auto_updates_channels(self):
        """引擎卡三开关的通道状态切换(引擎未运行时不触发重启路径)。

        ★ 2026-11-02:新分组默认效果名关 —— param 开关不应连带影响 effect。
        """
        from core.inventory import build_inventory

        b2 = type(self.b)()
        b2._inv = build_inventory(b2._root, b2._version)
        b2.setChannelAuto("param", False)
        self.assertFalse(b2.channelState()[1])
        self.assertFalse(b2.channelState()[0])   # 新默认:效果名关,且不受 param 切换影响
        b2.setChannelAuto("param", True)
        self.assertTrue(b2.channelState()[1])
        self.assertFalse(b2.channelState()[0])
        self.assertTrue(b2.channelState()[2])   # 合并通道：param/enum 配对
        b2.setChannelAuto("enum", False)        # 旧入口兼容
        self.assertFalse(b2.channelState()[1])
        self.assertFalse(b2.channelState()[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestEnumJunk(unittest.TestCase):
    """枚举流里的数字/序号/单位不是可译词，不能进补词页。"""

    def test_numeric_and_unit_strings_are_junk(self):
        import app.backend as B
        for s in ("0 - 100 % RGB", "32nd", "64th", "1-1-1", "10", "1000",
                  "360 degrees", "-90"):
            self.assertTrue(B._is_junk_enum(s), s)

    def test_real_enum_values_are_kept(self):
        import app.backend as B
        for s in ("Hue", "Use", "Antialiasing", "RGB", "Display Color Space",
                  "Black Color", "Multiply"):
            self.assertFalse(B._is_junk_enum(s), s)


class TestRun(unittest.TestCase):
    """_run 通道冒烟(2026-10-29 补)。

    ★ 起因:DLL 死代码清除时误删了 _run,10 个调用点(扫描/应用/引擎启停/
      回滚…)全部静默失效 —— 而 430 个用例没有一个真正跑过这条通道,
      全绿照样漏。此用例就是那条通道的兜底:后台执行 -> done 回调 -> busy 复位。
    """

    def test_run_delivers_done_and_resets_busy(self):
        import time

        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
        import app.backend as backend_mod

        b = backend_mod.AppBackend()
        box = []
        b._run(lambda: 42, lambda r: box.append(r), _busy_text="测试中")
        self.assertTrue(b.busy)
        deadline = time.time() + 5
        while not box and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        self.assertEqual(box, [42])
        self.assertFalse(b.busy)

    def test_run_delivers_failed_as_toast(self):
        import time

        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
        import app.backend as backend_mod

        b = backend_mod.AppBackend()
        got = []
        b.toast.connect(lambda lvl, txt: got.append((lvl, txt)))

        def boom():
            raise RuntimeError("故意失败")

        b._run(boom, lambda r: None, _busy_text="测试中")
        deadline = time.time() + 5
        while b.busy and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        self.assertTrue(any("故意失败" in t for _, t in got))
        self.assertFalse(b.busy)

# ------------------------------------------------------------------ 启动 AE
@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestLaunchAeAsync(unittest.TestCase):
    """「启动 AE」必须后台跑，不能卡住界面。

    ★ 2026-10-30 用户报「每次进入 AE 都要很久」：AE 冷启动实测就要 10 秒以上
      （Adobe 自身开销，见 docs/兼容性-AE2021-2023.md §8），而 launchAE() 原来
      是同步 Slot —— 这 10 秒里 QML 主线程完全卡死，用户看到的是界面假死。
      现在改成 Worker 后台线程：立即返回 + 就绪后 toast。
    """

    def test_launch_ae_returns_immediately(self):
        import time

        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
        import core.aelaunch as ael
        import app.backend as backend_mod

        b = backend_mod.AppBackend()
        b._auto_adapt_async = lambda v: None       # 绝不在测试里碰真实 AE
        calls = []
        old = ael.launch_ae
        ael.launch_ae = lambda exe="", timeout=0.0, verbose=False: (
            calls.append(timeout) or (True, "AE 已就绪"))
        try:
            t0 = time.time()
            msg = b.launchAE()
            dt = time.time() - t0
            self.assertLess(dt, 1.0, "launchAE 同步阻塞了 %.2fs" % dt)
            self.assertIsInstance(msg, str)
            self.assertTrue(b._aeLaunching)
            # 重复点不能起第二个 AE 进程
            self.assertIn("正在启动", b.launchAE())
            w = b._workers[-1]
            w.wait(5000)
            deadline = time.time() + 5
            while b._aeLaunching and time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)
        finally:
            ael.launch_ae = old
        self.assertFalse(b._aeLaunching)
        self.assertEqual(len(calls), 1)


# ------------------------------------------------------------------ 效果名常驻补写
@unittest.skipUnless(HAS_QT, "PySide6 不可用")
class TestLiveWatch(unittest.TestCase):
    """效果名通道常驻补写（2026-11-01）。

    ★ 起因（用户报「明明勾了效果名,但 AE 里效果名没汉化」）：开关状态是
      持久的（存选区文件），注册表写入却是一次性的 —— 只在点击开关那一刻
      执行，AE 一重启就没了；应用重启后 _inv 为 None 时点击还会被
      setChannelAuto 静默吞掉。修复 = 启动静默预扫 + 8 秒一拍的常驻补写。
      此处锁定接线：沙盒闸 / 会话记账 / 开关完整链 / 清单未就绪不炸。
    """

    def setUp(self):
        from PySide6.QtCore import QCoreApplication

        import app.backend as backend_mod

        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.bm = backend_mod
        self.b = backend_mod.AppBackend()

    def _real_inv(self):
        from core.inventory import build_inventory

        self.b._inv = build_inventory(self.b._root, self.b._version)
        # 沙盒里没有选区文件（默认全不勾），补写闸门要求「有勾选的组」
        for g in self.b._inv.groups:
            if not g.builtin_adobe:
                g.selected = True
                break
        return self.b._inv

    def test_sandbox_gate_disabled(self):
        """测试进程（DATA_ROOT 被换掉）绝不启动常驻定时器。"""
        self.assertFalse(self.b._liveAutoEnabled)
        self.assertFalse(hasattr(self.b, "_liveWatch"))

    def test_resize_defers_ui_thread_watch_polling_until_release(self):
        from unittest.mock import patch

        with patch.object(self.b, "_live_auto_allowed", return_value=False) as allowed:
            self.b.setWindowResizing(True)
            self.b._liveWatchTick()
            allowed.assert_not_called()
            self.b.setWindowResizing(False)
            self.b._liveWatchTick()
            allowed.assert_called_once()

    def test_tick_writes_every_pass(self):
        """每一拍都动手（★ 2026-11-06 起不再「成功即永久记账」）。

        ★ 为什么必须每拍都写：注册表（FLT_FCSpec+0x10）不再写了，新建的
          效果实例出生就是英文标题 —— 一次性记账会让之后新加的每个效果
          永远停在英文名上。稳态开销由 liveparams 的增量差分兜住。
        """
        from unittest.mock import patch

        self._real_inv()
        self.b.setAllChannels(True, True, True)     # 效果名通道开
        fake = [{"pid": 111, "creation_time": 222, "image": "x",
                 "fileVersion": "25.5"}]

        def fake_apply(*a, **k):
            # 模拟 _live_apply 的 done/fail:复位在飞标志
            self.b._liveAutoInflight = False

        with patch("core.proc.list_ae_processes", return_value=fake), \
             patch("core.proc.main_window", return_value=1), \
             patch.object(self.b, "_live_apply", side_effect=fake_apply) as apply_mock, \
             patch.object(self.b, "_live_auto_allowed", return_value=True):
            self.b._liveSeen[(111, 222)] = 0.0      # 越过稳定窗
            for want in (1, 2, 3, 4):
                self.b._liveWatchTick()
                self.assertEqual(apply_mock.call_count, want)
            kwargs = apply_mock.call_args.kwargs
            self.assertTrue(kwargs.get("auto_rename"))
            self.assertTrue(kwargs.get("quiet"))
            self.assertEqual(kwargs.get("memo_key"), (111, 222))
            # AE 重启(创建时间变了) → 换一把账本键
            fake[0]["creation_time"] = 333
            self.b._liveSeen[(111, 333)] = 0.0
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_args.kwargs.get("memo_key"), (111, 333))

    def test_apply_done_never_chains_script_and_fail_reports_once(self):
        """★ 2026-11-06 回归闸：做完只刷新界面，不再串跑任何换名脚本。

        ★ 旧版 done() 会串着调 _liveNamesAsync("on") —— 那条路径要
          aejsx.project_names(AfterFX.exe -r，超时 25 秒)，就是「新加效果
          要等两秒才中文」的延迟根源，也是把 FLT 注册表写脏的第二个入口。
          这里锁死：即使成功也不许再串。
        ★ 失败：不记账，同一会话只出声一次（下一拍重试）。
        """
        from unittest.mock import patch

        calls = []
        # ★ done() 里串着跑的 refreshPalette 内部也会再走一次 _run,
        #   所以必须按调用次序捕获,只看 _live_apply 自己那一次
        def fake_run(fn, on_done, *a, **k):
            calls.append({"done": on_done, "fail": k.get("_on_fail"),
                          "toast_fail": k.get("_toast_fail")})

        self.b._run = fake_run
        got = []
        self.b.toast.connect(lambda lvl, txt: got.append(txt))
        with patch.object(self.b, "_liveNamesAsync") as ln, \
             patch.object(self.b, "refreshPalette"):
            self.b._live_apply(auto_rename=True, quiet=True, memo_key=(9, 9))
            self.assertFalse(calls[0]["toast_fail"])      # 自动路径不在 _run 里弹错
            calls[0]["done"]({"applied": {"X": "甲"}, "written": 1,
                              "unchanged": [], "skipped": []})
            ln.assert_not_called()                        # 绝不串跑脚本换名
            self.assertTrue(any("1 条" in t for t in got))
            # 失败:第一次响,第二次静默(下一拍会重试)
            calls[0]["fail"]("权限不足")
            self.assertEqual(sum("权限不足" in t for t in got), 1)
            calls[0]["fail"]("权限不足")
            self.assertEqual(sum("权限不足" in t for t in got), 1)

    def test_tick_skips_when_channel_off_or_busy(self):
        """通道没开 / 没有清单 / 忙碌 时不动手；通道重开后恢复。"""
        from unittest.mock import patch

        self._real_inv()
        self.b.setAllChannels(False, True, True)    # 效果名通道关
        fake = [{"pid": 1, "creation_time": 2, "image": "x", "fileVersion": ""}]
        with patch("core.proc.list_ae_processes", return_value=fake), \
             patch("core.proc.main_window", return_value=1), \
             patch.object(self.b, "_live_apply") as apply_mock, \
             patch.object(self.b, "_live_auto_allowed", return_value=True):
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 0)
            self.b.setAllChannels(True, True, True)
            self.b._liveSeen[(1, 2)] = 0.0
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 1)

    def test_tick_needs_main_window(self):
        """AE 主窗口没起好（启动中/有模态框）→ 等下一拍，不记账不写入。"""
        from unittest.mock import patch

        self._real_inv()
        self.b.setAllChannels(True, True, True)
        fake = [{"pid": 5, "creation_time": 6, "image": "x", "fileVersion": ""}]
        with patch("core.proc.list_ae_processes", return_value=fake), \
             patch("core.proc.main_window", side_effect=RuntimeError("未找到唯一的 AE 主窗口")), \
             patch.object(self.b, "_live_apply") as apply_mock, \
             patch.object(self.b, "_live_auto_allowed", return_value=True):
            self.b._liveSeen[(5, 6)] = 0.0
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 0)
            # 窗口就绪后同一会话仍能补写
            with patch("core.proc.main_window", return_value=1):
                self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 1)

    def test_tick_zero_delay_and_idle_guards(self):
        """零延迟：主窗口就绪即写（不等稳定窗）；在飞/忙碌时绝不重叠。

        ★ 2026-11-06 起定时器是 0.4 秒一拍、每拍都写（不再有「复核两次」
          的轮次记账）—— 复核轮次的唯一作用是补写注册表里晚出现的条目，
          注册表不写之后它没有意义了。
        """
        from unittest.mock import patch

        self._real_inv()
        self.b.setAllChannels(True, True, True)
        fake = [{"pid": 77, "creation_time": 88, "image": "x", "fileVersion": ""}]

        def fake_apply(*a, **k):
            self.b._liveAutoInflight = False     # 模拟 done/fail 复位在飞标志

        with patch("core.proc.list_ae_processes", return_value=fake), \
             patch("core.proc.main_window", return_value=1), \
             patch.object(self.b, "_live_apply", side_effect=fake_apply) as apply_mock, \
             patch.object(self.b, "_live_auto_allowed", return_value=True):
            self.b._liveWatchTick()                      # 首见即写
            self.assertEqual(apply_mock.call_count, 1)
            for want in (2, 3, 4):                       # 每一拍都写
                self.b._liveWatchTick()
                self.assertEqual(apply_mock.call_count, want)
            # 上一拍还在飞 → 不重叠
            self.b._liveAutoInflight = True
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 4)
            self.b._liveAutoInflight = False
            # 应用忙碌 → 不动手
            self.b._busy = True
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 4)
            self.b._busy = False
            self.b._liveWatchTick()
            self.assertEqual(apply_mock.call_count, 5)

    def test_set_channel_auto_effect_on_full_chain(self):
        """开「效果名」= 注册表写入 + 既有实例就地换名（auto_rename）。"""
        from unittest.mock import patch

        inv = self._real_inv()
        from core.inventory import set_all_channels

        set_all_channels(inv, False, True, True)    # 先关
        with patch.object(self.b, "_live_apply") as apply_mock, \
             patch("core.proc.list_ae_processes", return_value=[]):
            self.b.setChannelAuto("effect", True)
            self.assertEqual(apply_mock.call_count, 1)
            self.assertTrue(apply_mock.call_args.kwargs.get("auto_rename"))
        self.assertTrue(self.b.channelState()[0])

    def test_language_status_background_progress_is_not_error(self):
        """后台 parade 重扫只显示「已应用/检查新增」，不能报异常。"""
        from unittest.mock import patch

        cases = [
            ({"language_ready": True, "mode": "on",
              "background_discovery_pending": True, "error": "",
              "popup_unsupported": 0}, "正在检查新增效果"),
            ({"language_ready": False, "mode": "on", "pending": True,
              "background_discovery_pending": True,
              "error": "discovery-pending:{rows:0}"}, "参数/下拉同步中"),
            ({"language_ready": False, "mode": "on",
              "background_discovery_pending": False,
              "error": "popup-partial:{'write': 1}"}, "语言同步异常"),
        ]
        for status, expected in cases:
            with patch("app.backend.engine.control_status", return_value=status):
                text = self.b.languageStatusText
            self.assertIn(expected, text)
            if expected != "语言同步异常":
                self.assertNotIn("异常", text)

    def test_language_status_evidence_based(self):
        """language_ready 只代表 hook 已挂载；文案必须看 draw_stats 的实际替换证据。

        背景：2026 text hook 未命中/词典未覆盖时，旧文案仍报“已显示中文”，
        被独立 reviewer 判为 overclaim（真实非阻断缺陷）。
        """
        from unittest.mock import patch

        cases = [
            # (status, expected_in, expected_not_in)
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 384,
                             "text_replaced": 228, "text_err": 0}},
             "参数/下拉已显示中文", "等待首次绘制确认"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 0,
                             "text_replaced": 0, "text_err": 0}},
             "等待首次绘制确认", "已显示中文"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 384,
                             "text_replaced": 0, "text_err": 0}},
             "文本标签未命中", "已显示中文"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 10,
                             "text_replaced": 10, "text_err": 3}},
             "替换异常 3", "已显示中文"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": False, "replaced": 0}},
             "等待首次绘制确认", "已显示中文"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": False, "replaced": 42}},
             "参数/下拉已显示中文", "等待首次绘制确认"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 5,
                             "text_replaced": 5, "text_err": 0,
                             "text_skip_invalid": 2}},
             "无效跳过 2", "替换异常"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"call_intact": False, "replaced": 0}},
             "调用点字节校验失败", "已显示中文"),
            ({"language_ready": False, "mode": "on",
              "error": "dvaui text build not verified: sha256 mismatch"},
             "dvaui 构建未验证", "语言同步异常"),
            ({"language_ready": True, "mode": "on", "error": "",
              "popup_error": "FLT build not verified: sha256 mismatch",
              "draw_stats": {"text_enabled": False, "replaced": 7}},
             "参数名已显示中文；下拉通道不可用", "参数/下拉已显示中文"),
            ({"language_ready": True, "mode": "on", "error": "",
              "popup_error": "RuntimeError: refresh scan failed after writes",
              "draw_stats": {"text_enabled": False, "replaced": 7}},
             "下拉通道不可用", "已安全拒写"),
            ({"language_ready": True, "mode": "on", "error": "",
              "draw_stats": {"text_enabled": True, "text_hits": 5,
                             "text_replaced": 0, "text_err": 0,
                             "text_skip_capacity": 3}},
             "容量限制跳过 3", "词典可能未覆盖"),
            ({"language_ready": True, "mode": "off", "error": "",
              "popup_error": "RuntimeError: refresh scan failed",
              "draw_stats": {"text_enabled": False, "replaced": 7}},
             "下拉通道可能未还原", "参数/下拉已还原原文"),
            ({"language_ready": True, "mode": "off", "error": "",
              "popup_restore_unknown": True,
              "draw_stats": {"text_enabled": False, "replaced": 7}},
             "下拉通道状态未知", "已安全拒写"),
            ({"language_ready": True, "mode": "on", "error": "",
              "background_discovery_pending": True,
              "draw_stats": {"text_enabled": False, "replaced": 3}},
             "正在检查新增效果", "等待首次绘制确认"),
            ({"language_ready": True, "mode": "on", "error": "",
              "popup_unsupported": 2,
              "draw_stats": {"text_enabled": False, "replaced": 3}},
             "2 组下拉选项容量未确认", "受容量限制"),
            ({"language_ready": True, "mode": "on", "error": ""},
             "等待首次绘制确认", "已显示中文"),
        ]
        for status, expected_in, expected_not_in in cases:
            with patch("app.backend.engine.control_status", return_value=status):
                text = self.b.languageStatusText
            self.assertIn(expected_in, text, msg="%r -> %r" % (status, text))
            if expected_not_in:
                self.assertNotIn(expected_not_in, text,
                                 msg="%r -> %r" % (status, text))

    def test_set_channel_auto_without_inventory_no_crash(self):
        """清单未就绪时点开关：不炸、有提示（不再静默无反应）。"""
        got = []
        self.b.toast.connect(lambda lvl, txt: got.append((lvl, txt)))
        self.b._inv = None
        self.b.setChannelAuto("effect", True)       # 不应抛异常
        self.assertTrue(any("清单" in t for _, t in got))
