"""壁纸引擎接入（core/wallpaper_engine.py）的回归测试。

重点覆盖三类**曾经或容易出错**的判定：
  1. PKG 压缩判定必须走完整链校验 —— 用「未压缩数据前 8 字节恰好是个大数」
     的样本证明它不会被误判成 LZ4（早期粗判版本 100% 误判）；
  2. project.json 是不可信输入 —— `..\\..\\Windows` 这类路径必须被目录包含校验挡住；
  3. WE 的「从库中移除」写在 visibility.json 里，项目目录仍在盘上 —— 必须过滤掉。

测试全部在临时目录里造夹具，绝不碰真实用户目录，也不需要真的装壁纸引擎。
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest

from core import wallpaper_engine as we


def _make_pkg(entries):
    """造一个最小 PKG 容器。

    entries: [(path, bytes)] —— 全部按**未压缩**存放（真实工坊包里绝大多数也是如此）。
    """
    index = []
    body = b""
    for path, data in entries:
        index.append((path, len(body), len(data)))
        body += data
    out = b""
    out += struct.pack("<i", 8) + b"PKGV0024"
    out += struct.pack("<i", len(index))
    for path, off, length in index:
        pb = path.encode("utf-8")
        out += struct.pack("<i", len(pb)) + pb
        out += struct.pack("<II", off, length)
    return out + body


def _jpeg_stub(size=2048):
    """带合法 SOI/DQT 头的一段伪 JPEG 数据（测试只看签名与长度）。"""
    return b"\xff\xd8\xff\xdb" + b"\x00" * size


def _mp4_stub(size=4096):
    """带 ftyp box 的一段伪 MP4（box 起点前置 4 字节长度字段）。"""
    return struct.pack(">I", 24) + b"ftypisom" + b"\x00" * 16 + b"\x00" * size


class TestLz4(unittest.TestCase):
    def test_literal_only_block(self):
        # token 高 4 位 = 字面量长度；0x50 = 5 个字节，无匹配段
        src = bytes([0x50]) + b"hello"
        self.assertEqual(we._lz4_block(src, 5), b"hello")

    def test_dst_size_mismatch_rejected(self):
        src = bytes([0x50]) + b"hello"
        with self.assertRaises(ValueError):
            we._lz4_block(src, 99)

    def test_oversized_rejected(self):
        with self.assertRaises(ValueError):
            we._lz4_block(b"\x00", we.MAX_DECOMPRESSED_BYTES + 1)

    def test_match_offset_out_of_range(self):
        # 字面量 1 字节 + 匹配偏移 100（远超已输出长度）→ 必须拒绝
        src = bytes([0x10]) + b"A" + struct.pack("<H", 100) + bytes([0x00])
        with self.assertRaises(ValueError):
            we._lz4_block(src, 8)


class TestCompressedProbe(unittest.TestCase):
    """压缩判定：完整链校验，绝不能被「前 8 字节恰好是大数」骗过。"""

    def test_raw_data_with_big_prefix_is_not_lz4(self):
        # 前 8 字节解出来是 0x7FFFFFFFFFFFFFFF 量级的「原始大小」，但后面不是链
        data = struct.pack("<Q", 0x00FFFFFFFFFFFF) + b"\x00" * 64
        self.assertIsNone(we._probe_compressed(data, 0, len(data)))

    def test_original_smaller_than_stored_is_not_lz4(self):
        # 原始大小 <= 存储大小 ⇒ 不可能是压缩（压缩只会更省）
        data = struct.pack("<Q", 10) + b"\x00" * 100
        self.assertIsNone(we._probe_compressed(data, 0, len(data)))

    def test_broken_chain_is_not_lz4(self):
        # 声称原始 1000 字节，但链里 uncomp<=0 ⇒ 不是合法链
        data = struct.pack("<Q", 1000) + struct.pack("<ii", 0, 10) + b"\x00" * 10
        self.assertIsNone(we._probe_compressed(data, 0, len(data)))

    def test_chain_not_consuming_whole_entry_is_not_lz4(self):
        # 链只解出 4 字节就结束，但条目后面还有垃圾 ⇒ 判定必须为「不是」
        block = bytes([0x40]) + b"abcd"          # 字面量 4 字节
        chain = struct.pack("<Q", 4) + struct.pack("<ii", 4, len(block)) + block
        data = chain + b"\xde\xad\xbe\xef"
        self.assertIsNone(we._probe_compressed(data, 0, len(data)))


class TestParsePkg(unittest.TestCase):
    def test_parses_index_and_offsets(self):
        pkg = _make_pkg([("scene.json", b'{"a":1}'), ("materials/x.tex", b"TEXV0005" + b"\x00" * 8)])
        magic, entries = we.parse_pkg(pkg)
        self.assertEqual(magic, "PKGV0024")
        self.assertEqual([e[0] for e in entries], ["scene.json", "materials/x.tex"])
        for path, off, stored, size, is_lz4 in entries:
            self.assertFalse(is_lz4, "未压缩条目不该被判成 LZ4: %s" % path)
            self.assertEqual(stored, size)
            self.assertLessEqual(off + stored, len(pkg))

    def test_bad_magic_rejected(self):
        bad = struct.pack("<i", 8) + b"NOTAPKG1" + struct.pack("<i", 0)
        with self.assertRaises(ValueError):
            we.parse_pkg(bad)

    def test_truncated_header_rejected(self):
        with self.assertRaises(ValueError):
            we.parse_pkg(b"\x08\x00")

    def test_absurd_entry_count_rejected(self):
        bad = struct.pack("<i", 8) + b"PKGV0024" + struct.pack("<i", 2_000_000)
        with self.assertRaises(ValueError):
            we.parse_pkg(bad)


class TestFtypAndPayload(unittest.TestCase):
    def test_find_ftyp_in_window(self):
        # data = 20 个 0 + stub；stub 以 4 字节 box 长度开头，ftyp 在 stub 内偏移 4
        # ⇒ ftyp 位于 24，box 起点 = 24 - 4 = 20
        data = b"\x00" * 20 + _mp4_stub(64)
        self.assertEqual(we._find_ftyp(data), 20)

    def test_find_ftyp_outside_window(self):
        data = b"\x00" * 400 + b"ftyp"
        self.assertEqual(we._find_ftyp(data), -1)

    def test_payload_prefers_video_over_embedded_jpeg(self):
        # 视频里带封面图：先判 MP4，否则会把封面当成整张壁纸
        body = b"TEXV0005\x00TEXI0001\x00" + b"\x00" * 8 + _mp4_stub(256)
        kind, payload = we._tex_payload(body)
        self.assertEqual(kind, "video")

    def test_payload_finds_jpeg(self):
        body = b"TEXV0005\x00TEXI0001\x00" + b"\x00" * 8 + _jpeg_stub(128)
        kind, payload = we._tex_payload(body)
        self.assertEqual(kind, "jpeg")
        self.assertTrue(payload.startswith(b"\xff\xd8\xff"))

    def test_payload_empty_when_nothing_matches(self):
        kind, payload = we._tex_payload(b"TEXV0005\x00" + b"\x01" * 200)
        self.assertEqual(kind, "")
        self.assertEqual(payload, b"")

    def test_aux_texture_detection(self):
        self.assertTrue(we._is_aux_tex("materials/pistols/x_normal.tex"))
        self.assertTrue(we._is_aux_tex("materials/masks/iris_mask.tex"))
        # ★ 歧义词不能命中：这些是壁纸作者给主画面起的名字
        self.assertFalse(we._is_aux_tex("materials/new_wall_combined4k.tex"))
        self.assertFalse(we._is_aux_tex("materials/vortex.tex"))
        self.assertFalse(we._is_aux_tex("materials/neon.tex"))


class TestPathContainment(unittest.TestCase):
    """project.json 是不可信输入：越界路径必须挡住。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aeloc_we_contain_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_normal_relative_ok(self):
        self.assertTrue(we._contained(self.root, "preview.jpg"))
        self.assertTrue(we._contained(self.root, "materials/a.tex"))

    def test_parent_traversal_rejected(self):
        self.assertFalse(we._contained(self.root, r"..\..\Windows\System32\cmd.exe"))
        self.assertFalse(we._contained(self.root, "../../etc/passwd"))

    def test_absolute_outside_rejected(self):
        self.assertFalse(we._contained(self.root, r"C:\Windows\notepad.exe"))

    def test_empty_rejected(self):
        self.assertFalse(we._contained(self.root, ""))


