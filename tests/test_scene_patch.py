"""场景包重打包（core/scene_patch.py）的回归测试。

这里**不跑真转码**（要 ffmpeg、几十秒），只覆盖三件容易错的事：
  1. 哪些 .tex 是"视频贴图"（Chromium 播不了的那批）；
  2. 重打包容器能被自己的 parse_pkg 读回来（索引偏移不能算错）；
  3. 补丁包缓存的新鲜度判断（源包变了就得重转）。
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest

from core import scene_patch as sp
from core import wallpaper_engine as we


def _make_pkg(entries):
    """造一个最小 PKGV 容器（全部未压缩）。entries: [(path, bytes)]"""
    index = []
    body = b""
    for path, data in entries:
        index.append((path, len(body), len(data)))
        body += data
    out = struct.pack("<i", 8) + b"PKGV0024" + struct.pack("<i", len(index))
    for path, off, length in index:
        pb = path.encode("utf-8")
        out += struct.pack("<i", len(pb)) + pb + struct.pack("<II", off, length)
    return out + body


def _jpeg_stub(n=4096):
    return b"\xff\xd8\xff\xdb" + b"\x00" * n


def _mp4_stub(n=8192):
    head = struct.pack(">I", 24) + b"ftypisom" + b"\x00\x00\x02\x00isomiso2"
    head = head.ljust(91, b"\x00")          # 模拟 .tex 头之后才是 MP4
    return head + b"\x00" * n


class ScenePatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeloc_patch_")
        self.wp = os.path.join(self.tmp, "wp")
        os.makedirs(self.wp)
        self.pkg = os.path.join(self.wp, "scene.pkg")
        with open(self.pkg, "wb") as f:
            f.write(_make_pkg([
                ("scene.json", b'{"objects":[]}'),
                ("materials/photo.tex", _jpeg_stub()),
                ("materials/60fps girl.tex", _mp4_stub()),
            ]))

    def test_find_video_texes(self):
        self.assertEqual(sp.find_video_texes(self.pkg), ["materials/60fps girl.tex"])

    def test_write_pkg_roundtrip(self):
        """内存载荷 + 文件引用混写，读回来内容必须逐字节一致。"""
        side = os.path.join(self.tmp, "side.bin")
        with open(side, "wb") as f:
            f.write(b"0123456789" * 10)
        out = os.path.join(self.tmp, "rebuilt.pkg")
        sp._write_pkg(out, [
            ("a.json", b'{"x":1}'),
            ("b.tex", (side, 5, 20)),
            ("c.bin", bytes(range(256))),
        ])
        with open(out, "rb") as f:
            data = f.read()
        magic, entries = we.parse_pkg(data)
        self.assertEqual(magic, "PKGV0024")
        self.assertEqual([e[0] for e in entries], ["a.json", "b.tex", "c.bin"])
        bodies = {e[0]: we._read_entry(data, e[1], e[2], e[3], e[4]) for e in entries}
        self.assertEqual(bodies["a.json"], b'{"x":1}')
        self.assertEqual(bodies["b.tex"], (b"0123456789" * 10)[5:25])
        self.assertEqual(bodies["c.bin"], bytes(range(256)))

    def test_cached_pkg_invalidation(self):
        root = os.path.join(self.tmp, "data")
        os.makedirs(root)
        self.assertEqual(sp.cached_patched_pkg(self.wp, root, "42"), "")
        cache = sp.cache_dir(root)
        out = os.path.join(cache, "42.pkg")
        with open(out, "wb") as f:
            f.write(b"x")
        st = os.stat(self.pkg)
        with open(out + ".json", "w", encoding="utf-8") as f:
            json.dump({"mtime": st.st_mtime, "size": st.st_size}, f)
        self.assertEqual(sp.cached_patched_pkg(self.wp, root, "42"), out)
        # 源包变过 → 缓存作废
        with open(out + ".json", "w", encoding="utf-8") as f:
            json.dump({"mtime": st.st_mtime - 10, "size": st.st_size}, f)
        self.assertEqual(sp.cached_patched_pkg(self.wp, root, "42"), "")


if __name__ == "__main__":
    unittest.main()
