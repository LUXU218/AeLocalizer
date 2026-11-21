"""PiPL 解析测试：用合成字节流验证结构解析与安全边界。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipl import (  # noqa: E402
    PluginInfo, PiplString, iter_pipl_strings, parse_pipl, read_plugin,
)


def make_item(attr: str, value: bytes, pad: int = 0) -> bytes:
    """构造一个 PiPL 字符串 item。"""
    body = bytes([len(value)]) + value + b"\x00" * pad
    item_len = len(body)
    return b"MIB8" + attr.encode("ascii") + b"\x00\x00\x00\x00" + item_len.to_bytes(4, "little") + body


class TestPiplParse(unittest.TestCase):
    def test_single_name(self):
        data = make_item("eman", b"Glow")
        out = parse_pipl(data)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].key, "name")
        self.assertEqual(out[0].text(), "Glow")
        self.assertTrue(out[0].writable())

    def test_all_three_keys(self):
        data = (
            make_item("eman", b"Glow Effect")
            + make_item("ANMe", b"ADBE Glow")
            + make_item("gtac", b"Stylize")
        )
        out = parse_pipl(data)
        self.assertEqual([s.key for s in out], ["name", "matchName", "category"])
        self.assertEqual(out[1].text(), "ADBE Glow")
        self.assertEqual(out[2].text(), "Stylize")

    def test_match_name_is_readonly(self):
        """matchName 永远不可改写 —— 改了 AE 报 "MatchName is not UTF8"。"""
        data = make_item("ANMe", b"ADBE Glow")
        s = parse_pipl(data)[0]
        self.assertFalse(s.writable())

    def test_name_is_writable(self):
        s = parse_pipl(make_item("eman", b"Glow"))[0]
        self.assertTrue(s.writable())

    def test_unknown_attr_ignored(self):
        data = make_item("zzzz", b"whatever") + make_item("eman", b"Real")
        out = parse_pipl(data)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].text(), "Real")

    def test_truncated_item_ignored(self):
        full = make_item("eman", b"Glow")
        out = parse_pipl(full[: len(full) - 3])   # 砍掉尾部
        self.assertEqual(out, [])

    def test_absurd_length_ignored(self):
        bad = b"MIB8eman" + b"\x00\x00\x00\x00" + (999999).to_bytes(4, "little") + b"\x04Glow"
        self.assertEqual(parse_pipl(bad), [])

    def test_multiple_items(self):
        data = b"".join(make_item("eman", ("Effect%d" % i).encode()) for i in range(5))
        out = parse_pipl(data)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[2].text(), "Effect2")

    def test_offsets_point_at_string(self):
        data = b"HEAD" + make_item("eman", b"Glow")
        s = parse_pipl(data)[0]
        self.assertEqual(data[s.offset:s.offset + s.length], b"Glow")

    def test_gbk_fallback(self):
        raw = "发光".encode("gbk")
        data = make_item("eman", raw)
        s = parse_pipl(data)[0]
        self.assertEqual(s.text(), "发光")

    def test_gbk_bytes_that_are_also_valid_utf8(self):
        """★ 回归：GBK 字节恰好也是合法 UTF-8 时不能解成乱码。

        '投影' 的 GBK 是 cd b6 d3 b0，而 cd b6 / d3 b0 各自都是
        合法的 UTF-8 双字节序列 —— 先试 UTF-8 会"成功"，解出
        'ͶӰ' 而不是报错，于是永远走不到 GBK 那一轮。

        实测踩坑的文件：
            S_DropShadow.aex / BCCDropShadow.aex
            BCCTwister.aex  / BCCTwisterPrTr.aex
        """
        raw = "投影".encode("gbk")
        # 先确认这串字节确实是合法 UTF-8（否则本测试就没意义了）
        raw.decode("utf-8")
        data = make_item("eman", raw)
        s = parse_pipl(data)[0]
        self.assertEqual(s.text(), "投影")

    def test_utf8_hint_still_honored(self):
        """显式传 encoding='utf-8' 时仍按 UTF-8 解。"""
        raw = "投影".encode("utf-8")
        data = make_item("eman", raw)
        s = parse_pipl(data)[0]
        self.assertEqual(s.text("utf-8"), "投影")


class TestReadPlugin(unittest.TestCase):
    def test_missing_file(self):
        info = read_plugin(r"C:\definitely\not\here.aex")
        self.assertTrue(info.error)
        self.assertEqual(info.name_origin, "none")

    def test_filename_fallback_not_editable(self):
        import os
        import tempfile

        d = tempfile.mkdtemp()
        p = os.path.join(d, "MyEffect.aex")
        with open(p, "wb") as f:
            f.write(b"no pipl here")
        info = read_plugin(p)
        self.assertEqual(info.display_name, "MyEffect")
        self.assertEqual(info.name_origin, "filename")
        self.assertFalse(info.name_editable)   # 文件名兜底的不算可改写

    def test_pipl_editable(self):
        import os
        import tempfile

        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.aex")
        with open(p, "wb") as f:
            f.write(make_item("eman", b"Glow") + make_item("ANMe", b"ADBE Glow"))
        info = read_plugin(p)
        self.assertEqual(info.display_name, "Glow")
        self.assertEqual(info.match_name, "ADBE Glow")
        self.assertTrue(info.name_editable)
        self.assertEqual(info.name_origin, "pipl")

    def test_builtin_detection_by_path(self):
        """真正在 Plug-ins\\Effects\\ 下的才算 Adobe 内置。"""
        info = PluginInfo(path=r"C:\AE\Support Files\Plug-ins\Effects\y.aex", file_name="y.aex")
        self.assertTrue(info.is_builtin_adobe)

    def test_adbe_prefix_not_builtin(self):
        """★ 2026-10-22 修正：matchName 带 ADBE 前缀【不再】算内置。

        实测有第三方（DLSS / DepthScanner / GoodbyeGreenscreen）的包装器
        把 matchName 写成 "ADBE xxx"，旧规则把它们误判成内置、界面锁死
        不能汉化。真正的内置效果（3DGlasses.aex 等 228 个）matchName
        其实是【空串】，所以这条规则本来就不可靠。
        """
        info = PluginInfo(path=r"C:\MediaCore\DLSS_Neural_Video.aex",
                          file_name="DLSS_Neural_Video.aex",
                          match_name="ADBE DLSS_Neural_Video")
        self.assertFalse(info.is_builtin_adobe)

    def test_effects_subdir_not_builtin(self):
        """Effects 子目录里的是第三方（CycoreFX / Mocha / Keylight）。"""
        info = PluginInfo(
            path=r"C:\AE\Support Files\Plug-ins\Effects\CycoreFXHD\Hair.aex",
            file_name="Hair.aex", match_name="CC Hair")
        self.assertFalse(info.is_builtin_adobe)

    def test_third_party_not_builtin(self):
        info = PluginInfo(path=r"C:\MediaCore\Sapphire\x.aex", file_name="x.aex",
                          match_name="S_Glow")
        self.assertFalse(info.is_builtin_adobe)


if __name__ == "__main__":
    unittest.main(verbosity=2)