class TestProjectReading(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aeloc_we_proj_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _project(self, name, obj, files=None):
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)
        for fn in files or []:
            p = os.path.join(d, fn)
            os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
            with open(p, "wb") as f:
                f.write(b"x")
        return d

    def test_reads_video_project(self):
        d = self._project("v1", {"title": "测试视频", "type": "video", "file": "a.mp4",
                                 "preview": "preview.jpg"},
                          files=["a.mp4", "preview.jpg"])
        w = we._read_project(d)
        self.assertIsNotNone(w)
        self.assertEqual(w.kind, "video")
        self.assertEqual(w.title, "测试视频")
        self.assertTrue(w.preview.endswith("preview.jpg"))

    def test_missing_main_file_dropped(self):
        d = self._project("v2", {"title": "缺文件", "type": "video", "file": "nope.mp4"})
        self.assertIsNone(we._read_project(d))

    def test_out_of_bounds_file_dropped(self):
        d = self._project("v3", {"title": "越界", "type": "video",
                                 "file": r"..\..\Windows\System32\cmd.exe"})
        self.assertIsNone(we._read_project(d))

    def test_out_of_bounds_preview_kept_but_no_preview(self):
        d = self._project("v4", {"title": "预览越界", "type": "video", "file": "a.mp4",
                                 "preview": r"..\..\secret.jpg"}, files=["a.mp4"])
        w = we._read_project(d)
        self.assertIsNotNone(w)
        self.assertEqual(w.preview, "")

    def test_scene_falls_back_to_scene_pkg(self):
        # 声明 scene.json 但只发 scene.pkg —— 工坊里非常常见的形态
        d = self._project("s1", {"title": "场景", "type": "scene", "file": "scene.json"},
                          files=["scene.pkg"])
        w = we._read_project(d)
        self.assertIsNotNone(w)
        self.assertEqual(w.kind, "scene")
        self.assertEqual(w.main, "scene.pkg")

    def test_scene_single_pkg_by_extension(self):
        d = self._project("s2", {"title": "场景2", "type": "scene"}, files=["only.pkg"])
        w = we._read_project(d)
        self.assertIsNotNone(w)
        self.assertEqual(w.main, "only.pkg")

    def test_broken_json_skipped(self):
        d = os.path.join(self.root, "bad")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertIsNone(we._read_project(d))


