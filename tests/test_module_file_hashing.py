import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import aescan, proc


class BoundedFile(io.FileIO):
    def read(self, size=-1):
        if size < 0 or size > 1024 * 1024:
            raise AssertionError("Module hashing requested an unbounded read")
        return super().read(size)

    def readinto(self, buffer):
        if len(buffer) > 1024 * 1024:
            raise AssertionError("Module hashing requested an oversized buffer")
        return super().readinto(buffer)


class ModuleFileHashingTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "module.dll"
        self.data = bytes(range(256)) * 8192 + b"last partial block"
        self.path.write_bytes(self.data)
        self.expected = hashlib.sha256(self.data).hexdigest()
        self.host = SimpleNamespace(pid=1, creation_time=10)

    def test_streamed_digest_reuses_only_matching_host_and_file_identity(self):
        with patch.object(proc, "_digests", {}), \
             patch.object(Path, "open", autospec=True, side_effect=BoundedFile) as reads:
            self.assertEqual(proc.module_digest(self.host, self.path), self.expected)
            self.assertEqual(proc.module_digest(self.host, self.path), self.expected)
            self.assertEqual(reads.call_count, 1)
            self.host.creation_time += 1
            self.assertEqual(proc.module_digest(self.host, self.path), self.expected)
            self.assertEqual(reads.call_count, 2)
            with open(self.path, "ab") as stream:
                stream.write(b"+")
            self.assertEqual(proc.module_digest(self.host, self.path),
                             hashlib.sha256(self.data + b"+").hexdigest())
            self.assertEqual(reads.call_count, 3)

    def test_failed_hash_is_retried(self):
        with patch.object(proc, "_digests", {}):
            with patch.object(Path, "open", side_effect=PermissionError("unreadable")):
                with self.assertRaises(PermissionError):
                    proc.module_digest(self.host, self.path)
            self.assertEqual(proc.module_digest(self.host, self.path), self.expected)

    def test_static_recheck_streams_and_still_detects_same_size_changes(self):
        fixture = SimpleNamespace(exe=self.path, version="1", modules={
            "module": {"path": self.path, "sha256": self.expected}})
        with patch.object(proc, "file_version", return_value="1"), \
             patch.object(Path, "open", autospec=True, side_effect=BoundedFile):
            aescan.ModuleTools.verify_files(fixture)
            with open(self.path, "r+b") as stream:
                stream.seek(-1, io.SEEK_END)
                stream.write(b"!")
            with self.assertRaisesRegex(ValueError, "分析期间模块发生变化"):
                aescan.ModuleTools.verify_files(fixture)
