"""设置页(主题/背景图/更新器)的 backend mixin(2026-11-01 解耦拆分)。

★ 拆分规约(backend 大搬批次的试点):
  · mixin 是普通类(不继承 QObject),AppBackend 组装:`class AppBackend(SettingsUiMixin, QObject)`
  · 本区段的 Signal 声明在这里(随 MRO 进最终 metaobject);Slot/Property 原样搬
  · 对外契约零变化:QML/测试引用的成员名一个不动
  · 运行期状态(_uiTheme/_update*)在 AppBackend.__init__ 初始化,这里只定义行为
  · 数据根走 _data_root()(调用时经 app.backend 模块对象取) —— 与测试
    「打补丁 backend_mod.DATA_ROOT」的沙盒契约逐次调用保持一致
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

from PySide6.QtCore import Property, QTimer, QUrl, Signal, Slot
from core import updater as _updater
from core.version import APP_VERSION, DEFAULT_UPDATE_REPOSITORY
from app.backend_parts.worker import Worker

#: 个性化项的三个枚举档位（2026-11-16 设置页改版）。
#: ★ 后端是唯一的合法值判定方：QML 传什么都可能（旧配置文件、手改、测试），
#:   非法值一律忽略并保留原值，绝不让 Theme 单例拿到不存在的档位。
_VALID_ACCENTS = ("blue", "violet", "teal", "amber", "rose", "slate")
_VALID_DENSITY = ("compact", "normal", "relaxed")
_VALID_FONT_SCALE = ("small", "normal", "large")


def _data_root() -> str:
    """调用时取当前生效的数据根(测试沙盒打的是 backend 模块属性补丁)。"""
    import app.backend as _bm

    return _bm.DATA_ROOT


class SettingsUiMixin:
    # ---- 通知(随 MRO 注册进 AppBackend 的 metaobject)----
    #: 主题模式变了
    uiThemeChanged = Signal()
    #: 检查/下载的状态或结论文字变了
    updateChanged = Signal()
    #: 下载进度(百分比,-1 = 总长未知)
    updateProgressChanged = Signal(int)
    #: 背景图/浓度变了(设置页外观区)
    bgChanged = Signal()
    #: 个性化项(强调色/密度/字号/动效/底板)变了 —— QML 收到后灌进 Theme 单例
    appearanceChanged = Signal()
    startupWithAeChanged = Signal()

    # ---------------------------------------------------------- 设置页（主题 / 更新）

    _VALID_THEMES = ("light", "dark", "system")

    def _loadUiSettings(self) -> None:
        """启动时恢复主题模式 / 更新源 / 背景图。坏值回退默认，绝不让设置文件坏了影响启动。"""
        try:
            from core import settings as _settings

            obj = _settings.load(_data_root())
        except Exception:
            return
        try:
            mode = str(obj.get("ui_theme") or "")
            if mode in self._VALID_THEMES:
                self._uiTheme = mode
            try:
                self._updateRepository = _updater.normalize_github_repository(
                    str(obj.get("update_repository") or "")) or DEFAULT_UPDATE_REPOSITORY
            except ValueError:
                self._updateRepository = DEFAULT_UPDATE_REPOSITORY
            img = str(obj.get("bg_image") or "").strip()
            if img and os.path.isfile(img):
                self._bgImage = img
            try:
                d = float(obj.get("bg_dim"))
            except (TypeError, ValueError):
                d = self._bgDim
            if 0.05 <= d <= 0.95:
                self._bgDim = d
            # ---- 个性化（2026-11-16）----
            ak = str(obj.get("accent_key") or "")
            if ak in _VALID_ACCENTS:
                self._accentKey = ak
            dn = str(obj.get("density") or "")
            if dn in _VALID_DENSITY:
                self._density = dn
            fs = str(obj.get("font_scale") or "")
            if fs in _VALID_FONT_SCALE:
                self._fontScale = fs
            self._reduceMotion = bool(obj.get("reduce_motion") or False)
            # ★ 默认 True：老配置没有这个键时行为不变（照旧启动自动扫描）
            self._autoScan = bool(obj.get("auto_scan", True))
            # 字体与文字颜色（2026-11-16）
            fm = str(obj.get("ui_font") or "").strip()
            self._uiFont = fm[:64]
            tc = str(obj.get("text_color") or "").strip().lower()
            if re.fullmatch(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})", tc):
                self._textColor = tc
            # ---- 壁纸引擎背景（2026-11-16）----
            # 视频背景只存路径（视频动辄几百 MB，不复制）；载入时校验存在性。
            if hasattr(self, "_bgVideo"):
                vb = str(obj.get("bg_video") or "").strip()
                self._bgVideo = vb if vb and os.path.isfile(vb) else ""
                self._bgWeId = str(obj.get("bg_we_id") or "").strip() if (self._bgVideo or self._bgImage) else ""
                # ---- 画面调节（2026-11-16）：全部中性值兜底，老配置升级观感不变 ----
                if hasattr(self, "_bgBlur"):
                    # ★ 缺键时的默认值 = 「磨砂玻璃」档（与 wallpaper_ui._we_init 一致）。
                    #   默认 0 的话，加了壁纸会先看到一层没磨砂的图压着字，
                    #   用户还得自己去摸索砂滑块。
                    try:
                        self._bgBlur = max(0, min(60, int(obj.get("bg_blur", 24) or 0)))
                    except (TypeError, ValueError):
                        pass
                    try:
                        self._bgBrightness = max(50, min(160, int(obj.get("bg_brightness", 94) or 94)))
                    except (TypeError, ValueError):
                        pass
                    try:
                        self._bgSaturation = max(0, min(200, int(obj.get("bg_saturation", 92))))
                    except (TypeError, ValueError):
                        pass
                    try:
                        self._bgContrast = max(50, min(160, int(obj.get("bg_contrast", 100) or 100)))
                    except (TypeError, ValueError):
                        pass
                    fit = str(obj.get("bg_fit") or "cover")
                    if fit in ("cover", "contain"):
                        self._bgFit = fit
                    if hasattr(self, "_glassBlur"):
                        try:
                            self._glassBlur = max(0, min(60, int(obj.get("glass_blur", 0) or 0)))
                        except (TypeError, ValueError):
                            pass
                    # 每张壁纸自己的画面参数（2026-11-16）：坏值整条丢弃，绝不让
                    # 设置文件里的一条脏数据把渲染改乱。
                    # 场景实时渲染（2026-11-17）：只存目录与壁纸 id —— 端口是随机的，
                    # URL 不能落盘，重启后按目录重新起服务。
                    if hasattr(self, "_bgSceneUrl"):
                        self._bgSceneWeId = str(obj.get("bg_scene_we_id") or "").strip()[:32]
                        d = str(obj.get("bg_scene_dir") or "").strip()
                        self._bgSceneDir = d if (d and os.path.isdir(d)) else ""
                        if not self._bgSceneDir:
                            self._bgSceneWeId = ""
                        elif self._bgSceneWeId:
                            self._bgWeId = self._bgSceneWeId
                    if hasattr(self, "_weSceneFail"):
                        sf = obj.get("we_scene_fail")
                        if isinstance(sf, list):
                            self._weSceneFail = {str(x) for x in sf[:200] if str(x)}
                    if hasattr(self, "_wePending"):
                        wp2 = obj.get("we_props_pending")
                        if isinstance(wp2, dict):
                            pend = {}
                            for k, v in list(wp2.items())[:200]:
                                if isinstance(v, dict) and str(k):
                                    pend[str(k)] = {str(a): b for a, b in list(v.items())[:40]}
                            self._wePending = pend
                    scene_props = obj.get("we_scene_props")
                    if isinstance(scene_props, dict):
                        self._weSceneProps = {
                            wid: props for wid, props in scene_props.items() if isinstance(props, dict)
                        }
                    if hasattr(self, "_weTune"):
                        wt = obj.get("we_tune")
                        if isinstance(wt, dict):
                            clean = {}
                            for k, v in list(wt.items())[:200]:
                                if isinstance(v, dict) and str(k):
                                    clean[str(k)] = {str(a): b for a, b in list(v.items())[:8]}
                            self._weTune = clean
                    # 旧配置可能只有未模糊的背景图，影响文字可读性。
                    # 首次迁移缺少画面调节参数的配置时补上默认值。
                    if "bg_blur" not in obj and (self._bgVideo or self._bgImage):
                        if "bg_dim" not in obj:
                            self._bgDim = 0.22
                        self._plate = 0.60
            try:
                pl = float(obj.get("plate"))
            except (TypeError, ValueError):
                pl = self._plate
            if 0.0 <= pl <= 1.0:
                self._plate = pl
        except Exception:
            pass

    def _saveUiSettings(self) -> None:
        """把主题/更新源/背景写回 settings.json（一次原子写）。"""
        try:
            from core import settings as _settings

            obj = _settings.load(_data_root())
            obj["ui_theme"] = self._uiTheme
            obj["update_repository"] = self._updateRepository
            obj["bg_image"] = self._bgImage
            obj["bg_dim"] = self._bgDim
            obj["accent_key"] = self._accentKey
            obj["density"] = self._density
            obj["font_scale"] = self._fontScale
            obj["reduce_motion"] = self._reduceMotion
            obj["auto_scan"] = self._autoScan
            obj["plate"] = self._plate
            obj["bg_video"] = getattr(self, "_bgVideo", "")
            obj["bg_we_id"] = getattr(self, "_bgWeId", "")
            obj["bg_blur"] = getattr(self, "_bgBlur", 0)
            obj["bg_brightness"] = getattr(self, "_bgBrightness", 100)
            obj["bg_saturation"] = getattr(self, "_bgSaturation", 100)
            obj["bg_contrast"] = getattr(self, "_bgContrast", 100)
            obj["bg_fit"] = getattr(self, "_bgFit", "cover")
            obj["glass_blur"] = getattr(self, "_glassBlur", 0)
            obj["we_tune"] = getattr(self, "_weTune", {})
            obj["we_scene_fail"] = sorted(getattr(self, "_weSceneFail", set()))
            obj["bg_scene_we_id"] = getattr(self, "_bgSceneWeId", "")
            obj["bg_scene_dir"] = getattr(self, "_bgSceneDir", "")
            obj["we_props_pending"] = getattr(self, "_wePending", {})
            obj["we_scene_props"] = getattr(self, "_weSceneProps", {})
            obj["ui_font"] = getattr(self, "_uiFont", "")
            obj["text_color"] = getattr(self, "_textColor", "")
            _settings.save(_data_root(), obj)
        except Exception:
            pass  # 设置保存失败不影响本次运行

    def _spawn_update_worker(self, fn, on_done, on_fail) -> None:
        """跑一个更新相关后台任务。不占全局 busy —— 不许挡住其它页面的操作。"""
        w = Worker(fn)
        self._workers.append(w)
        w.done.connect(on_done)
        w.failed.connect(on_fail)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        w.start()

    @Property(str, notify=uiThemeChanged)
    def uiTheme(self):
        return self._uiTheme

    @Slot(str)
    def setUiTheme(self, mode: str):
        """切主题。QML 侧拿到 uiThemeChanged 后把 Theme.themeMode 置掉 —— 即时生效不重启。"""
        if mode not in self._VALID_THEMES or mode == self._uiTheme:
            return
        self._uiTheme = mode
        self._saveUiSettings()
        self.uiThemeChanged.emit()

    @Property(str, constant=True)
    def appVersion(self):
        return APP_VERSION

    @Property(str, constant=True)
    def appBuildTag(self):
        """界面构建标记 = app/qml 下最新一个 .qml 的 mtime（MMdd-HHmm）。

        ★ 为什么需要它：QML 是**启动时**加载的，本项目最常见的故障就是
          「改了没反应 / 界面还是旧的」。光看截图分不清用户跑的是哪份代码，
          有这个标记一眼就能确认「是不是没重启 / 是不是打包的旧 exe」。
          它由文件 mtime 推出来，不需要手动维护，改一次 QML 就自动变。
        """
        import time

        qml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qml")
        newest = 0.0
        for base, _dirs, files in os.walk(qml_dir):
            for name in files:
                if name.endswith((".qml", ".js")):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(base, name)))
                    except OSError:
                        pass
        return time.strftime("%m%d-%H%M", time.localtime(newest)) if newest else "?"

    # ---- 背景图 ----

    _BG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    _BG_MAX_BYTES = 30 * 1024 * 1024

    @Property(str, notify=bgChanged)
    def bgImage(self):
        """QML Image 直接可用的 file:/// URL；无背景图返回空串。"""
        if not self._bgImage:
            return ""
        return QUrl.fromLocalFile(self._bgImage).toString()

    @Slot(str)
    def setBgImage(self, src: str):
        """设置背景图。接受 file:/// URL 或本地路径；复制进数据目录
        （源文件之后被移动/删除也不失效），旧图清理。不合法就 toast 拒绝。"""
        path = str(src or "").strip()
        if path.lower().startswith("file:"):
            path = QUrl(path).toLocalFile()
        if not path:
            return
        path = os.path.normpath(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._BG_EXTS:
            self.toast.emit("error", "不支持的图片格式（%s）—— 用 png / jpg / webp / bmp" % (ext or "无扩展名"))
            return
        if not os.path.isfile(path):
            self.toast.emit("error", "文件不存在：%s" % path)
            return
        try:
            if os.path.getsize(path) > self._BG_MAX_BYTES:
                self.toast.emit("error", "图片超过 30MB，换张小点的")
                return
        except OSError as exc:
            self.toast.emit("error", "背景图读取失败：%s" % exc)
            return
        self._setMediaBackground(path, "image", "")

    @Slot()
    def clearBgImage(self):
        self.clearWallpaperBackground()

    @Property(float, notify=bgChanged)  # ★ PySide 类型名是 float（写 real 会 NameError）
    def bgDim(self):
        return self._bgDim

    @Slot(float)
    def setBgDim(self, v: float):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        v = max(0.05, min(0.95, v))
        if abs(v - self._bgDim) < 0.001:
            return
        self._bgDim = v
        self._saveUiSettings()
        self.bgChanged.emit()

    # ---- 个性化（2026-11-16 设置页改版）----
    #
    # 五个键全部走 appearanceChanged 一个信号：QML 那边是一个 Connections 全量
    # 灌进 Theme 单例，拆成五个信号只会让 main.qml 多四段重复代码。

    @Property(str, notify=appearanceChanged)
    def accentKey(self):
        return self._accentKey

    @Slot(str)
    def setAccentKey(self, key: str):
        key = str(key or "").strip()
        if key not in _VALID_ACCENTS or key == self._accentKey:
            return
        self._accentKey = key
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(str, notify=appearanceChanged)
    def density(self):
        return self._density

    @Slot(str)
    def setDensity(self, v: str):
        v = str(v or "").strip()
        if v not in _VALID_DENSITY or v == self._density:
            return
        self._density = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(str, notify=appearanceChanged)
    def fontScale(self):
        return self._fontScale

    @Slot(str)
    def setFontScale(self, v: str):
        v = str(v or "").strip()
        if v not in _VALID_FONT_SCALE or v == self._fontScale:
            return
        self._fontScale = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(bool, notify=appearanceChanged)
    def reduceMotion(self):
        return self._reduceMotion

    @Slot(bool)
    def setReduceMotion(self, v: bool):
        v = bool(v)
        if v == self._reduceMotion:
            return
        self._reduceMotion = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(float, notify=appearanceChanged)
    def plate(self):
        """内容底板不透明度。1 = 完全盖住背景图，0 = 完全透出。"""
        return self._plate

    @Slot(float)
    def setPlate(self, v: float):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if v != v:          # NaN（QML 空串 parseFloat 出来的）
            return
        v = max(0.0, min(1.0, v))
        if abs(v - self._plate) < 0.005:
            return
        self._plate = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(bool, notify=startupWithAeChanged)
    def startupWithAe(self):
        from core import windows_startup
        return windows_startup.is_enabled(_data_root())

    @Slot(bool)
    def setStartupWithAe(self, enabled):
        from core import windows_startup
        try:
            windows_startup.set_enabled(enabled, _data_root())
        except (OSError, ValueError) as exc:
            self.toast.emit("warn", str(exc))
        self.startupWithAeChanged.emit()

    @Property(bool, notify=appearanceChanged)
    def autoScan(self):
        """启动后是否自动静默扫描一次插件清单（常规分区）。"""
        return self._autoScan

    @Slot(bool)
    def setAutoScan(self, v: bool):
        """★ 只影响【下一次启动】。

        当前进程的预扫是由 __init__ 里的 1500ms 单次定时器起的，运行中把它
        改成 false 不会取消已经排上的那次；反过来改成 true 也不补扫。
        这是有意的 —— 运行中补一次全量扫描会卡界面，而且用户此刻多半
        正在别的页面做事。真要让当前会话立刻扫，用「翻译」页的扫描按钮。
        """
        v = bool(v)
        if v == self._autoScan:
            return
        self._autoScan = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    # ---- 字体与文字颜色（2026-11-16）----
    #
    # 壁纸亮度不同。允许设置文字颜色，便于在浅色或深色背景上保持可读性。

    @Property(str, notify=appearanceChanged)
    def uiFont(self):
        return self._uiFont

    @Slot(result="QStringList")
    def systemFonts(self):
        """本机已安装的字体族名（给设置页的字体下拉用）。

        ★ 为什么要缓存：QFontDatabase.families() 每次都重新枚举系统字体，
          在 Windows 上要几十毫秒。设置页会反复求值（每次切分区、每次绑定刷新），
          不缓存会让界面时不时卡一下。
        ★ 排序用 casefold：中文名与英文名混排时，纯 ascii 排序会把所有中文排到最后，
          看着像"没排序"。
        """
        if self._fontCache is not None:
            return self._fontCache
        names = []
        try:
            from PySide6.QtGui import QFontDatabase

            names = [str(n) for n in QFontDatabase.families() if str(n).strip()]
        except Exception:
            names = []
        names = sorted(set(names), key=lambda s: (s.casefold(), s))
        self._fontCache = names
        return names

    @Slot(str)
    def setUiFont(self, name: str):
        """换界面字体。空串 = 恢复内置默认（Microsoft YaHei UI）。"""
        name = str(name or "").strip()
        if name == self._uiFont:
            return
        self._uiFont = name
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(str, notify=appearanceChanged)
    def textColor(self):
        """自定义文字颜色（#RRGGBB）；空串 = 跟随主题。"""
        return self._textColor

    @Slot(str)
    def setTextColor(self, hexstr: str):
        """设置文字颜色。接受 #RGB / #RRGGBB，非法值忽略。"""
        v = str(hexstr or "").strip()
        if v and not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", v):
            return
        v = v.lower()
        if v == self._textColor:
            return
        self._textColor = v
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Slot()
    def resetAppearance(self):
        self._accentKey = "blue"
        self._density = "normal"
        self._fontScale = "normal"
        self._reduceMotion = False
        self._plate = 0.85
        self._saveUiSettings()
        self.appearanceChanged.emit()

    @Property(str, notify=updateChanged)
    def updateRepository(self):
        return self._updateRepository

    @Slot(str, result=bool)
    def setUpdateRepository(self, value: str) -> bool:
        if self._updateState in ("checking", "downloading", "installing"):
            return False
        try:
            repository = _updater.normalize_github_repository(value) or DEFAULT_UPDATE_REPOSITORY
        except ValueError:
            return False
        if repository == self._updateRepository:
            return True
        self._updateRepository = repository
        self._updateState = ""
        self._updateText = ""
        self._clear_update_package()
        self._saveUiSettings()
        self.updateChanged.emit()
        return True

    def _clear_update_package(self):
        self._updateLatest = ""
        self._updateDownloadUrl = ""
        self._updateSha256 = ""
        self._updateFile = ""

    @Property(str, notify=updateChanged)
    def updateState(self):
        return self._updateState

    @Property(str, notify=updateChanged)
    def updateText(self):
        return self._updateText

    @Property(str, notify=updateChanged)
    def updateLatest(self):
        return self._updateLatest

    @Property(str, notify=updateChanged)
    def updateFile(self):
        return self._updateFile

    @Property(int, notify=updateProgressChanged)
    def updateProgress(self):
        return self._updateProgress

    @Slot()
    def checkUpdate(self):
        if self._updateState in ("checking", "downloading", "installing"):
            return
        repository = self._updateRepository
        if not repository:
            self._updateState = "error"
            self._updateText = "未配置更新源，请先添加 GitHub 仓库"
            self.updateChanged.emit()
            return
        self._clear_update_package()
        self._updateState = "checking"
        self._updateText = "正在检查 GitHub Releases…"
        self.updateChanged.emit()

        def _job():
            return _updater.check_github_release(repository, APP_VERSION)

        def _done(res):
            self._updateLatest = res["latest"]
            if res["has_update"]:
                self._updateDownloadUrl = res["download_url"]
                self._updateSha256 = res["sha256"]
                self._updateState = "available"
                self._updateText = "发现新版本 v%s" % res["latest"]
            else:
                self._updateState = "latest"
                self._updateText = "已是最新版本"
            self.updateChanged.emit()

        def _fail(msg):
            self._updateState = "error"
            self._updateText = "检查失败：" + msg.splitlines()[0][:200]
            self.updateChanged.emit()

        self._spawn_update_worker(_job, _done, _fail)

    @Slot()
    def downloadUpdate(self):
        """下载更新包到数据目录 update/ 下，边下边验 sha256。"""
        if self._updateState not in ("available", "download-error"):
            return
        if not self._updateDownloadUrl or not self._updateSha256:
            return
        self._updateState = "downloading"
        self._updateText = "正在下载…"
        self._updateProgress = -1
        self.updateChanged.emit()
        url, sha = self._updateDownloadUrl, self._updateSha256
        dest = os.path.join(_data_root(), "update")

        def _job():
            def prog(_total, pct):
                self._updateProgress = pct
                self.updateProgressChanged.emit(pct)

            return _updater.download(url, dest, sha256=sha, progress=prog)

        def _done(path):
            self._updateFile = path
            self._updateState = "downloaded"
            self._updateText = "v%s 已下载并验证" % self._updateLatest
            self.updateChanged.emit()

        def _fail(msg):
            self._updateState = "download-error"
            self._updateText = "下载失败：" + msg.splitlines()[0][:200]
            self.updateChanged.emit()

        self._spawn_update_worker(_job, _done, _fail)

    @Slot()
    def installUpdate(self):
        """一键自动更新：等本进程退出→备份→解压新包→重启（2026-11-19）。

        ★ 为什么必须交给外部脚本：Windows 不允许覆盖正在运行的 exe，
          所以只能先写脚本、再退出自己，由脚本完成替换。
        ★ 失败会回滚（脚本把备份目录改回来），并把过程写进 update.log。
        """
        if self._updateState != "downloaded" or not self._updateFile:
            return
        if self.panelLocalization.busy:
            self._updateText = "面板与脚本正在处理任务，请稍候再安装更新"
            self.updateChanged.emit()
            return
        if not getattr(sys, "frozen", False):
            self._updateState = "error"
            self._updateText = "源码运行不支持自动更新（只有打包版可以）"
            self.updateChanged.emit()
            return
        if self._engineBusy:
            self._updateText = "引擎正在启停，请稍候再点立即更新"
            self.updateChanged.emit()
            return
        from core import engine

        # 冻结版守卫与界面使用同一个 exe，正常关闭界面后守卫仍可能占用旧目录。
        # 更新前走已有的安全卸载流程，保留用户的开关意图供重启后恢复。
        self._engineBusy = True
        self._updateState = "installing"
        self._updateText = "正在安全停止汉化守卫…"
        self.stateChanged.emit()
        self.updateChanged.emit()

        def failed(msg):
            self._engineBusy = False
            self._updateState = "downloaded"
            self._updateText = str(msg).split("\n", 1)[0][:200]
            self.stateChanged.emit()
            self.updateChanged.emit()

        def stopped(result):
            ok, msg = result
            if not ok:
                failed("暂时无法更新：" + msg)
                return
            try:
                self._launch_update_script()
            except Exception as exc:
                failed("启动更新脚本失败：" + str(exc))

        self._spawn_update_worker(
            engine.stop_engine, stopped,
            lambda msg: failed("停止汉化守卫失败：" + msg))

    def _launch_update_script(self):
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
        # ★ 免管理员：程序目录不可写（装在 Program Files）时**不报错**，
        #   改装到 %LOCALAPPDATA%\Programs\AeLocalizer —— 那里当前用户可写，
        #   旧目录原样留着（用户自己删），从此新版本就在用户目录里跑。
        in_place = _updater.can_self_update(app_dir)
        target_dir = "" if in_place else _updater.user_install_dir()
        data = _data_root()
        log_path = os.path.join(data, "update.log")
        script = os.path.join(data, "update", "apply_update.ps1")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        text = _updater.build_update_script(
            os.getpid(), app_dir, self._updateFile, log_path,
            in_place=in_place, target_dir=target_dir)
        with open(script, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text)
        # Windows PowerShell can exit successfully without running -File when
        # launched with DETACHED_PROCESS. CREATE_NO_WINDOW still keeps it hidden.
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", script],
            creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
        )
        if in_place:
            self._updateText = "正在替换并重启…（过程见 %s）" % log_path
        else:
            self._updateText = ("程序目录不可写，正在安装到用户目录（无需管理员）：\n%s\n"
                                "完成后会从新位置启动，旧目录可自行删除。日志：%s"
                                % (target_dir, log_path))
        self.updateChanged.emit()
        QTimer.singleShot(700, self, self._quitForUpdate)

    def _quitForUpdate(self) -> None:
        """退出主程序，把 exe 让给更新脚本。"""
        try:
            from PySide6.QtGui import QGuiApplication

            app = QGuiApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass

    @Slot()
    def openUpdateFolder(self):
        """在资源管理器里打开已下载更新包所在目录。"""
        if self._updateFile:
            _updater.open_folder(os.path.dirname(self._updateFile))

    @Slot()
    def openDataDir(self):
        """在资源管理器里打开用户数据目录（settings/词典库/密钥都在这）。"""
        _updater.open_folder(_data_root())

    # ---------------------------------------------------------- 数据 / 诊断 / 关于

    @Property(str, notify=updateChanged)
    def dataDirText(self):
        """数据目录的显示路径。

        ★ 不暴露成可编辑 —— 换数据目录等于换掉整个词典库/密钥/选区，
          是本项目没有实现过的迁移路径，不做成设置项。
        """
        return _data_root()

    @Slot(result=str)
    def dataDirUsage(self):
        """数据目录当前占用（人类可读）+ 文件数。目录不存在返回「—」。"""
        root = _data_root()
        total = 0
        files = 0
        try:
            for cur, _dirs, names in os.walk(root):
                for n in names:
                    try:
                        st = os.stat(os.path.join(cur, n))
                    except OSError:
                        continue
                    total += st.st_size
                    files += 1
        except OSError:
            return "—"
        return "%s · %d 个文件" % (_human_size(total), files)

    @Property(int, notify=updateChanged)
    def aiCacheEntries(self):
        """AI 翻译缓存条数（设置页「数据」区显示，便于判断要不要清）。"""
        try:
            return len(self._aiCache)
        except Exception:
            return 0

    @Slot(result=str)
    def clearAiCache(self):
        """清空 AI 翻译缓存。清完下次翻译会重新请求（会计费），所以要二次确认。"""
        try:
            path = getattr(self._aiCache, "path", "")
            self._aiCache._data = {}
            self._aiCache._dirty = True
            self._aiCache.flush()
            if path and os.path.isfile(path):
                os.unlink(path)
        except Exception as exc:
            msg = "清理失败：%s" % str(exc)[:80]
            self.toast.emit("error", msg)
            return msg
        self.updateChanged.emit()
        msg = "已清空 AI 翻译缓存（下次翻译会重新请求）"
        self.toast.emit("info", msg)
        return msg

    @Slot(result=str)
    def diagnosticText(self):
        """拼一份可直接贴进问题反馈的诊断信息。纯只读，不改任何状态。"""
        lines = [
            "AeLocalizer %s" % APP_VERSION,
            "数据目录: %s" % _data_root(),
        ]
        try:
            import PySide6

            from PySide6.QtCore import qVersion

            lines.append("PySide6 %s / Qt %s" % (PySide6.__version__, qVersion()))
        except Exception:
            lines.append("PySide6: 未知")
        try:
            lines.append("Python %s" % os.sys.version.split()[0])
        except Exception:
            pass
        try:
            from core import engine as _engine

            st = _engine.check(getattr(self, "_root", ""), getattr(self, "_version", ""))
            lines.append("引擎: %s" % ("运行中" if st.running else "未运行"))
            lines.append("引擎词典: %d 条" % int(getattr(st, "engine_dict_entries", 0) or 0))
        except Exception:
            lines.append("引擎: 读取失败")
        try:
            lines.append("选定 AE: %s" % (getattr(self, "_version", "") or "未选"))
            lines.append("运行 AE: %s" % self.hostAeVersionText)
            if self.languageStatusText:
                lines.append(self.languageStatusText)
        except Exception:
            pass
        try:
            lines.append("词典生效条目: %d" % len(self._store.resolve()))
        except Exception:
            pass
        try:
            lines.append("AI 缓存: %d 条" % len(self._aiCache))
        except Exception:
            pass
        return "\n".join(lines)

    @Slot(result=str)
    def copyDiagnostic(self):
        """把诊断信息复制到剪贴板（QML 的 TextEdit 剪贴板操作在无头环境下不可靠）。"""
        text = self.diagnosticText()
        try:
            from PySide6.QtGui import QGuiApplication

            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.setText(text)
        except Exception as exc:
            self.toast.emit("warn", "复制失败：%s" % str(exc)[:60])
            return text
        self.toast.emit("info", "诊断信息已复制到剪贴板")
        return text


def _human_size(n: int) -> str:
    """字节数 -> 人类可读（BD 目录里动辄几百 MB，必须带单位）。"""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return "%.0f %s" % (v, unit) if unit == "B" else "%.1f %s" % (v, unit)
        v /= 1024.0
    return "%.1f GB" % v