class TestVisibility(unittest.TestCase):
    """WE「从库中移除」只改 visibility.json，项目目录仍在盘上。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aeloc_we_vis_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_hidden_names_parsed(self):
        with open(os.path.join(self.root, "visibility.json"), "w", encoding="utf-8") as f:
            json.dump({"gone1": False, "keep": True, "nested": {"gone2": False}}, f)
        names = we._hidden_names(self.root)
        self.assertIn("gone1", names)
        self.assertIn("gone2", names)
        self.assertNotIn("keep", names)

    def test_missing_file_does_not_filter(self):
        self.assertEqual(we._hidden_names(self.root), set())

    def test_broken_file_does_not_filter(self):
        with open(os.path.join(self.root, "visibility.json"), "w", encoding="utf-8") as f:
            f.write("nonsense")
        self.assertEqual(we._hidden_names(self.root), set())

    def test_scan_skips_hidden_projects(self):
        for name in ("visible", "hidden"):
            d = os.path.join(self.root, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
                json.dump({"title": name, "type": "video", "file": "a.mp4"}, f)
            with open(os.path.join(d, "a.mp4"), "wb") as f:
                f.write(b"x")
        with open(os.path.join(self.root, "visibility.json"), "w", encoding="utf-8") as f:
            json.dump({"hidden": False}, f)
        items = we.scan("", [self.root])
        self.assertEqual([w.id for w in items], ["visible"])


class TestScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aeloc_we_scan_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_scan_dedupes_and_sorts(self):
        for name, title in (("b", "Beta"), ("a", "alpha")):
            d = os.path.join(self.root, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
                json.dump({"title": title, "type": "video", "file": "v.mp4"}, f)
            with open(os.path.join(d, "v.mp4"), "wb") as f:
                f.write(b"x")
        # 同一个根传两次（大小写不同）—— 结果不该翻倍
        items = we.scan("", [self.root, self.root.upper()])
        self.assertEqual(len(items), 2)
        self.assertEqual([w.title for w in items], ["alpha", "Beta"])

    def test_find_install_env_override(self):
        fake = os.path.join(self.root, "we")
        os.makedirs(os.path.join(fake, "distribution"), exist_ok=True)
        with open(os.path.join(fake, "distribution", "version.json"), "w") as f:
            f.write("{}")
        old = os.environ.get("AELOC_WE_ROOT")
        os.environ["AELOC_WE_ROOT"] = fake
        try:
            self.assertEqual(os.path.normcase(we.find_install()),
                             os.path.normcase(fake))
        finally:
            if old is None:
                os.environ.pop("AELOC_WE_ROOT", None)
            else:
                os.environ["AELOC_WE_ROOT"] = old

    def test_env_dir_without_markers_not_recognized(self):
        """装了别的 Steam 游戏也不该把任意目录当成壁纸引擎安装根。

        ★ 这里只断言 `_is_we_root`：`find_install` 在环境变量不匹配时会继续
          按注册表探测**本机真实安装**，那是有意行为（环境变量是覆盖，不是隔离）。
        """
        self.assertFalse(we._is_we_root(self.root))

    def test_we_root_marker_variants(self):
        """老版把 exe 放根目录，新版挪进 distribution\\ —— 两种都要认。"""
        variants = (
            "wallpaper32.exe",
            "wallpaper64.exe",
            os.path.join("distribution", "wallpaper32.exe"),
            os.path.join("distribution", "version.json"),
        )
        for i, rel in enumerate(variants):
            d = os.path.join(self.root, "v%d" % i)
            os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write("x")
            self.assertTrue(we._is_we_root(d), "应识别为安装根: %s" % rel)


class TestScenePayload(unittest.TestCase):
    """scene.pkg 主画面挑选：排除辅助纹理、取最大载荷、只认可解码签名。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aeloc_we_scene_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _write_pkg(self, entries):
        p = os.path.join(self.root, "scene.pkg")
        with open(p, "wb") as f:
            f.write(_make_pkg(entries))
        return p

    def test_picks_largest_non_aux(self):
        p = self._write_pkg([
            ("materials/masks/a_mask.tex", _jpeg_stub(8192)),
            ("materials/main.tex", _jpeg_stub(4096)),
        ])
        kind, payload, path = we.scene_payload(p)
        self.assertEqual(path, "materials/main.tex")
        self.assertEqual(kind, "jpeg")

    def test_aux_only_returns_empty(self):
        # 只有法线/遮罩 —— 不能拿它当壁纸画面
        p = self._write_pkg([
            ("materials/effects/waterripplenormal.tex", _jpeg_stub(4096)),
        ])
        kind, payload, path = we.scene_payload(p)
        self.assertEqual(kind, "")
        self.assertEqual(payload, b"")

    def test_embedded_video_wins(self):
        p = self._write_pkg([
            ("materials/main.tex", _mp4_stub(4096)),
        ])
        kind, payload, _ = we.scene_payload(p)
        self.assertEqual(kind, "video")
        self.assertTrue(payload[4:8] == b"ftyp")

    def test_no_tex_returns_empty(self):
        p = self._write_pkg([("scene.json", b'{"a":1}')])
        self.assertEqual(we.scene_payload(p)[0], "")



