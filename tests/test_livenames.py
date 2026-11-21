"""core.livenames 的离线测试:写入/回滚/恢复/外部修改防护/会话身份。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import livenames  # noqa: E402
from core.livenames import NameSession, encode_name, read_live  # noqa: E402

from fake_ae import FakeAE  # noqa: E402


class LiveNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_live_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeAE(self.tmp)
        self.tmp = self.fake.tmp_path
        patcher = self.fake.patches()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = NameSession(self.tmp)

    # ---- 构造 translations: {matchName: zh} ----
    def _translations(self, count=None, offset=0, zh="汉"):
        result = {}
        for display, match, _category in self.fake.effects[offset:count]:
            result[match] = zh + display
        return result

    def test_apply_writes_and_journal(self):
        result = self.session.apply(self._translations())
        self.assertEqual(set(result["applied"]), {m for _d, m, _c in self.fake.effects})
        for index, (display, match, _c) in enumerate(self.fake.effects):
            self.assertEqual(self.fake.current_display_name(index), zh_expected(display))
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(journal["schemaVersion"], 1)
        self.assertEqual(journal["pid"], self.fake.pid)
        self.assertEqual(journal["creationTime"], self.fake.creation_time)
        self.assertEqual(journal["encoding"], self.fake.encoding)
        self.assertEqual(len(journal["entries"]), len(self.fake.effects))

    def test_apply_twice_reports_unchanged(self):
        self.session.apply(self._translations())
        before = self.fake.process(write=True).write_calls
        result = self.session.apply(self._translations())
        self.assertEqual(len(result["unchanged"]), len(self.fake.effects))
        self.assertEqual(self.fake.process(write=True).write_calls, before)

    def test_restore_returns_original(self):
        originals = [self.fake.current_display_name(i) for i in range(len(self.fake.effects))]
        self.session.apply(self._translations())
        result = self.session.restore()
        self.assertEqual(set(result["restored"].values()), set(originals))
        for index in range(len(self.fake.effects)):
            self.assertEqual(self.fake.current_display_name(index), originals[index])
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(journal["entries"], {})

    def test_restore_partial_keeps_rest(self):
        originals = [self.fake.current_display_name(i) for i in range(len(self.fake.effects))]
        self.session.apply(self._translations())
        first_match = self.fake.effects[0][1]
        result = self.session.restore([first_match])
        self.assertEqual(list(result["restored"]), [first_match])
        self.assertEqual(self.fake.current_display_name(0), originals[0])
        self.assertEqual(self.fake.current_display_name(1), zh_expected(self.fake.effects[1][0]))
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(set(journal["entries"]), {m for _d, m, _c in self.fake.effects[1:]})

    def test_restore_refuses_externally_modified(self):
        self.session.apply(self._translations())
        # 用户(或其他程序)改掉了第 0 个效果的名字字节
        raw = bytearray(self.fake.display_name_bytes(0))
        raw[0:2] = "雾".encode("mbcs")
        raw[2:] = b"\0" * (len(raw) - 2)
        self.fake.set_display_name_bytes(0, bytes(raw))
        with self.assertRaises(RuntimeError) as ctx:
            self.session.restore()
        self.assertIn("已被外部修改", str(ctx.exception))

    def test_apply_refuses_externally_modified_entry(self):
        self.session.apply(self._translations())
        raw = bytearray(self.fake.display_name_bytes(1))
        raw[0:2] = "雪".encode("mbcs")
        raw[2:] = b"\0" * (len(raw) - 2)
        self.fake.set_display_name_bytes(1, bytes(raw))
        with self.assertRaises(RuntimeError) as ctx:
            self.session.apply(self._translations(2))
        self.assertIn("已被其他程序修改", str(ctx.exception))

    def test_full_rollback_when_first_write_fails(self):
        originals = [self.fake.current_display_name(i) for i in range(len(self.fake.effects))]
        proc = self.fake.process(write=True)
        proc.fail_next_write = True  # 一次性故障:staged 写失败,回滚写成功
        with self.assertRaises(RuntimeError) as ctx:
            self.session.apply(self._translations())
        self.assertIn("已完全回滚", str(ctx.exception))
        for index in range(len(self.fake.effects)):
            self.assertEqual(self.fake.current_display_name(index), originals[index])
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(journal["entries"], {})

    def test_partial_rollback_keeps_failed_entry(self):
        # 持续性故障(真实场景:写权限被吊销):第二条起所有写入都失败,
        # 已写进去的第 0 条也回滚不了 —— 日志必须保留,不得静默。
        proc = self.fake.process(write=True)
        proc.fail_on_write = 2
        with self.assertRaises(RuntimeError) as ctx:
            self.session.apply(self._translations())
        self.assertIn("部分字段未能回滚", str(ctx.exception))
        self.assertEqual(self.fake.current_display_name(0), "汉" + self.fake.effects[0][0])  # 已写入且回滚失败
        self.assertEqual(self.fake.current_display_name(1), self.fake.effects[1][0])  # 没写进去
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(set(journal["entries"]), {self.fake.effects[0][1], self.fake.effects[1][1]})

    def test_journal_identity_reset_for_new_process(self):
        # 旧日志属于另一个进程:绝不把旧地址写进新进程 —— 记录直接作废
        stale = {"schemaVersion": 1, "pid": 999, "creationTime": 1, "encoding": self.fake.encoding,
                 "entries": {"S_Glow": {"originalHex": ("41" + "00" * 47), "appliedHex": ("42" + "00" * 47),
                                        "originalName": "S_Glow", "newName": "旧进程的写入"}}}
        (self.tmp / "live-session.json").write_text(json.dumps(stale), encoding="utf8")
        result = self.session.apply(self._translations())
        self.assertEqual(len(result["applied"]), len(self.fake.effects))
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(journal["pid"], self.fake.pid)

    def test_restore_old_process_ended(self):
        self.session.apply(self._translations())
        stale = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        stale["pid"] = 999
        (self.tmp / "live-session.json").write_text(json.dumps(stale), encoding="utf8")
        result = self.session.restore()
        self.assertTrue(result.get("oldProcessEnded"))
        journal = json.loads((self.tmp / "live-session.json").read_text(encoding="utf8"))
        self.assertEqual(journal["entries"], {})

    def test_apply_skips_unencodable_on_mbcs_build(self):
        self.fake.process  # noqa: B018  (确保进程实例已缓存)
        translations = self._translations()
        first = next(iter(translations))
        translations[first] = "辉光™"
        result = self.session.apply(translations)
        self.assertIn(first, result["unwritable"]["keys"])
        self.assertNotIn(first, result["applied"])

    def test_apply_shortens_toolong_instead_of_dropping_it(self):
        # ★ 2026-11-06 回归:「保存过的工程只有部分效果名汉化」的第二条根因。
        #   48 字节显示名字段装不下超长译文(词库里确有 16 条效果名 > 47 字节),
        #   原实现让 encode_name 抛 ValueError 冒到 apply 外,导致**整批**全不写;
        #   只跳过的版本虽然不再拖垮整批,那条效果名仍停在英文 —— 用户看到的
        #   依然是「部分汉化」。现在按标点收口后照写。
        translations = self._translations()
        first = next(iter(translations))
        long_text = "BCC 平面粒子系统，可发射、飘散、生成雪/尘/火花等二维粒子"
        translations[first] = long_text
        self.assertGreater(len(long_text.encode("utf8")), livenames.FIELD_SIZE - 1)
        result = self.session.apply(translations)          # 不得抛异常
        self.assertEqual(set(result["applied"]), set(translations))
        self.assertNotIn(first, (result["unwritable"] or {}).get("keys", []))
        # 主干优先:收到第一个中文标点前
        self.assertEqual(result["applied"][first], "BCC 平面粒子系统")
        self.assertEqual(result["shortened"][first], "BCC 平面粒子系统")
        for index, (display, match, _c) in enumerate(self.fake.effects):
            want = result["applied"][match]
            self.assertEqual(self.fake.current_display_name(index), want)

    def test_apply_hard_truncates_when_no_break_char(self):
        # 没有标点可断时退化成前缀截断,同样不许整条退回英文。
        translations = self._translations()
        first = next(iter(translations))
        translations[first] = "汉" * 40
        result = self.session.apply(translations)
        got = result["applied"][first]
        # 长度闸按当前构建的编码算(测试用 FakeAE,可能是 mbcs/GBK),不是 UTF-8
        self.assertLessEqual(len(got.encode(self.fake.encoding)), livenames.FIELD_SIZE - 1)
        self.assertTrue(got)
        self.assertEqual(got, "汉" * (len(got)))
        self.assertIn(first, result["shortened"])

    def test_apply_missing_key_skipped(self):
        translations = self._translations()
        translations["Not_Loaded_Effect"] = "不存在"
        result = self.session.apply(translations, skip_missing=True)
        self.assertNotIn("Not_Loaded_Effect", result["applied"])
        self.assertIn("Not_Loaded_Effect", result["skipped"])

    def test_read_live_snapshot(self):
        snapshot = read_live(self.tmp)
        self.assertEqual(snapshot["version"], self.fake.version)
        self.assertEqual(len(snapshot["effects"]), len(self.fake.effects))
        self.assertEqual({e["matchName"] for e in snapshot["effects"]}, {m for _d, m, _c in self.fake.effects})


def zh_expected(display):
    return "汉" + display


class EncodeNameTests(unittest.TestCase):
    def test_length_gate(self):
        self.assertEqual(len(encode_name("a" * 47, "utf8")), 48)
        with self.assertRaises(ValueError):
            encode_name("a" * 48, "utf8")
        # 15 个常见汉字 = 45 字节,可行;16 个超限
        self.assertEqual(len(encode_name("汉" * 15, "utf8")), 48)
        with self.assertRaises(ValueError):
            encode_name("汉" * 16, "utf8")

    def test_control_chars_rejected(self):
        with self.assertRaises(ValueError):
            encode_name("a\x01b", "utf8")
        with self.assertRaises(ValueError):
            encode_name("   ", "utf8")

    def test_mbcs_unencodable(self):
        with self.assertRaises(ValueError) as ctx:
            encode_name("辉光™", "mbcs")
        self.assertIn("系统编码", str(ctx.exception))

    def test_padding(self):
        value = encode_name("辉光", "utf8")
        self.assertTrue(value.endswith(b"\0\0"))
        self.assertEqual(value.rstrip(b"\0"), "辉光".encode("utf8"))
        self.assertEqual(len(value), 48)


if __name__ == "__main__":
    unittest.main()
