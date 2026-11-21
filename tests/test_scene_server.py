"""本地场景渲染服务（core/scene_server.py）的回归测试。

重点：**只服务这一张壁纸目录**、token 不对就 404、目录越界挡住。
它监听 127.0.0.1 随机端口，测试全在临时目录里跑。
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from core import scene_server


def _get(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""


class SceneServerTest(unittest.TestCase):
    def setUp(self):
        self.assets = scene_server.asset_dir()
        self.assertTrue(os.path.isfile(os.path.join(self.assets, "index.html")),
                        "渲染页必须随仓库发布：app/assets/webwallgl/index.html")
        self.assertTrue(os.path.isfile(os.path.join(self.assets, "webwallgl.global.min.js")),
                        "渲染核心必须随仓库发布（MIT，见同目录 LICENSE）")

        self.tmp = tempfile.mkdtemp(prefix="aeloc_scene_")
        self.wp = os.path.join(self.tmp, "wp")
        os.makedirs(self.wp)
        with open(os.path.join(self.wp, "scene.pkg"), "wb") as f:
            f.write(b"PKGV0024" + b"\x00" * 64)
        with open(os.path.join(self.wp, "project.json"), "w", encoding="utf-8") as f:
            f.write('{"title":"t","type":"scene","file":"scene.pkg"}')
        # 壁纸目录之外的文件：越界必须挡住
        with open(os.path.join(self.tmp, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("secret")

        self.srv = scene_server.SceneServer(self.assets)
        self.url = self.srv.start(self.wp)

    def tearDown(self):
        self.srv.stop()

    def test_page_and_assets_served(self):
        code, body, ctype = _get(self.url)
        self.assertEqual(code, 200)
        self.assertIn(b"__aeloc", body)
        self.assertIn("text/html", ctype)

        code, body, ctype = _get(self.url + "webwallgl.global.min.js")
        self.assertEqual(code, 200)
        self.assertGreater(len(body), 100000)
        self.assertIn("javascript", ctype)

    def test_wallpaper_files_served(self):
        code, body, _ = _get(self.url + "wp/scene.pkg")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"PKGV0024"))
        code, body, _ = _get(self.url + "wp/project.json")
        self.assertEqual(code, 200)
        self.assertIn(b'"scene"', body)

    def test_path_traversal_blocked(self):
        code, _, _ = _get(self.url + "wp/../secret.txt")
        self.assertEqual(code, 404)
        code, _, _ = _get(self.url + "wp/%2e%2e/secret.txt")
        self.assertEqual(code, 404)
        code, _, _ = _get(self.url + "wp/..%2fsecret.txt")
        self.assertEqual(code, 404)

    def test_wrong_token_404(self):
        bad = self.url.replace("/t/", "/t/deadbeef")
        code, _, _ = _get(bad)
        self.assertEqual(code, 404)

    def test_unknown_path_404(self):
        code, _, _ = _get(self.url + "nope.txt")
        self.assertEqual(code, 404)

    def test_restart_same_dir_reuses_url(self):
        same = self.srv.start(self.wp)
        self.assertEqual(same, self.url)

    def test_switch_wallpaper_keeps_url(self):
        """换壁纸**不换端口/token**（2026-11-18 架构优化）：渲染页 origin 不变，
        QML 才能在同一个页面里 inst.load() 热切场景，而不是整页重载。"""
        other = os.path.join(self.tmp, "wp2")
        os.makedirs(other, exist_ok=True)
        with open(os.path.join(other, "scene.pkg"), "wb") as f:
            f.write(b"PKGV0024X")
        same_url = self.srv.start(other)
        self.assertEqual(same_url, self.url, "换壁纸不该换 URL")
        code, body, _ = _get(self.url + "wp/scene.pkg")
        self.assertEqual(code, 200)
        self.assertTrue(body.endswith(b"X"), "同一个 URL 必须立刻服务新壁纸")

    def test_stop_invalidates_url(self):
        self.srv.stop()
        try:
            code, _, _ = _get(self.url + "wp/scene.pkg")
        except urllib.error.URLError:
            code = -1
        self.assertTrue(code in (-1, 404))

if __name__ == "__main__":
    unittest.main()