class ReadPropertiesTest(unittest.TestCase):
    """壁纸自带参数（project.json → general.properties）的读取。

    ★ 这些值**只读**：消费方是 WE 自己的渲染器，本工具取的是提取出来的画面。
      所以这里只保证「读得对、不会被不可信输入带偏」，不保证"改了会生效"。
    """

    def _mk(self, props, kind="scene"):
        d = tempfile.mkdtemp(prefix="aeloc_props_")
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "t", "type": kind, "file": "scene.pkg",
                       "general": {"properties": props}}, f)
        open(os.path.join(d, "scene.pkg"), "wb").close()
        return we.Wallpaper(id="1", title="t", kind=kind, main="scene.pkg", dir=d)

    def test_sorted_by_order_and_types_kept(self):
        w = self._mk({
            "b": {"order": 20, "index": 1, "text": "后", "type": "slider",
                  "value": 0.3, "min": 0, "max": 1, "step": 0.01},
            "a": {"order": 10, "index": 0, "text": "先", "type": "combo",
                  "value": "1", "options": [{"label": "甲", "value": "0"},
                                            {"label": "乙", "value": "1"}]},
        })
        props = we.read_properties(w)
        self.assertEqual([p["name"] for p in props], ["a", "b"])
        self.assertEqual(props[0]["type"], "combo")
        self.assertEqual(len(props[0]["options"]), 2)
        self.assertEqual(props[1]["min"], 0)
        self.assertEqual(props[1]["max"], 1)

    def test_html_stripped_and_localization_key_falls_back_to_name(self):
        w = self._mk({
            "schemecolor": {"order": 100, "text": "ui_browse_properties_scheme_color",
                            "type": "color", "value": "0.8 0.2 0.4"},
            "x": {"order": 101, "text": "<b>粗体</b>&nbsp;标题", "type": "text",
                  "value": ""},
        })
        props = {p["name"]: p for p in we.read_properties(w)}
        # 本地化 key 不当标题用 —— 退回属性名，界面上才不会出现一串 ui_xxx
        self.assertEqual(props["schemecolor"]["label"], "schemecolor")
        self.assertEqual(props["x"]["label"], "粗体 标题")

    def test_unknown_type_and_bad_json_are_ignored(self):
        w = self._mk({"a": {"order": 1, "text": "x", "type": "nonsense", "value": 1}})
        self.assertEqual(we.read_properties(w), [])
        d = tempfile.mkdtemp(prefix="aeloc_props_bad_")
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        bad = we.Wallpaper(id="2", title="t", kind="scene", main="scene.pkg", dir=d)
        self.assertEqual(we.read_properties(bad), [])

    def test_missing_general_returns_empty(self):
        d = tempfile.mkdtemp(prefix="aeloc_props_none_")
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "t", "type": "video"}, f)
        w = we.Wallpaper(id="3", title="t", kind="video", main="a.mp4", dir=d)
        self.assertEqual(we.read_properties(w), [])


