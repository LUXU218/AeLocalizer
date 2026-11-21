"""更新器测试：版本比对 / 清单拉取 / 校验下载 —— 全部走本地 HTTP，不碰外网。

★ 沙盒纪律（2026-10-23 事故）：涉及 AppBackend 的测试一律把
  backend.DATA_ROOT 指到临时目录，绝不写真实 %LOCALAPPDATA%。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.updater import check, compare_versions, download, fetch_manifest
from core import updater
from core.version import APP_VERSION


# ---------------------------------------------------------------- 纯逻辑

class TestCompareVersions(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(compare_versions("1.0.0", "1.0.1"), -1)
        self.assertEqual(compare_versions("1.0.1", "1.0.0"), 1)

    def test_two_digit_segment(self):
        # ★ 1.10 > 1.9 —— 字符串比会错，数字比才对
        self.assertEqual(compare_versions("1.10", "1.9"), 1)
        self.assertEqual(compare_versions("1.9", "1.10"), -1)

    def test_v_prefix(self):
        self.assertEqual(compare_versions("v1.2.0", "1.2.0"), 0)
        self.assertEqual(compare_versions("V2.0", "1.9.9"), 1)

    def test_non_numeric_suffix(self):
        self.assertEqual(compare_versions("1.0.0-beta", "1.0.0"), 0)
        self.assertEqual(compare_versions("1.0", "1.0.0"), 0)

    def test_shorter_pads_zero(self):
        self.assertEqual(compare_versions("1.0", "1.0.0.0"), 0)
        self.assertEqual(compare_versions("2.0", "1.9.9"), 1)


class TestGitHubRelease(unittest.TestCase):
    def setUp(self):
        self.repo = "Example/AeLocalizer"
        self.digest = "a" * 64
        self.url = f"https://github.com/{self.repo}/releases/download/v1.0.13/AeLocalizer-1.0.13-win64.zip"
        self.asset = {
            "name": "AeLocalizer-1.0.13-win64.zip",
            "browser_download_url": self.url,
            "digest": f"sha256:{self.digest}",
            "state": "uploaded",
        }
        self.release = {"tag_name": "v1.0.13", "assets": [self.asset]}

    def fetch(self, current="1.0.12"):
        with mock.patch.object(updater, "_read_bytes", return_value=json.dumps(self.release).encode()) as read:
            result = updater.check_github_release(self.repo, current)
        read.assert_called_once_with(
            f"https://api.github.com/repos/{self.repo}/releases/latest", 8.0)
        return result

    def test_repository_accepts_slug_and_github_url(self):
        self.assertEqual(updater.normalize_github_repository(self.repo), self.repo)
        self.assertEqual(updater.normalize_github_repository(
            f" https://github.com/{self.repo}/releases/latest "), self.repo)
        for value in ("https://evil.example/Example/AeLocalizer",
                      "https://github.com@example.net/Example/AeLocalizer",
                      "https://github.com/Example/AeLocalizer/version.json", "Owner/.."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                updater.normalize_github_repository(value)

    def test_picks_versioned_windows_asset_with_digest(self):
        self.release["assets"].insert(0, {"name": "Source code.zip"})
        result = self.fetch()
        self.assertTrue(result["has_update"])
        self.assertEqual(result["latest"], "1.0.13")
        self.assertEqual(result["download_url"], self.url)
        self.assertEqual(result["sha256"], self.digest)

    def test_current_release_needs_no_asset(self):
        self.release["assets"] = []
        self.assertFalse(self.fetch("1.0.13")["has_update"])

    def test_missing_or_unverified_asset_cannot_be_downloaded(self):
        self.release["assets"] = []
        with self.assertRaisesRegex(ValueError, "Windows x64"):
            self.fetch()
        self.release["assets"] = [self.asset]
        self.asset["digest"] = None
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.fetch()
        self.asset["digest"] = f"sha256:{self.digest}"
        self.asset["browser_download_url"] = self.url.replace(self.repo, "Other/Repo")
        with self.assertRaisesRegex(ValueError, "下载地址"):
            self.fetch()

    def test_preview_and_non_version_tags_rejected(self):
        self.release["prerelease"] = True
        with self.assertRaisesRegex(ValueError, "正式版本"):
            self.fetch()
        self.release["prerelease"] = False
        self.release["tag_name"] = "v1.0.13-beta"
        with self.assertRaisesRegex(ValueError, "版本标签"):
            self.fetch()

    def test_no_visible_release_has_actionable_message(self):
        with mock.patch.object(updater, "_read_bytes",
                               side_effect=HTTPError("", 404, "Not Found", {}, None)):
            with self.assertRaisesRegex(ValueError, "公开的正式版本"):
                updater.check_github_release(self.repo, "1.0.12")


class TestCheck(unittest.TestCase):
    def test_has_update(self):
        res = check("1.0.0", {"version": "1.1.0", "url": "http://x/pkg.zip",
                              "notes": "修复若干", "sha256": "ABCD"})
        self.assertTrue(res["has_update"])
        self.assertEqual(res["latest"], "1.1.0")
        self.assertEqual(res["download_url"], "http://x/pkg.zip")
        self.assertEqual(res["notes"], "修复若干")
        self.assertEqual(res["sha256"], "abcd")  # 统一小写

    def test_no_update(self):
        res = check("1.1.0", {"version": "1.1.0"})
        self.assertFalse(res["has_update"])
        self.assertEqual(res["download_url"], "")

    def test_old_manifest(self):
        res = check("2.0.0", {"version": "1.0.0"})
        self.assertFalse(res["has_update"])

    def test_missing_version_raises(self):
        with self.assertRaises(ValueError):
            check("1.0.0", {"url": "http://x"})
        with self.assertRaises(ValueError):
            check("1.0.0", {})


# ---------------------------------------------------------------- 本地 HTTP

_PKG_BYTES = b"PK\x03\x04 fake aeloc update package \x00\x01\x02"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/version.json":
            body = json.dumps({
                "version": "99.0.0",
                "url": self.server.pkg_url,
                "notes": "测试更新说明",
                "sha256": hashlib.sha256(_PKG_BYTES).hexdigest(),
            }).encode("utf-8")
            self._send(200, body, "application/json")
        elif self.path == "/old.json":
            self._send(200, json.dumps({"version": "0.0.1"}).encode("utf-8"),
                       "application/json")
        elif self.path == "/noversion.json":
            self._send(200, json.dumps({"url": "http://x"}).encode("utf-8"),
                       "application/json")
        elif self.path == "/bad.json":
            self._send(200, b"not json at all", "application/json")
        elif self.path == "/pkg.zip":
            self._send(200, _PKG_BYTES, "application/octet-stream")
        elif self.path == "/pkg-corrupt.zip":
            self._send(200, b"corrupted", "application/octet-stream")
        else:
            self._send(404, b"nope", "text/plain")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音测试输出
        pass


class TestManifestAndDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = cls.httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        cls.httpd.pkg_url = base + "/pkg.zip"
        cls.base = base
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_fetch_manifest_ok(self):
        m = fetch_manifest(self.base + "/version.json", direct=True)
        self.assertEqual(m["version"], "99.0.0")
        self.assertEqual(m["sha256"], hashlib.sha256(_PKG_BYTES).hexdigest())

    def test_fetch_manifest_missing_version(self):
        with self.assertRaises(ValueError):
            fetch_manifest(self.base + "/noversion.json", direct=True)

    def test_fetch_manifest_bad_json(self):
        with self.assertRaises(Exception):
            fetch_manifest(self.base + "/bad.json", direct=True)

    def test_fetch_manifest_404(self):
        with self.assertRaises(Exception):
            fetch_manifest(self.base + "/none.json", direct=True)

    def test_check_with_local_manifest(self):
        m = fetch_manifest(self.base + "/version.json", direct=True)
        res = check(APP_VERSION, m)
        self.assertTrue(res["has_update"])

    def test_download_ok_with_sha(self):
        dest = tempfile.mkdtemp(prefix="aeloc_dl_")
        seen = []
        path = download(self.base + "/pkg.zip", dest,
                        sha256=hashlib.sha256(_PKG_BYTES).hexdigest(),
                        progress=lambda t, p: seen.append(p), direct=True)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), _PKG_BYTES)
        self.assertEqual(os.path.basename(path), "pkg.zip")
        # 有 Content-Length，进度应给过 0-100 的百分比
        self.assertTrue(any(0 <= p <= 100 for p in seen))

    def test_download_sha_mismatch(self):
        dest = tempfile.mkdtemp(prefix="aeloc_dl_")
        with self.assertRaises(ValueError):
            download(self.base + "/pkg-corrupt.zip", dest,
                     sha256=hashlib.sha256(_PKG_BYTES).hexdigest(), direct=True)
        # 坏包绝不能以正式文件名留下
        self.assertEqual([n for n in os.listdir(dest) if not n.endswith(".part")], [])

    def test_download_no_sha_still_works(self):
        dest = tempfile.mkdtemp(prefix="aeloc_dl_")
        path = download(self.base + "/pkg.zip", dest, sha256="", direct=True)
        self.assertTrue(os.path.isfile(path))


# ---------------------------------------------------------------- backend 集成

try:
    from PySide6.QtGui import QGuiApplication

    _HAS_QT = True
except Exception:  # pragma: no cover
    _HAS_QT = False


@unittest.skipUnless(_HAS_QT, "PySide6 不可用")
class TestSettingsUiBackend(unittest.TestCase):
    """主题/更新源在 settings.json 的持久化 + 手动检查的同步分支。"""

    @classmethod
    def setUpClass(cls):
        QGuiApplication.instance() or QGuiApplication([])

        import app.backend as backend_mod
        cls.backend_mod = backend_mod
        cls._tmpdir = tempfile.mkdtemp(prefix="aeloc_test_setui_")
        cls._old_root = backend_mod.DATA_ROOT
        backend_mod.DATA_ROOT = cls._tmpdir
        cls.b = backend_mod.AppBackend()

    @classmethod
    def tearDownClass(cls):
        import shutil

        cls.backend_mod.DATA_ROOT = cls._old_root
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_default_theme_light(self):
        # 默认 light = 历史外观，升级不改观感
        self.assertEqual(self.b.uiTheme, "light")

    def test_set_theme_persists(self):
        from core import settings as _settings

        self.b.setUiTheme("dark")
        self.assertEqual(self.b.uiTheme, "dark")
        obj = _settings.load(self._tmpdir)
        self.assertEqual(obj.get("ui_theme"), "dark")
        # 换回 light 再存，键跟着变
        self.b.setUiTheme("light")
        self.assertEqual(_settings.load(self._tmpdir).get("ui_theme"), "light")

    def test_invalid_theme_ignored(self):
        before = self.b.uiTheme
        self.b.setUiTheme("blue")
        self.assertEqual(self.b.uiTheme, before)

    def test_theme_restored_on_new_backend(self):
        self.b.setUiTheme("system")
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.uiTheme, "system")

    def test_github_repository_persists(self):
        from core import settings as _settings

        self.assertTrue(self.b.setUpdateRepository("https://github.com/Example/AeLocalizer"))
        self.assertEqual(self.b.updateRepository, "Example/AeLocalizer")
        self.assertEqual(_settings.load(self._tmpdir).get("update_repository"), "Example/AeLocalizer")
        self.assertEqual(self.backend_mod.AppBackend().updateRepository, "Example/AeLocalizer")

    def test_legacy_manifest_is_not_a_github_source(self):
        from core import settings as _settings

        _settings.save(self._tmpdir, {"update_url": "https://example.net/version.json",
                                     "ui_theme": "dark"})
        b = self.backend_mod.AppBackend()
        self.assertEqual(b.updateRepository, "LUXU218/AeLocalizer")
        self.assertEqual(b.uiTheme, "dark")

    def test_changing_repository_invalidates_download_and_keeps_previous_on_invalid_input(self):
        self.b.setUpdateRepository("Example/AeLocalizer")
        self.b._updateState = "downloaded"
        self.b._updateFile = "old.zip"
        self.b._updateDownloadUrl = "https://example.com/old.zip"
        self.assertFalse(self.b.setUpdateRepository("https://example.com/Other/AeLocalizer"))
        self.assertEqual(self.b.updateRepository, "Example/AeLocalizer")
        self.assertEqual(self.b.updateState, "downloaded")
        self.assertTrue(self.b.setUpdateRepository("Other/AeLocalizer"))
        self.assertEqual(self.b.updateRepository, "Other/AeLocalizer")
        self.assertEqual(self.b.updateState, "")
        self.assertEqual(self.b.updateFile, "")
        self.assertEqual(self.b._updateDownloadUrl, "")

    def test_blank_update_repository_restores_official_release_source(self):
        from core import settings as _settings

        self.b.setUpdateRepository("Example/AeLocalizer")
        self.assertTrue(self.b.setUpdateRepository(""))
        self.assertEqual(self.b.updateRepository, "LUXU218/AeLocalizer")
        _settings.save(self._tmpdir, {"update_repository": ""})
        self.assertEqual(self.backend_mod.AppBackend().updateRepository, "LUXU218/AeLocalizer")

    def test_app_version_property(self):
        self.assertEqual(self.b.appVersion, APP_VERSION)

    def test_install_stops_guard_before_launching_replacement_script(self):
        from app.backend_parts import settings_ui
        from core import engine

        backend = self.backend_mod.AppBackend()
        backend._updateState = "downloaded"
        backend._updateFile = os.path.join(self._tmpdir, "update.zip")
        steps = []

        def run_worker(job, done, _fail):
            done(job())

        def stop_guard():
            steps.append("stop")
            return True, "stopped"

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(backend, "_spawn_update_worker", side_effect=run_worker), \
             mock.patch.object(engine, "stop_engine", side_effect=stop_guard), \
             mock.patch.object(settings_ui._updater, "can_self_update", return_value=True), \
             mock.patch.object(settings_ui.subprocess, "Popen",
                               side_effect=lambda *_a, **_kw: steps.append("launch")) as launch, \
             mock.patch.object(settings_ui.QTimer, "singleShot"):
            backend.installUpdate()

        self.assertEqual(steps, ["stop", "launch"])
        self.assertEqual(backend.updateState, "installing")
        self.assertEqual(launch.call_args.kwargs["creationflags"],
                         getattr(settings_ui.subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(settings_ui.subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def test_install_keeps_app_open_when_guard_cannot_stop(self):
        from app.backend_parts import settings_ui
        from core import engine

        backend = self.backend_mod.AppBackend()
        backend._updateState = "downloaded"
        backend._updateFile = os.path.join(self._tmpdir, "update.zip")

        def run_worker(job, done, _fail):
            done(job())

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(backend, "_spawn_update_worker", side_effect=run_worker), \
             mock.patch.object(engine, "stop_engine", return_value=(False, "still running")), \
             mock.patch.object(settings_ui._updater, "can_self_update", return_value=True), \
             mock.patch.object(settings_ui.subprocess, "Popen") as launch, \
             mock.patch.object(settings_ui.QTimer, "singleShot") as quit_timer:
            backend.installUpdate()

        launch.assert_not_called()
        quit_timer.assert_not_called()
        self.assertEqual(backend.updateState, "downloaded")
        self.assertIn("still running", backend.updateText)
        self.assertFalse(backend._engineBusy)

    # ---- 背景图 ----

    def _make_png(self, path):
        # 1x1 PNG（不需要真是合法图片：backend 只看扩展名/大小，不做像素解码）
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    def test_bg_default_empty(self):
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.bgImage, "")
        # bgDim 只在**有壁纸时**当纱罩用；没壁纸时它不参与绘制。
        # 默认值已改成玻璃档（0.22），所以这里跟的是新默认而不是历史的 0.85。
        self.assertAlmostEqual(b2.bgDim, 0.22)

    def test_set_bg_copies_and_persists(self):
        import urllib.request

        from core import settings as _settings

        src = os.path.join(self._tmpdir, "mypic.png")
        self._make_png(src)
        url = urllib.request.urlparse("file:///" + src.replace("\\", "/")).geturl()
        self.b.setBgImage(url)
        self.assertTrue(self.b.bgImage.startswith("file:///"), self.b.bgImage)
        # 复制进了沙盒数据目录
        bg_dir = os.path.join(self._tmpdir, "ui_bg")
        self.assertTrue(os.path.isfile(os.path.join(bg_dir, "bg.png")))
        # 落盘 + 新 backend 能恢复
        self.assertEqual(_settings.load(self._tmpdir).get("bg_image"),
                         os.path.join(bg_dir, "bg.png"))
        b2 = self.backend_mod.AppBackend()
        self.assertTrue(b2.bgImage.startswith("file:///"))

    def test_set_bg_rejects_bad_ext(self):
        src = os.path.join(self._tmpdir, "evil.exe")
        with open(src, "wb") as f:
            f.write(b"MZ")
        before = self.b.bgImage
        self.b.setBgImage(src)
        self.assertEqual(self.b.bgImage, before)

    def test_clear_bg(self):
        from core import settings as _settings

        src = os.path.join(self._tmpdir, "c.png")
        self._make_png(src)
        self.b.setBgImage(src)
        self.b.clearBgImage()
        self.assertEqual(self.b.bgImage, "")
        self.assertEqual(_settings.load(self._tmpdir).get("bg_image"), "")

    def test_bg_dim_clamped_and_persisted(self):
        from core import settings as _settings

        self.b.setBgDim(0.5)
        self.assertAlmostEqual(self.b.bgDim, 0.5)
        self.assertAlmostEqual(_settings.load(self._tmpdir).get("bg_dim"), 0.5)
        self.b.setBgDim(9.9)   # 超范围 → 夹到上限
        self.assertAlmostEqual(self.b.bgDim, 0.95)
        self.b.setBgDim(-3)
        self.assertAlmostEqual(self.b.bgDim, 0.05)

    def test_no_auto_network_on_init(self):
        # ★ 纯手动约定：新建 backend 后绝不能处于 checking/downloading
        b2 = self.backend_mod.AppBackend()
        self.assertNotIn(b2.updateState, ("checking", "downloading"))

    # ---- 个性化（2026-11-16 设置页改版）----

    def test_appearance_defaults(self):
        """默认值必须等于历史观感 —— 老用户升级后不该看到界面变了。"""
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.accentKey, "blue")
        self.assertEqual(b2.density, "normal")
        self.assertEqual(b2.fontScale, "normal")
        self.assertFalse(b2.reduceMotion)
        # plate 同理：没有壁纸时 plateAlpha 恒为 1，这个默认值只影响有壁纸的场合。
        # 默认改成玻璃档 0.60。
        self.assertAlmostEqual(b2.plate, 0.60)
        self.assertTrue(b2.autoScan)

    def test_accent_persists(self):
        from core import settings as _settings

        self.b.setAccentKey("violet")
        self.assertEqual(self.b.accentKey, "violet")
        self.assertEqual(_settings.load(self._tmpdir).get("accent_key"), "violet")
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.accentKey, "violet")
        self.b.setAccentKey("blue")

    def test_invalid_accent_ignored(self):
        """非法档位必须被忽略：Theme 拿到不存在的键会回退，但状态不该被污染。"""
        before = self.b.accentKey
        self.b.setAccentKey("chartreuse")
        self.assertEqual(self.b.accentKey, before)

    def test_density_and_font_scale_persist(self):
        from core import settings as _settings

        self.b.setDensity("compact")
        self.b.setFontScale("large")
        obj = _settings.load(self._tmpdir)
        self.assertEqual(obj.get("density"), "compact")
        self.assertEqual(obj.get("font_scale"), "large")
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.density, "compact")
        self.assertEqual(b2.fontScale, "large")
        self.b.setDensity("normal")
        self.b.setFontScale("normal")

    def test_invalid_density_and_font_ignored(self):
        before_d, before_f = self.b.density, self.b.fontScale
        self.b.setDensity("wide")
        self.b.setFontScale("huge")
        self.assertEqual(self.b.density, before_d)
        self.assertEqual(self.b.fontScale, before_f)

    def test_reduce_motion_persists(self):
        from core import settings as _settings

        self.b.setReduceMotion(True)
        self.assertTrue(self.b.reduceMotion)
        self.assertTrue(_settings.load(self._tmpdir).get("reduce_motion"))
        self.b.setReduceMotion(False)
        self.assertFalse(self.b.reduceMotion)

    def test_plate_clamped_and_persisted(self):
        from core import settings as _settings

        self.b.setPlate(0.4)
        self.assertAlmostEqual(self.b.plate, 0.4)
        self.assertAlmostEqual(_settings.load(self._tmpdir).get("plate"), 0.4)
        self.b.setPlate(3.0)      # 超上限 → 夹到 1
        self.assertAlmostEqual(self.b.plate, 1.0)
        self.b.setPlate(-2.0)     # 超下限 → 夹到 0
        self.assertAlmostEqual(self.b.plate, 0.0)
        self.b.setPlate(0.85)

    def test_reset_appearance(self):
        """恢复默认外观不该动主题与背景图 —— 那是两回事。"""
        self.b.setUiTheme("dark")
        self.b.setAccentKey("rose")
        self.b.setDensity("relaxed")
        self.b.setFontScale("small")
        self.b.setReduceMotion(True)
        self.b.setPlate(0.3)
        self.b.resetAppearance()
        self.assertEqual(self.b.accentKey, "blue")
        self.assertEqual(self.b.density, "normal")
        self.assertEqual(self.b.fontScale, "normal")
        self.assertFalse(self.b.reduceMotion)
        self.assertAlmostEqual(self.b.plate, 0.85)
        self.assertEqual(self.b.uiTheme, "dark")     # 主题保留
        self.b.setUiTheme("light")

    def test_auto_scan_persists(self):
        from core import settings as _settings

        self.b.setAutoScan(False)
        self.assertFalse(self.b.autoScan)
        self.assertFalse(_settings.load(self._tmpdir).get("auto_scan"))
        self.b.setAutoScan(True)
        self.assertTrue(self.b.autoScan)

    def test_legacy_settings_file_still_loads(self):
        """老 settings.json（没有个性化键）必须照旧能起，且用默认观感。"""
        from core import settings as _settings

        _settings.save(self._tmpdir, {"ui_theme": "light", "bg_image": "", "bg_dim": 0.85})
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.accentKey, "blue")
        self.assertEqual(b2.density, "normal")
        self.assertTrue(b2.autoScan)

    def test_ai_cache_clear(self):
        import tempfile as _tf

        d = _tf.mkdtemp(prefix="aeloc_cache_")
        try:
            from core.ai.pipeline import Cache

            path = os.path.join(d, "ai-cache.json")
            self.b._aiCache = Cache(path)
            self.b._aiCache.put("k1", "译文")
            self.b._aiCache.flush()
            self.assertEqual(self.b.aiCacheEntries, 1)
            self.b.clearAiCache()
            self.assertEqual(self.b.aiCacheEntries, 0)
            self.assertFalse(os.path.exists(path))
        finally:
            import shutil

            shutil.rmtree(d, ignore_errors=True)

    def test_diagnostic_text_contains_version(self):
        text = self.b.diagnosticText()
        self.assertIn(APP_VERSION, text)
        self.assertIn("数据目录", text)

    def test_diagnostic_uses_current_engine_status(self):
        from core.engine import EngineStatus

        for running, label in ((True, "运行中"), (False, "未运行")):
            with mock.patch("core.engine.check", return_value=EngineStatus(running=running)):
                self.assertIn(f"引擎: {label}", self.b.diagnosticText().splitlines())

    def test_data_dir_usage_readable(self):
        s = self.b.dataDirUsage()
        self.assertIn("文件", s)

    def test_data_dir_text_matches_root(self):
        self.assertEqual(self.b.dataDirText, self._tmpdir)

    # ---- 满屏磨砂玻璃的画面调节（2026-11-16）----

    def test_bg_adjust_defaults_neutral(self):
        """默认必须是「什么都不做」：没壁纸时观感不能被这层改掉。

        ★ 这个类共用同一个数据目录，前面几个用例会把值改掉并落盘；
          所以要真测「默认值」必须先把设置文件清干净再构造。
        """
        from core import settings as _settings

        _settings.save(self._tmpdir, {})
        b2 = self.backend_mod.AppBackend()
        # 默认即「磨砂玻璃」档 —— 加了壁纸第一次看到的就是最终形态
        self.assertEqual(b2.bgBlur, 24)
        self.assertEqual(b2.bgBrightness, 94)
        self.assertEqual(b2.bgSaturation, 92)
        self.assertEqual(b2.bgContrast, 100)
        self.assertEqual(b2.bgFit, "cover")

    def test_bg_adjust_clamped(self):
        self.b.setBgBlur(999)
        self.assertEqual(self.b.bgBlur, 60)
        self.b.setBgBlur(-5)
        self.assertEqual(self.b.bgBlur, 0)
        self.b.setBgBrightness(1)
        self.assertEqual(self.b.bgBrightness, 50)
        self.b.setBgSaturation(999)
        self.assertEqual(self.b.bgSaturation, 200)
        self.b.setBgContrast(999)
        self.assertEqual(self.b.bgContrast, 160)

    def test_bg_adjust_persists(self):
        from core import settings as _settings

        self.b.setBgBlur(26)
        self.b.setBgBrightness(93)
        self.b.setBgSaturation(87)
        self.b.setBgContrast(104)
        obj = _settings.load(self._tmpdir)
        self.assertEqual(obj.get("bg_blur"), 26)
        self.assertEqual(obj.get("bg_brightness"), 93)
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.bgBlur, 26)
        self.assertEqual(b2.bgBrightness, 93)

    def test_bg_fit_validation(self):
        self.b.setBgFit("contain")
        self.assertEqual(self.b.bgFit, "contain")
        self.b.setBgFit("weird")
        self.assertEqual(self.b.bgFit, "contain")   # 非法值忽略
        self.b.setBgFit("cover")

    def test_bg_preset_applies_all(self):
        self.b.applyBgPreset("frosted")
        self.assertEqual(self.b.bgBlur, 34)
        self.assertEqual(self.b.bgBrightness, 88)
        self.assertEqual(self.b.bgSaturation, 88)
        self.assertAlmostEqual(self.b.bgDim, 0.26)
        self.assertAlmostEqual(self.b.plate, 0.88)
        self.assertEqual(self.b.currentBgPreset, "frosted")

    def test_bg_preset_unknown_ignored(self):
        before = self.b.bgBlur
        self.b.applyBgPreset("nonsense")
        self.assertEqual(self.b.bgBlur, before)

    def test_bg_preset_persists(self):
        from core import settings as _settings

        self.b.applyBgPreset("reading")
        obj = _settings.load(self._tmpdir)
        self.assertEqual(obj.get("bg_blur"), 24)
        self.assertEqual(obj.get("bg_brightness"), 80)

    def test_current_preset_reports_custom(self):
        self.b.setBgBlur(7)          # 不属于任何预设
        self.assertEqual(self.b.currentBgPreset, "")

    # ---- 字体与文字颜色 ----

    def test_text_color_validation(self):
        self.b.setTextColor("#ffffff")
        self.assertEqual(self.b.textColor, "#ffffff")
        self.b.setTextColor("#FFF")          # 三位简写也认
        self.assertEqual(self.b.textColor, "#fff")
        self.b.setTextColor("not-a-color")   # 非法忽略
        self.assertEqual(self.b.textColor, "#fff")
        self.b.setTextColor("")              # 空串 = 跟随主题
        self.assertEqual(self.b.textColor, "")

    def test_text_color_persists(self):
        from core import settings as _settings

        self.b.setTextColor("#e8ecf5")
        self.assertEqual(_settings.load(self._tmpdir).get("text_color"), "#e8ecf5")
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.textColor, "#e8ecf5")
        self.b.setTextColor("")

    def test_ui_font_persists(self):
        from core import settings as _settings

        self.b.setUiFont("思源黑体")
        self.assertEqual(self.b.uiFont, "思源黑体")
        self.assertEqual(_settings.load(self._tmpdir).get("ui_font"), "思源黑体")
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.uiFont, "思源黑体")
        self.b.setUiFont("")

    def test_legacy_bg_config_gets_glass_migration(self):
        """老配置有背景但没有画面调节参数 → 补一次玻璃档，否则字会糊在壁纸上。"""
        from core import settings as _settings

        _settings.save(self._tmpdir, {
            "ui_theme": "light",
            "bg_image": "",       # 路径不存在 → 加载时被清空，用视频标记代替
            "bg_video": __file__,  # 任意存在的文件，模拟已有背景
            "bg_dim": 0.2,
        })
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.bgBlur, 24, "老配置应被补上磨砂默认值")

    # ---- 每张壁纸自己的画面参数（2026-11-16 追加）----

    def test_we_tune_defaults_to_current_and_clamps(self):
        """没调过 = 跟当前全局值一致；越界一律夹回，未知键忽略。"""
        from core import settings as _settings

        _settings.save(self._tmpdir, {})
        b2 = self.backend_mod.AppBackend()
        tune = dict(b2.weTune("wid1"))
        self.assertEqual(tune["blur"], b2.bgBlur, "没调过的壁纸应回落到当前全局值")
        self.assertEqual(dict(self.b.setWeTune("wid1", "blur", 999))["blur"], 60)
        self.assertEqual(dict(self.b.setWeTune("wid1", "saturation", -5))["saturation"], 0)
        self.assertNotIn("nope", dict(self.b.setWeTune("wid1", "nope", 1)))

    def test_we_tune_persists_per_wallpaper(self):
        """参数按壁纸 id 分开存，重开程序还在 —— 换壁纸再换回来不能丢。"""
        from core import settings as _settings

        self.b.setWeTune("aaa", "blur", 12)
        self.b.setWeTune("bbb", "blur", 34)
        raw = _settings.load(self._tmpdir).get("we_tune") or {}
        self.assertEqual(raw["aaa"]["blur"], 12)
        self.assertEqual(raw["bbb"]["blur"], 34)
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(dict(b2.weTune("aaa"))["blur"], 12)
        self.assertEqual(dict(b2.weTune("bbb"))["blur"], 34)
        self.assertEqual(dict(b2.weTune("ccc"))["blur"], b2.bgBlur)

    # ---- 壁纸自带参数的读取与写回（2026-11-17）----

    def _fake_we(self):
        """造一个假 WE 安装目录 + 假壁纸，返回 (backend, we_dir, cfg_path)。"""
        import json as _json

        from core import wallpaper_engine as we

        we_dir = os.path.join(self._tmpdir, "we")
        wdir = os.path.join(self._tmpdir, "wp", "123")
        os.makedirs(we_dir, exist_ok=True)
        os.makedirs(wdir, exist_ok=True)
        cfg_path = os.path.join(we_dir, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            _json.dump({"4001": {"version": 5, "wproperties": {}, "keepme": "x"},
                        "?installdirectory": we_dir}, f)
        with open(os.path.join(wdir, "project.json"), "w", encoding="utf-8") as f:
            _json.dump({"title": "t", "type": "scene", "file": "scene.pkg",
                        "general": {"properties": {
                            "flag": {"order": 1, "text": "开关", "type": "bool", "value": True},
                            "lvl": {"order": 2, "text": "等级", "type": "slider",
                                    "value": 0.5, "min": 0, "max": 1, "step": 0.1},
                            "mode": {"order": 3, "text": "模式", "type": "combo", "value": "0",
                                     "options": [{"label": "甲", "value": "0"},
                                                 {"label": "乙", "value": "1"}]},
                        }}}, f)
        open(os.path.join(wdir, "scene.pkg"), "wb").close()

        b = self.backend_mod.AppBackend()
        b._weItems = [we.Wallpaper(id="123", title="t", kind="scene",
                                   main="scene.pkg", dir=wdir)]
        b._weScanned = True
        b._weInstall = we_dir
        b._weRunning = lambda: False
        b._weSceneProps.pop("123", None)
        b._wePending.pop("123", None)
        return b, we_dir, cfg_path

    def test_we_props_edit_writes_only_that_branch(self):
        """改属性只动 wproperties.<入口>.Monitor0，config.json 别的键一个不碰。"""
        import json as _json

        b, _we_dir, cfg_path = self._fake_we()
        try:
            props = {p["name"]: p for p in b.weProps("123")}
            self.assertEqual(props["flag"]["effective"], True)
            self.assertFalse(props["flag"]["hasOverride"])
            self.assertTrue(dict(b.setWeProp("123", "flag", False))["ok"])
            self.assertTrue(dict(b.setWeProp("123", "lvl", 5))["ok"])   # 越界 → 夹到 max
            self.assertTrue(dict(b.setWeProp("123", "mode", "1"))["ok"])
            self.assertFalse(dict(b.setWeProp("123", "mode", "9"))["ok"])  # 不在选项里

            with open(cfg_path, encoding="utf-8") as fh:
                after = _json.load(fh)
            self.assertEqual(after["4001"]["keepme"], "x")
            key = os.path.join(b._weItems[0].dir, "scene.pkg").replace("\\", "/")
            mon = after["4001"]["wproperties"][key]["Monitor0"]
            self.assertIs(mon["flag"], False)
            self.assertEqual(mon["lvl"], 1)
            self.assertEqual(mon["mode"], "1")
            self.assertTrue(os.path.isfile(cfg_path + ".aeloc.bak"), "写之前必须先备份")
        finally:
            b.shutdown()

    def test_we_prop_while_engine_running_is_remembered_then_flushed(self):
        """引擎在跑：照写但记 pending；它关掉后（下次扫描时）自动重写一遍。

        ★ 场景来自实测：壁纸引擎几乎总在运行，而它退出时会用内存里的旧值整份
          覆盖 config.json。如果直接拒绝写，用户就是"点了没反应"；
          如果只写不记，用户退出引擎后改动就丢了。所以两件事都做。
        """
        import json as _json

        b, _we_dir, cfg_path = self._fake_we()
        try:
            b._weRunning = lambda: True
            r = dict(b.setWeProp("123", "flag", False))
            self.assertTrue(r["ok"], r)
            self.assertIn("正在运行", r["msg"])
            self.assertEqual(b._wePending["123"]["flag"], False)

            # 模拟：引擎关掉了 → 下一次扫描时冲掉待写队列
            b._weRunning = lambda: False
            b._flushPendingProps()
            self.assertEqual(b._wePending, {})
            with open(cfg_path, encoding="utf-8") as fh:
                after = _json.load(fh)
            key = os.path.join(b._weItems[0].dir, "scene.pkg").replace("\\", "/")
            self.assertIs(after["4001"]["wproperties"][key]["Monitor0"]["flag"], False)
        finally:
            b.shutdown()

    def test_live_scene_properties_survive_wallpaper_engine_overwriting_its_config(self):
        from core import settings
        from core import wallpaper_engine as we

        b, _, cfg_path = self._fake_we()
        _settings_before = settings.load(self._tmpdir)
        original = Path(cfg_path).read_bytes()
        b._bgWeId = b._bgSceneWeId = "123"
        b._bgSceneDir = b._weItems[0].dir
        b._bgSceneUrl = "http://127.0.0.1/example/index.html"
        b._weRunning = lambda: True
        try:
            self.assertTrue(b.setWeProp("123", "flag", False)["ok"])
            self.assertTrue(b.setWeProp("123", "lvl", 0.8)["ok"])
            Path(cfg_path).write_bytes(original)
            with mock.patch.dict(os.environ, {"AELOC_WEBENGINE": "0"}), \
                    mock.patch.object(we, "find_install", return_value=b._weInstall):
                restarted = self.backend_mod.AppBackend()
            try:
                restarted._weItems = b._weItems
                restarted._weScanned = True
                self.assertEqual(restarted._bgWeId, "123")
                self.assertEqual(restarted.sceneInitialProps(), {"flag": False, "lvl": 0.8})
                self.assertIs(next(p for p in restarted.weProps("123") if p["name"] == "flag")["effective"], False)
            finally:
                restarted.shutdown()
        finally:
            b.shutdown()
            settings.save(self._tmpdir, _settings_before)

    def test_extracted_image_keeps_wallpaper_identity_and_zero_saturation(self):
        from core import settings

        original = settings.load(self._tmpdir)
        image = Path(self._tmpdir) / "extracted-wallpaper.png"
        image.write_bytes(b"test image")
        try:
            settings.save(self._tmpdir, {"bg_image": str(image), "bg_we_id": "123", "bg_saturation": 0})
            restarted = self.backend_mod.AppBackend()
            try:
                self.assertEqual(restarted._bgWeId, "123")
                self.assertEqual(restarted.bgSaturation, 0)
            finally:
                restarted.shutdown()
        finally:
            settings.save(self._tmpdir, original)

    # ---- 面板磨砂 / 系统字体（2026-11-16 追加）----

    def test_glass_blur_default_off_and_clamped(self):
        """面板磨砂默认关闭 —— 它是额外一遍模糊，不该默认吃 GPU。"""
        from core import settings as _settings

        _settings.save(self._tmpdir, {})
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.glassBlur, 0)
        self.b.setGlassBlur(999)
        self.assertEqual(self.b.glassBlur, 60)
        self.b.setGlassBlur(-3)
        self.assertEqual(self.b.glassBlur, 0)

    def test_glass_blur_persists(self):
        from core import settings as _settings

        self.b.setGlassBlur(33)
        self.assertEqual(_settings.load(self._tmpdir).get("glass_blur"), 33)
        b2 = self.backend_mod.AppBackend()
        self.assertEqual(b2.glassBlur, 33)

    def test_system_fonts_returns_sorted_unique_list(self):
        """字体下拉的数据源：必须是去重后的字符串列表（可能为空，取决于环境）。"""
        fonts = self.b.systemFonts()
        self.assertIsInstance(fonts, list)
        self.assertEqual(len(fonts), len(set(fonts)), "字体列表不该有重复项")
        if fonts:
            self.assertTrue(all(isinstance(f, str) and f for f in fonts))
        # 第二次必须命中缓存（同一个对象）
        self.assertIs(self.b.systemFonts(), fonts)


if __name__ == "__main__":
    unittest.main()

class SelfUpdateScriptTest(unittest.TestCase):
    """自动更新脚本（2026-11-19）：纯生成逻辑的回归测试。

    ★ 真实替换动作已用独立的 e2e 脚本验证过（备份→解压→重启→清理、失败回滚、无残留），
      这里只钉住"生成出来的脚本内容对不对"—— 尤其是 $PID 那个坑。
    """

    APP = r"D:\Apps\AeLocalizer"
    ZIP = r"D:\DL\AeLocalizer-1.1.0-win64.zip"
    LOG = r"D:\Data\update.log"

    def _script(self, **kw):
        from core import updater as up

        args = dict(pid=4321, app_dir=self.APP, zip_path=self.ZIP, log_path=self.LOG)
        args.update(kw)
        return up.build_update_script(**args)

    def test_embeds_paths_and_pid(self):
        s = self._script()
        self.assertIn("$TargetPid = 4321", s)
        self.assertIn("'" + self.APP + "'", s)
        self.assertIn("'" + self.ZIP + "'", s)
        self.assertIn("'" + self.LOG + "'", s)

    def test_never_assigns_automatic_pid_variable(self):
        """$PID 是 PowerShell 的只读自动变量 —— 赋值会立刻报错退出（实测踩过）。"""
        s = self._script()
        self.assertNotIn("$Pid =", s)
        self.assertNotRegex(s, r"\$PID\s*=")

    def test_has_backup_extract_restart_rollback(self):
        s = self._script()
        for key in ("Rename-Item", "tar.exe", "Expand-Archive", "Start-Process",
                    "回滚", "Remove-Item"):
            self.assertIn(key, s)

    def test_single_quote_in_path_is_escaped(self):
        s = self._script(app_dir=r"D:\O'Brien\AeLocalizer")
        self.assertIn(r"'D:\O''Brien\AeLocalizer'", s)

    def test_can_self_update_detects_writable_dir(self):
        from core import updater as up

        tmp = tempfile.mkdtemp(prefix="aeloc_upd_")
        try:
            self.assertTrue(up.can_self_update(tmp))
            self.assertFalse(up.can_self_update(os.path.join(tmp, "missing")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_user_dir_fallback_script(self):
        """程序目录不可写时（装在 Program Files）：改成装到用户目录，不用管理员。"""
        from core import updater as up

        target = up.user_install_dir()
        s = self._script(in_place=False, target_dir=target)
        self.assertIn("$InPlace = $false", s)
        self.assertIn("'" + target + "'", s)
        self.assertIn("无需管理员", s)
        self.assertIn('$Bak = "$TargetDir.old-"', s)
        self.assertIn("$rollback =", s)

    def test_in_place_script_uses_app_dir(self):
        s = self._script()
        self.assertIn("$InPlace = $true", s)
        self.assertIn("'D:\\Apps\\AeLocalizer'", s)
