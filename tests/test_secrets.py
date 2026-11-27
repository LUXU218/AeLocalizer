"""API Key 加密存储测试（DPAPI + 备份/恢复）。

测试曾在默认数据目录写入假密钥，覆盖了已保存的 DPAPI 密文。
所有用例在临时沙盒内验证加密、替换与恢复。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import secrets  # noqa: E402


def _dpapi_ok() -> bool:
    try:
        return secrets.unprotect(secrets.protect("probe")) == "probe"
    except Exception:
        return False


@unittest.skipUnless(_dpapi_ok(), "DPAPI 不可用（非 Windows）")
class TestSecrets(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aeloc_test_secrets_")
        self.path = os.path.join(self.dir, "ai_key.dat")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip(self):
        ok, _ = secrets.save_key("sk-abcdef-1234567890", self.path)
        self.assertTrue(ok)
        self.assertEqual(secrets.load_key(self.path), "sk-abcdef-1234567890")
        self.assertTrue(secrets.has_key(self.path))

    def test_no_plaintext_on_disk(self):
        secrets.save_key("sk-plain-should-not-appear", self.path)
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(secrets.MAGIC))
        self.assertNotIn(b"sk-plain-should-not-appear", raw)
        self.assertNotIn(b"should-not-appear", raw)

    def test_empty_key_rejected(self):
        ok, msg = secrets.save_key("   ", self.path)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.path))

    def test_empty_key_never_overwrites_existing(self):
        secrets.save_key("sk-keep-me-0000", self.path)
        with open(self.path, "rb") as f:
            before = f.read()
        ok, _ = secrets.save_key("", self.path)     # 空 -> 必须拒绝
        self.assertFalse(ok)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(secrets.load_key(self.path), "sk-keep-me-0000")

    def test_same_key_does_not_create_backup(self):
        secrets.save_key("sk-same-1234", self.path)
        secrets.save_key("sk-same-1234", self.path)
        self.assertFalse(secrets.has_previous(self.path))

    def test_change_keeps_previous_and_restores(self):
        secrets.save_key("sk-old-1111", self.path)
        secrets.save_key("sk-new-2222", self.path)
        self.assertTrue(secrets.has_previous(self.path))
        self.assertEqual(secrets.previous_key(self.path), "sk-old-1111")
        self.assertEqual(secrets.load_key(self.path), "sk-new-2222")

        got = secrets.restore_previous(self.path)
        self.assertEqual(got, "sk-old-1111")
        self.assertEqual(secrets.load_key(self.path), "sk-old-1111")

    def test_clear_removes_backup_too(self):
        secrets.save_key("sk-a-1111", self.path)
        secrets.save_key("sk-b-2222", self.path)
        self.assertTrue(secrets.clear_key(self.path))
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + secrets.BACKUP_SUFFIX))
        self.assertEqual(secrets.load_key(self.path), "")

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "wb") as f:
            f.write(b"not-our-format")
        self.assertEqual(secrets.load_key(self.path), "")
        self.assertFalse(secrets.has_previous(self.path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