class WPropertyCaseTest(unittest.TestCase):
    """WE 的 config.json 条目键是「盘符大写」的绝对路径，而扫描出来的路径可能是小写。

    ★ 2026-11-17 的真 bug：按 abspath 直接当键写，会在同一份 config 里造出一个
      只差大小写的重复条目 —— WE 只读它自己那个键，于是所有写入都"没有反应"。
    """

    def _setup(self, extra_entry=None):
        import json

        d = tempfile.mkdtemp(prefix="aeloc_wpc_")
        we = os.path.join(d, "we")
        wdir = os.path.join(d, "wp", "42")
        os.makedirs(we)
        os.makedirs(wdir)
        open(os.path.join(wdir, "scene.pkg"), "wb").close()
        lower = os.path.join(wdir, "scene.pkg").replace("\\", "/").lower()
        upper = lower[0].upper() + lower[1:]
        cfg = {"10001": {"version": 5, "wproperties": {upper: {"Monitor0": {"a": 1}}}}}
        if extra_entry:
            cfg["10001"]["wproperties"][extra_entry] = {"Monitor0": {"b": 2}}
        path = os.path.join(we, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        return d, we, wdir, upper, lower, path

    def test_read_matches_case_insensitively(self):
        d, we_dir, wdir, upper, lower, _p = self._setup()
        w = we.Wallpaper(id="42", title="t", kind="scene", main="scene.pkg", dir=wdir)
        self.assertEqual(we.read_wproperty_overrides(w, we_dir), {"a": 1})

    def test_write_reuses_we_key_and_merges_duplicates(self):
        import json

        d, we_dir, wdir, upper, lower, path = self._setup(extra_entry="__other__")
        # 造一个只差大小写的重复条目（就是旧代码干的事）
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["10001"]["wproperties"][lower] = {"Monitor0": {"b": 2, "c": 3}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

        w = we.Wallpaper(id="42", title="t", kind="scene", main="scene.pkg", dir=wdir)
        ok, _msg = we.write_wproperty(w, we_dir, "d", 4)
        self.assertTrue(ok)

        with open(path, encoding="utf-8") as fh:
            after = json.load(fh)
        entries = after["10001"]["wproperties"]
        keys = [k for k in entries if k.lower().endswith("scene.pkg")]
        self.assertEqual(len(keys), 1, "只该剩一个键：%r" % keys)
        self.assertEqual(keys[0], upper, "必须沿用 WE 自己的键（盘符大写）")
        self.assertEqual(entries[upper]["Monitor0"]["a"], 1)
        self.assertEqual(entries[upper]["Monitor0"]["b"], 2)
        self.assertEqual(entries[upper]["Monitor0"]["c"], 3)
        self.assertEqual(entries[upper]["Monitor0"]["d"], 4)

if __name__ == "__main__":
    unittest.main()
