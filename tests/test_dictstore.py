"""分层词典仓库测试：优先级 / 冲突 / 导入预览 / 持久化。"""
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dictpack import PackError, build_pack  # noqa: E402
from core.dictstore import DictStore  # noqa: E402
from tests.test_dictpack import rewrite_pack_json  # noqa: E402


def _pack(entries, pack_id="com.t.pack", name="测试包", version="1.0.0", **options):
    return build_pack(
        pack_id=pack_id, name=name, version=version, author="t", entries=entries, **options,
    )


class TestDictStore(unittest.TestCase):
    def test_builtin_lowest(self):
        st = DictStore()
        st.set_builtin({"Glow": "发光"})
        st.upsert_user("Glow", "光晕")
        self.assertEqual(st.lookup("Glow").zh, "光晕")
        self.assertEqual(st.lookup("Glow").layer, "user")

    def test_locked_beats_user(self):
        # 两层的优先级由 resolve 保证，不依赖 upsert 的"解锁"语义
        st = DictStore()
        st.set_user({"Glow": "光晕"}, locked=False)
        st.set_user({"Glow": "发光"}, locked=True)
        self.assertEqual(st.lookup("Glow").zh, "发光")
        self.assertTrue(st.lookup("Glow").locked)

    def test_upsert_unlock_moves_to_user(self):
        st = DictStore()
        st.upsert_user("Glow", "发光", locked=True)
        self.assertEqual(st.lookup("Glow").layer, "user_locked")
        st.upsert_user("Glow", "光晕", locked=False)
        self.assertEqual(st.lookup("Glow").layer, "user")
        self.assertEqual(st.lookup("Glow").zh, "光晕")

    def test_pack_over_builtin(self):
        st = DictStore()
        st.set_builtin({"Glow": "发光"})
        info = st.import_pack(_pack({"Glow": {"zh": "光晕"}}))
        self.assertFalse(info.signed)
        self.assertEqual(info.verify_msg, "未签名")
        self.assertEqual(st.lookup("Glow").zh, "光晕")
        self.assertEqual(st.lookup("Glow").layer, "pack")

    def test_user_beats_pack(self):
        st = DictStore()
        st.import_pack(_pack({"Glow": {"zh": "光晕"}}))
        st.upsert_user("Glow", "发光")
        self.assertEqual(st.lookup("Glow").zh, "发光")

    def test_locked_pack_cannot_override(self):
        st = DictStore()
        st.upsert_user("Glow", "我的发光", locked=True)
        st.import_pack(_pack({"Glow": {"zh": "包的光晕"}}))
        self.assertEqual(st.lookup("Glow").zh, "我的发光")

    def test_pack_priority_within_layer(self):
        st = DictStore()
        st.import_pack(_pack({"Glow": {"zh": "低", "priority": 10}}, pack_id="a", name="A"))
        st.import_pack(_pack({"Glow": {"zh": "高", "priority": 90}}, pack_id="b", name="B"))
        self.assertEqual(st.lookup("Glow").zh, "高")

    def test_disable_pack(self):
        st = DictStore()
        st.set_builtin({"Glow": "发光"})
        info = st.import_pack(_pack({"Glow": {"zh": "光晕"}}))
        st.set_pack_enabled(info.pack_id, False)
        self.assertEqual(st.lookup("Glow").zh, "发光")

    def test_preview(self):
        st = DictStore()
        st.set_builtin({"Glow": "发光", "Blur": "模糊"})
        st.upsert_user("Locked", "锁住", locked=True)
        pv = st.preview_import(
            _pack({"Glow": {"zh": "光晕"}, "Blur": {"zh": "模糊"}, "New": {"zh": "新的"},
                   "Locked": {"zh": "被挡"}})
        )
        self.assertEqual(pv.new, ["New"])
        self.assertEqual(pv.identical, ["Blur"])
        self.assertEqual(len(pv.overwrite_user), 1)
        self.assertEqual(pv.blocked, ["Locked"])
        self.assertIn("新增 1 条", pv.summary)

    def test_import_upgrade_replaces(self):
        st = DictStore()
        st.import_pack(_pack({"A": {"zh": "旧"}}, version="1.0.0"))
        st.import_pack(_pack({"B": {"zh": "新"}}, version="2.0.0"))
        self.assertEqual(len(st.packs), 1)
        self.assertEqual(st.packs[0].version, "2.0.0")
        self.assertIsNone(st.lookup("A"))
        self.assertEqual(st.lookup("B").zh, "新")

    def test_import_signed_pack(self):
        data = _pack(
            {"Glow": {"zh": "光晕"}}, sign_key=Ed25519PrivateKey.generate().private_bytes_raw(),
            glossary={"Glow": {"zh": "光晕"}}, readme="词典说明", license_text="MIT",
        )
        store = DictStore()
        info = store.import_pack(data)
        self.assertTrue(info.signed)
        self.assertEqual(info.verify_msg, "签名有效")
        self.assertEqual(store.lookup("Glow").zh, "光晕")

    def test_rejected_import_preserves_original_layers_packs_and_cache(self):
        store = DictStore()
        original = store.import_pack(_pack({"Glow": {"zh": "光晕"}}))
        store.upsert_user("Blur", "模糊", locked=True)
        revision = store.revision
        unsigned = _pack({"Glow": {"zh": "发光"}}, version="2")
        modified = rewrite_pack_json(
            unsigned, "dict.json", lambda obj: obj["entries"]["Glow"].update(zh="强光"),
        )
        signed = _pack(
            {"Glow": {"zh": "发光"}}, version="2",
            sign_key=Ed25519PrivateKey.generate().private_bytes_raw(),
        )
        invalid_signature = base64.b64encode(bytes(64)).decode("ascii")
        invalid = rewrite_pack_json(
            signed, "manifest.json", lambda obj: obj["signature"].update(sig=invalid_signature),
        )

        for data, reason in ((modified, "dict.json 校验失败"), (invalid, "签名校验失败")):
            with self.subTest(reason=reason):
                previous_cache = store._resolution_cache
                preview = store.preview_import(data)
                self.assertFalse(preview.verify_ok)
                self.assertIn(reason, preview.verify_msg)
                self.assertIs(store._resolution_cache, previous_cache)
                resolved = store.resolve()
                with self.assertRaisesRegex(PackError, reason):
                    store.import_pack(data)
                self.assertEqual(store.revision, revision)
                self.assertIs(store._resolution_cache, resolved)
                self.assertEqual(store.packs, [original])
                self.assertEqual(store.lookup("Glow").zh, "光晕")
                self.assertEqual(store.lookup("Blur").zh, "模糊")

    def test_signing_dependency_is_required_only_for_signed_imports(self):
        entries = {"Glow": {"zh": "光晕"}}
        signed = _pack(entries, sign_key=Ed25519PrivateKey.generate().private_bytes_raw())
        store = DictStore()
        with patch.dict(sys.modules, {"cryptography.hazmat.primitives.asymmetric.ed25519": None}):
            with self.assertRaisesRegex(PackError, "无法验证签名"):
                store.import_pack(signed)
            self.assertEqual(store.packs, [])
            self.assertEqual(store.import_pack(_pack(entries)).verify_msg, "未签名")

    def test_remove_pack(self):
        st = DictStore()
        info = st.import_pack(_pack({"Glow": {"zh": "光晕"}}))
        self.assertTrue(st.remove_pack(info.pack_id))
        self.assertIsNone(st.lookup("Glow"))

    def test_remove_user(self):
        st = DictStore()
        st.upsert_user("Glow", "发光")
        self.assertTrue(st.remove_user("Glow"))
        self.assertIsNone(st.lookup("Glow"))
        self.assertFalse(st.remove_user("nope"))

    def test_stats(self):
        st = DictStore()
        st.set_builtin({"A": "甲"})
        st.upsert_user("B", "乙")
        s = st.stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["layer:builtin"], 1)
        self.assertEqual(s["layer:user"], 1)

    def test_save_load_roundtrip(self):
        st = DictStore()
        st.set_builtin({"A": "甲"})
        st.upsert_user("B", "乙")
        st.upsert_user("C", "丙", locked=True)
        st.import_pack(_pack({"D": {"zh": "丁"}}))
        tmp = os.path.join(tempfile.mkdtemp(), "store.json")
        st.save(tmp)
        st2 = DictStore.load(tmp)
        self.assertEqual(st2.lookup("A").zh, "甲")
        self.assertEqual(st2.lookup("B").zh, "乙")
        self.assertEqual(st2.lookup("C").zh, "丙")
        self.assertTrue(st2.lookup("C").locked)
        self.assertEqual(st2.lookup("D").zh, "丁")
        self.assertEqual(len(st2.packs), 1)

    def test_load_missing_is_empty(self):
        st = DictStore.load(os.path.join(tempfile.mkdtemp(), "nope.json"))
        self.assertEqual(st.resolve(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
