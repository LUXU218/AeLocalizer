"""壁纸引擎（Wallpaper Engine）接入的 backend mixin（2026-11-16）。

用户在设置页里选一张本地壁纸引擎的壁纸当背景。两种呈现：
  · 图片类 / 场景类里的 JPEG-PNG → 复制进数据目录当静态背景（走既有的 bg_image）
  · 视频类 / 场景里内嵌的 MP4     → 记下路径当视频背景（新通道 bg_video，实时播放）

★ 拆分规约同 settings_ui.py：mixin 是普通类（不继承 QObject），
  由 `class AppBackend(WallpaperUiMixin, SettingsUiMixin, QObject)` 组装；
  Signal 声明在这里（随 MRO 进最终 metaobject）；对外契约对 QML 生效。

★ 为什么视频不复制进数据目录：一张视频壁纸动辄 100–1000 MB，复制一份纯浪费，
  而且是用户自己 Steam 库里的文件（卸载壁纸后自然失效）。所以视频只记路径，
  载入时校验存在性 —— 文件没了就安静退回无背景，不弹错误占满屏幕。
  图片类相反：几十 KB～几 MB，复制进 ui_bg/ 才能防源文件移动。

★ 为什么提取放后台线程：拆 scene.pkg 可能要读上百 MB（实测最大 336 MB），
  在 UI 线程做会把界面卡住几秒。走既有的 Worker（QThread）模式。
"""
from __future__ import annotations

import json
import os
import shutil

from PySide6.QtCore import Property, QUrl, Signal, Slot


def _data_root() -> str:
    """调用时取当前生效的数据根（测试沙盒打的是 backend 模块属性补丁）。"""
    import app.backend as _bm

    return _bm.DATA_ROOT


class WallpaperUiMixin:
    # ---- 通知 ----
    #: 壁纸引擎可用性 / 壁纸清单变了
    weChanged = Signal()
    #: 视频背景变了。
    #:
    #: ★ 为什么不复用 SettingsUiMixin 的 bgChanged：那是另一个类的 Signal，
    #:   在本类体的 `@Property(notify=...)` 里不可见（NameError）。而重名再声明
    #:   一个 bgChanged 会让最终 metaobject 里出现两个同名信号，QML 的
    #:   onBgChanged 处理器行为不确定。所以视频通道用自己的信号，
    #:   改背景时两个信号都发（图片那条仍走 bgChanged）。
    videoBgChanged = Signal()
    #: 壁纸自带参数写成功后发出（name, value）——
    #: ★ 场景实时渲染时 QML 靠它热更新；提取画面时没人接，不影响。
    wePropApplied = Signal(str, "QVariant")

    # ---------------------------------------------------------- 基础状态

    def _we_init(self) -> None:
        """初始化壁纸引擎相关状态。由 AppBackend.__init__ 调用。

        ★ 只做**安装探测**（读注册表 + 一个 VDF，几毫秒），不做壁纸枚举 ——
          枚举要读几十个 project.json，属于「打开外观分区才需要」的 I/O。
          但可用性必须此刻就知道：设置页要靠它决定要不要显示「从壁纸引擎选择」
          这个入口，等用户点进去才算就等于入口一直不出现。
        """
        self._weItems = []          # 缓存的清单（[{id,title,...}]）
        self._weScanned = False
        self._weInstall = ""
        self._weError = ""
        self._bgVideo = ""          # 视频背景的本地绝对路径；空 = 无
        self._bgWeId = ""           # 当前背景来自哪张壁纸（空 = 自定义/无）
        # 有壁纸时应用磨砂、透明度和清晰度调节，默认使用磨砂玻璃样式。
        self._bgBlur = 24            # 0..60 磨砂档位
        self._bgBrightness = 94      # 50..160 %（100 = 不改）
        self._bgSaturation = 92      # 0..200 %
        self._bgContrast = 100       # 50..160 %
        self._bgFit = "cover"        # cover | contain
        self._glassBlur = 0          # 0..60 面板自己的磨砂档位（0 = 关）
        #: 按壁纸 ID 保存画面参数。再次选中同一壁纸时恢复其调节值。
        #: 这些值用于显示背景，场景文件的参数由 Wallpaper Engine 管理。
        self._weTune = {}
        #: 待写入壁纸引擎的属性改动 {壁纸id: {属性名: 值}}。
        #: ★ 壁纸引擎几乎总在运行，而它退出时会把 config.json 整份覆盖回去。
        #: 所以「改了但引擎在跑」时不装作写完就完事：记下来，等引擎关掉后
        #: （下次启动本工具/重新扫描时）自动重写一遍。
        self._wePending = {}
        self._weSceneProps = {}
        # ---- 场景实时渲染（2026-11-17，WebWallGL + QtWebEngine）----
        #: 当前是不是「实时渲染」模式；这两项要持久化，重启后要能接回。
        self._bgSceneWeId = ""
        self._bgSceneDir = ""
        self._bgSceneUrl = ""
        #: 场景目标变了就 +1（URL 可能不变，QML 靠它触发**热切**而不是整页重载）
        self._bgSceneRev = 0
        self._sceneServer = None
        #: 实时渲染画不出来的场景（id 集合）—— 下次直接走提取画面，别再给用户黑屏
        self._weSceneFail = set()
        try:
            from core import wallpaper_engine as _we

            self._weInstall = _we.find_install()
        except Exception:
            self._weInstall = ""

    def _loadBgVideo(self) -> None:
        """启动时恢复视频背景路径；文件已不存在就丢弃（壁纸被卸载是常态）。"""
        if not self._bgVideo:
            return
        if not os.path.isfile(self._bgVideo):
            self._bgVideo = ""
            self._bgWeId = ""

    def _restoreBgScene(self) -> None:
        """启动时把「现场景实时渲染」接回来（端口是随机的，不能靠存 URL）。"""
        if not self._bgSceneDir or not self.webEngineAvailable:
            return
        if not os.path.isdir(self._bgSceneDir):
            self._bgSceneWeId = ""
            self._bgSceneDir = ""
            return
        pkg = os.path.join(self._bgSceneDir, "scene.pkg")
        override = ""
        try:
            from core import scene_patch

            # ★ 有视频贴图（Chromium 播不了 H.264）却还没转码时，**不能**先起服务：
            #   那样会先黑屏，健康检查还会把这张壁纸永久拉进失败名单。
            #   这里先转码，转完 worker 会自己回来调 _applySceneLive。
            if os.path.isfile(pkg) and scene_patch.find_video_texes(pkg):
                override = scene_patch.cached_patched_pkg(
                    self._bgSceneDir, _data_root(), self._bgSceneWeId, self._sceneProfile())
                if not override:
                    w = self._findWeWallpaper(self._bgSceneWeId)
                    if w is not None:
                        self._prepareScenePatch(w)
                    return
        except Exception:
            override = ""
        try:
            from core import scene_server

            if self._sceneServer is None:
                self._sceneServer = scene_server.SceneServer(scene_server.asset_dir())
            self._bgSceneUrl = self._sceneServer.start(self._bgSceneDir, override)
        except Exception as exc:
            self._bgSceneUrl = ""
            self.toast.emit("warn", "场景实时渲染启动失败，已退回静态背景：%s" % str(exc)[:60])

    @Slot(str)
    def sceneRenderFailed(self, reason: str):
        """实时渲染确认画不出画面（QML 侧健康检查报的）→ 自动退回提取画面。

        有些场景包的 WebWallGL 画布恒为纯黑，例如 3470764447。
        这时回退到提取画面，避免设置页显示黑底。
        """
        wid = self._bgSceneWeId
        if not wid:
            return
        self._weSceneFail.add(str(wid))
        try:
            self._saveUiSettings()
        except Exception:
            pass
        w = self._findWeWallpaper(wid)
        title = w.title if w is not None else wid
        self._dropSceneLive()
        self._bgVideo = ""
        self._bgImage = ""
        self._bgWeId = str(wid)
        self._saveUiSettings()
        self.videoBgChanged.emit()
        self.bgChanged.emit()
        self.toast.emit("warn", "「%s」实时渲染不出画面（%s），已退回提取画面" % (title[:20], reason))
        # 走老的提取路径把背景补上
        if w is not None:
            self._extractAsync(w)

    # ---------------------------------------------------------- 探测与清单

    @Property(bool, notify=weChanged)
    def weAvailable(self):
        """本机有没有可用的壁纸引擎安装。"""
        return bool(self._weInstall)

    @Property(str, notify=weChanged)
    def weInstallDir(self):
        return self._weInstall

    @Property(str, notify=weChanged)
    def weError(self):
        return self._weError

    def _we_scan_sync(self) -> list:
        """同步扫描一遍壁纸清单（调用方保证不在 UI 线程或能接受毫秒级）。"""
        from core import wallpaper_engine as _we

        install = _we.find_install()
        self._weInstall = install
        if not install:
            self._weItems = []
            self._weError = "未找到壁纸引擎安装"
            return []
        try:
            items = _we.scan(install, _we.workshop_dirs())
        except Exception as exc:      # 探测失败不该让设置页打不开
            self._weItems = []
            self._weError = "扫描失败：%s" % str(exc)[:80]
            return []
        self._weError = ""
        self._weItems = items
        self._flushPendingProps()
        return items

    @Slot(result="QVariantList")
    def weWallpapers(self):
        """壁纸清单（给 QML 选择器）。

        每个条目带 `previewUrl`（封面图的 file:/// URL，用于列表缩略图）——
        缩略图直接用封面图，不去拆包（拆包是选定时才做的重活）。
        """
        if not self._weScanned:
            self._we_scan_sync()
            self._weScanned = True
            self.weChanged.emit()
        out = []
        for w in self._weItems:
            preview_url = ""
            if w.preview and os.path.isfile(w.preview):
                preview_url = QUrl.fromLocalFile(w.preview).toString()
            out.append({
                "id": w.id,
                "title": w.title,
                "kind": w.kind,
                "previewUrl": preview_url,
                "source": w.source,
                "current": w.id == self._bgWeId,
            })
        return out

    @Slot(result="QVariantList")
    def refreshWallpapers(self):
        """重新扫描壁纸库，供设置页刷新使用。"""
        self._we_scan_sync()
        self._weScanned = True
        self.weChanged.emit()
        return self.weWallpapers()

    def _findWeWallpaper(self, wid: str):
        wid = str(wid or "").strip()
        if not wid:
            return None
        if not self._weScanned:
            self._we_scan_sync()
            self._weScanned = True
        for w in self._weItems:
            if w.id == wid:
                return w
        return None

    def _weRunning(self) -> bool:
        """壁纸引擎是否在跑。

        ★ 为了安全：WE 退出时会把整份 config.json 覆盖回去，这时我们写进去的
          属性值会静默丢失。所以它在跑就直说，别装作写成功了。
        """
        import subprocess
        import time

        # ★ 缓存 3 秒：tasklist 一次几十毫秒，而这个结果会被 QML 绑定反复读，
        #   不缓存会把界面拖卡（属性面板打开时尤其明显）。
        now = time.time()
        ts, cached = getattr(self, "_weRunCache", (0.0, False))
        if now - ts < 3.0:
            return cached

        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=4,
                                 creationflags=flags).stdout.lower()
            running = any(n in out for n in ("wallpaper32.exe", "wallpaper64.exe",
                                             "ui32.exe", "ui64.exe"))
        except Exception:
            running = False
        self._weRunCache = (now, running)
        return running

    @Slot(str, result="QVariantList")
    def weProps(self, wid: str):
        """这张壁纸自己声明的可调参数 + 它在壁纸引擎里被改过的值。

        每个条目附 ``override``（WE 里改过的值，没改过为空）与 ``effective``
        （界面显示的值）。这些值由 WE 自己的渲染器消费：改它们只影响**桌面**上的
        壁纸引擎，本工具的背景是提取出来的画面，不会跟着变 —— 界面上写明了这点。
        """
        w = self._findWeWallpaper(wid)
        if w is None:
            return []
        try:
            from core import wallpaper_engine as _we

            props = _we.read_properties(w)
        except Exception:
            return []
        wid = str(w.id)
        over = self._weOverrides(wid, w)
        pend = self._wePending.get(wid, {})
        for it in props:
            got = over.get(it["name"]) if it["name"] in over else None
            it["override"] = got
            it["hasOverride"] = got is not None
            it["effective"] = got if got is not None else it.get("value")
            if it["name"] in pend:      # 引擎在跑时改的：界面上先按用户改的显示
                it["pending"] = True
        return props

    @Slot(str, str, "QVariant", result="QVariantMap")
    def setWeProp(self, wid: str, name: str, value):
        """改壁纸自带的一个属性值，写进 WE 自己的 config.json。

        返回 ``{ok, msg}``。校验严格按 project.json 声明的类型/范围来：
        布尔、滑杆夹范围、下拉必须是声明的选项之一、颜色统一成 ``"r g b"``。
        """
        w = self._findWeWallpaper(wid)
        if w is None:
            return {"ok": False, "msg": "找不到这张壁纸"}
        if not self._weInstall:
            return {"ok": False, "msg": "没找到壁纸引擎安装"}
        try:
            from core import wallpaper_engine as _we

            props = _we.read_properties(w)
        except Exception:
            return {"ok": False, "msg": "读不到这张壁纸的参数"}
        spec = None
        for it in props:
            if it["name"] == name:
                spec = it
                break
        if spec is None:
            return {"ok": False, "msg": "这张壁纸没有这个参数"}

        ptype = spec["type"]
        if ptype == "bool":
            val = bool(value)
        elif ptype == "slider":
            try:
                f = float(value)
            except (TypeError, ValueError):
                return {"ok": False, "msg": "数值不对"}
            lo = spec.get("min", 0.0)
            hi = spec.get("max", 1.0)
            val = max(float(lo), min(float(hi), f))
            if isinstance(spec.get("value"), int):
                val = int(round(val))
        elif ptype == "combo":
            vals = [str(o.get("value")) for o in (spec.get("options") or [])]
            if str(value) not in vals:
                return {"ok": False, "msg": "不在可选值里"}
            base = spec.get("value")
            val = int(value) if isinstance(base, int) and not isinstance(base, bool) else str(value)
        elif ptype == "color":
            hexstr = str(value or "").strip().lstrip("#")
            raw = str(spec.get("value") or "1 1 1").split()
            try:
                if len(hexstr) == 6:
                    rgb = [int(hexstr[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
                else:
                    rgb = [max(0.0, min(1.0, float(v))) for v in raw[:3]]
            except ValueError:
                return {"ok": False, "msg": "颜色格式不对"}
            val = " ".join("%.6f" % c for c in rgb)
        else:
            val = str(value)[:200]

        # 已提取的视频包含渲染过的动画，调整场景属性无法改变它。
        # 修改当前场景壁纸的属性时先切到实时渲染，再应用属性。
        if (w.kind == "scene" and self.webEngineAvailable
                and str(w.id) == str(self._bgWeId)
                and str(w.id) != self._bgSceneWeId):
            switched = self._applySceneLive(w)
            if switched is not None:
                msg_extra = "（已切到实时渲染）"
            else:
                msg_extra = ""
        else:
            msg_extra = ""

        ok, msg = _we.write_wproperty(w, self._weInstall, name, val)
        wid = str(w.id)
        live = wid == self._bgSceneWeId and bool(self._bgSceneUrl)
        if w.kind == "scene" and (ok or live):
            self._weSceneProps.setdefault(wid, {})[name] = val
        if ok:
            if self._weRunning():
                # 引擎在跑：它退出时会用内存里的旧值覆盖文件 → 记下来，等它关掉再重写
                self._wePending.setdefault(wid, {})[name] = val
                msg = "已记录；壁纸引擎正在运行，退出它之后本工具会再写一次"
            else:
                pending = self._wePending.get(wid, {})
                pending.pop(name, None)
                if not pending:
                    self._wePending.pop(wid, None)
                msg = "已写入壁纸引擎（桌面壁纸下次启动生效）"
            if not live and not self._wePending.get(wid):
                self.toast.emit("info", msg)
        if ok or live:
            self._saveUiSettings()
        if live:
            self.wePropApplied.emit(str(name), val)
            message = "已生效（实时渲染，画面已更新）" + msg_extra
            if not ok:
                message += "。Wallpaper Engine 写入失败：" + msg
            return {"ok": True, "msg": message}
        return {"ok": ok, "msg": msg}

    def _flushPendingProps(self) -> None:
        """把「引擎在跑时改了」的属性重写一遍。只在引擎没跑时做。"""
        if not getattr(self, "_wePending", None) or not self._weInstall:
            return
        if self._weRunning():
            return
        try:
            from core import wallpaper_engine as _we
        except Exception:
            return
        changed = False
        for wid, vals in list(self._wePending.items()):
            w = None
            for it in self._weItems:
                if it.id == wid:
                    w = it
                    break
            if w is None:
                continue
            ok_all = True
            for name, val in list(vals.items()):
                ok, _msg = _we.write_wproperty(w, self._weInstall, name, val)
                ok_all = ok_all and ok
            if ok_all:
                self._wePending.pop(wid, None)
                changed = True
        if changed:
            self._saveUiSettings()
            self.toast.emit("info", "壁纸引擎里的属性已重新写入")

    @Property(bool, notify=weChanged)
    def weEngineRunning(self):
        """壁纸引擎是否在运行（界面据此提示"改动会被它退出时覆盖"）。"""
        return self._weRunning()

    # ---------------------------------------------------------- 场景实时渲染

    def _screenHeight(self) -> int:
        """转码目标高度 = 显示器高度（超过屏幕没有意义，也不降画质）。"""
        try:
            from PySide6.QtGui import QGuiApplication

            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                h = int(scr.size().height() * scr.devicePixelRatio())
                if h >= 480:
                    return min(h, 2160)
        except Exception:
            pass
        return 1080

    def _sceneProfile(self) -> str:
        try:
            from core import scene_patch
            from core import wallpaper_engine as _we

            return scene_patch.profile_for(self._screenHeight(), _we.find_ffmpeg() or "")
        except Exception:
            return ""

    @Property(bool, constant=True)
    def webEngineAvailable(self):
        """本机能不能实时渲染场景（装了 PySide6-WebEngine 且已在启动时初始化）。"""
        return os.environ.get("AELOC_WEBENGINE") == "1"

    @Property(str, notify=videoBgChanged)
    def bgSceneUrl(self):
        """场景渲染页地址；空 = 当前不是实时渲染。"""
        return self._bgSceneUrl or ""

    @Property(int, notify=videoBgChanged)
    def bgSceneRev(self):
        """场景目标版本号：换壁纸 / 换补丁包时 +1。"""
        return self._bgSceneRev

    @Property(str, notify=videoBgChanged)
    def bgSceneWeId(self):
        """当前实时渲染的是哪张壁纸（空 = 没在实时渲染）。界面用它标「实时/提取」。"""
        return self._bgSceneWeId or ""

    @Slot(result="QVariantMap")
    def sceneInitialProps(self):
        """当前场景的外部配置和 AeLocalizer 保存的属性。"""
        wid = self._bgSceneWeId
        if not wid:
            return {}
        return self._weOverrides(wid, self._findWeWallpaper(wid) if self._weInstall else None)

    def _weOverrides(self, wid, wallpaper):
        overrides = {}
        if wallpaper is not None and self._weInstall:
            from core import wallpaper_engine as _we

            overrides.update(_we.read_wproperty_overrides(wallpaper, self._weInstall))
        overrides.update(self._wePending.get(wid, {}))
        overrides.update(self._weSceneProps.get(wid, {}))
        return overrides

    # ---------------------------------------------------------- 每张壁纸的画面参数

    #: 允许调的键与范围（越界一律夹回，绝不让设置文件写坏渲染）
    _TUNE_RANGES = {"blur": (0, 60), "brightness": (50, 160),
                    "saturation": (0, 200), "contrast": (50, 160)}

    def _defaultTune(self):
        """没调过参数时：跟当前全局值一致 —— 面板上看到的永远是"现在真实在用的"。"""
        return {
            "blur": self._bgBlur,
            "brightness": self._bgBrightness,
            "saturation": self._bgSaturation,
            "contrast": self._bgContrast,
            "fit": self._bgFit,
        }

    @Slot(str, result="QVariantMap")
    def weTune(self, wid: str):
        """某张壁纸自己的画面参数（没调过则返回当前全局值）。"""
        wid = str(wid or "").strip()
        t = getattr(self, "_weTune", {}).get(wid)
        if not isinstance(t, dict):
            return self._defaultTune()
        out = self._defaultTune()
        out.update({k: v for k, v in t.items() if k in out})
        return out

    @Slot(str, str, "QVariant", result="QVariantMap")
    def setWeTune(self, wid: str, key: str, value):
        """改这张壁纸的一个参数；它正在用时立刻生效，返回改完的整份参数。"""
        wid = str(wid or "").strip()
        key = str(key or "").strip()
        if not wid:
            return self._defaultTune()
        tune = dict(self.weTune(wid))
        if key == "fit":
            fv = str(value or "").strip()
            if fv in ("cover", "contain"):
                tune["fit"] = fv
        elif key in self._TUNE_RANGES:
            lo, hi = self._TUNE_RANGES[key]
            try:
                iv = max(lo, min(hi, int(round(float(value)))))
            except (TypeError, ValueError):
                iv = tune.get(key, lo)
            tune[key] = iv
        else:
            return tune
        self._weTune[wid] = tune
        if wid and wid == self._bgWeId:
            self._applyTune(tune)
        self._saveUiSettings()
        return tune

    def _applyTune(self, tune: dict) -> None:
        """把一份参数灌进"当前画面"，并通知界面重画。"""
        self._bgBlur = int(tune.get("blur", self._bgBlur))
        self._bgBrightness = int(tune.get("brightness", self._bgBrightness))
        self._bgSaturation = int(tune.get("saturation", self._bgSaturation))
        self._bgContrast = int(tune.get("contrast", self._bgContrast))
        fit = str(tune.get("fit") or self._bgFit)
        if fit in ("cover", "contain"):
            self._bgFit = fit
        self.videoBgChanged.emit()

    def _restoreTune(self, wid: str) -> None:
        """把这张壁纸上次调的那套参数灌回来（没调过就保持现状）。"""
        t = getattr(self, "_weTune", {}).get(str(wid or ""))
        if isinstance(t, dict):
            self._applyTune(t)

    def _snapshotTune(self) -> None:
        """换壁纸前，把现在这套参数记到"正在用的那张"名下。"""
        wid = getattr(self, "_bgWeId", "")
        if not wid:
            return
        self._weTune[wid] = self._defaultTune()

    # ---------------------------------------------------------- 应用壁纸

    @Slot(str, result=str)
    def applyWallpaper(self, wid: str):
        """把某张壁纸设为背景。同步返回一句给 toast 的话，重活在后台线程。

        ★ 返回值只描述「已开始」，最终结果通过 toast 通知 —— 拆包可能几秒。
        ★ 用基类的 `_run`（而不是自己 new Worker）：它已经把「忙碌态、跨线程
          调度到 owner 线程、失败提示」统一处理过，重复实现只会多一处会漂的路径。
        """
        wid = str(wid or "").strip()
        if not wid:
            return "未指定壁纸"
        if self.busy:
            return "当前有任务在跑，稍后再试"
        if not self._weItems:
            self._we_scan_sync()
        target = None
        for w in self._weItems:
            if w.id == wid:
                target = w
                break
        if target is None:
            return "找不到这张壁纸（可能已移除）"

        # 有可用的 WebEngine 时实时渲染，场景属性可热更新。
        # 渲染不可用时回退到提取画面。
        if target.kind == "scene":
            live = self._applySceneLive(target)
            if live is not None:
                return live

        self._extractAsync(target)
        return "正在应用「%s」…" % target.title

    def _extractAsync(self, target) -> None:
        """老路：拆包取画面当背景（实时渲染不可用或渲染失败时的降级）。"""
        wid = str(target.id)

        def work():
            from core import wallpaper_engine as _we

            cache = _we.default_cache_dir(_data_root())
            res = _we.extract(target, cache)
            if res is None or not res.ok:
                return {"ok": False, "msg": "这张壁纸取不到画面（可能只含三维场景）"}
            return {
                "ok": True,
                "path": res.path,
                "kind": res.kind,
                "preview": bool(res.from_preview),
                "title": target.title,
            }

        def done(res):
            if not res or not res.get("ok"):
                self.toast.emit("error", (res or {}).get("msg") or "应用失败")
                return
            self._finish_apply(res["path"], res["kind"], res.get("preview", False),
                               res.get("title", ""), wid)

        self._run(work, done, _busy_text="正在准备壁纸…")

    def _applySceneLive(self, w) -> "str | None":
        """把一张场景壁纸切成实时渲染。返回 None 表示"不可用，请走提取画面"。

        ★ 用户显式选这张壁纸时总会重试一次：失败名单里可能记着**补丁包还没生成时**
          的旧结论（那时候必然黑屏），不清掉的话这张壁纸永远只能走提取画面。
          真失败的话 QML 的健康检查会把它重新记回去。
        """
        self._weSceneFail.discard(str(w.id))
        if not self.webEngineAvailable:
            return None
        # ★ Chromium（QtWebEngine）不带 H.264：场景里那些"视频贴图"必须转成 VP9
        #   才画得出来（core/scene_patch.py）。第一次选这张壁纸要转一次，之后走缓存。
        override = ""
        pkg = os.path.join(w.dir, "scene.pkg")
        try:
            from core import scene_patch

            if os.path.isfile(pkg) and scene_patch.find_video_texes(pkg):
                override = scene_patch.cached_patched_pkg(w.dir, _data_root(), str(w.id),
                                                          self._sceneProfile())
                if not override:
                    self._prepareScenePatch(w)
                    return "正在为「%s」准备实时渲染（转换视频贴图，一次性）…" % w.title[:20]
        except Exception:
            override = ""
        try:
            from core import scene_server

            if self._sceneServer is None:
                self._sceneServer = scene_server.SceneServer(scene_server.asset_dir())
            url = self._sceneServer.start(w.dir, override)
        except Exception as exc:
            self.toast.emit("warn", "实时渲染起不来，改用提取画面：%s" % str(exc)[:60])
            return None
        self._snapshotTune()
        self._bgImage = ""
        self._bgVideo = ""
        self._bgSceneWeId = str(w.id)
        self._bgSceneDir = w.dir
        self._bgWeId = str(w.id)
        self._bgSceneUrl = url
        self._bgSceneRev = self._bgSceneRev + 1
        self._restoreTune(w.id)
        self._saveUiSettings()
        self.videoBgChanged.emit()
        self.bgChanged.emit()
        self.toast.emit("info", "「%s」实时渲染中 —— 参数改完立刻生效" % w.title[:24])
        return "正在实时渲染「%s」…" % w.title[:24]

    def _prepareScenePatch(self, w) -> None:
        """首次实时渲染前，把包里的 H.264 视频贴图转成 VP9 并缓存（一次性）。"""
        pkg = os.path.join(w.dir, "scene.pkg")

        def work():
            from core import scene_patch
            from core import wallpaper_engine as _we

            ff = _we.find_ffmpeg()
            if not ff:
                return {"ok": False, "msg": "找不到 ffmpeg"}
            out_dir = scene_patch.cache_dir(_data_root())
            out = os.path.join(out_dir, "%s.pkg" % w.id)
            height = self._screenHeight()
            ok, msg = scene_patch.patch_scene_pkg(pkg, out, ff, target_height=height)
            if ok:
                st = os.stat(pkg)
                with open(out + ".json", "w", encoding="utf-8") as f:
                    json.dump({"mtime": st.st_mtime, "size": st.st_size,
                               "profile": self._sceneProfile(), "height": height}, f)
            return {"ok": ok, "msg": msg}

        def done(res):
            if res and res.get("ok"):
                self.toast.emit("info", "视频贴图已转码，正在开启实时渲染…")
                self._applySceneLive(w)
            else:
                self.toast.emit("warn", "视频贴图转码失败（%s），已退回提取画面"
                                % ((res or {}).get("msg") or "未知"))
                self._extractAsync(w)

        self._run(work, done, _busy_text="正在转换视频贴图（首次约半分钟）…")

    def _dropSceneLive(self) -> None:
        """停止实时渲染（换成图片/视频/清除背景时都要调）。"""
        if self._sceneServer is not None:
            try:
                self._sceneServer.stop()
            except Exception:
                pass
        self._bgSceneWeId = ""
        self._bgSceneDir = ""
        self._bgSceneUrl = ""

    def _finish_apply(self, path: str, kind: str, from_preview: bool,
                      title: str, we_id: str = "") -> None:
        """把提取结果落成真正的背景配置。

        ★ we_id 必须记下来（2026-11-16 修）：以前视频分支把它清成 "",
          于是壁纸选择器里「使用中」永远不亮 —— 从场景壁纸剥出 mp4 的正是视频分支。
        """
        # ★ 提取画面这条路：先关掉可能正在跑的实时渲染
        self._dropSceneLive()
        # ★ 换壁纸前，先把当前这套画面参数记到"上一张"名下，切回来能还原
        self._snapshotTune()
        if kind == "video":
            self._bgImage = ""
            self._bgVideo = path
            self._bgWeId = we_id
            self._restoreTune(we_id)
            self._saveUiSettings()
            self.videoBgChanged.emit()
            self.bgChanged.emit()
            self.toast.emit("info", "背景已设为视频壁纸「%s」（实时播放）" % title[:24])
            return

        # 图片：复制进 ui_bg（与既有自定义背景同一条路，防源文件移动）
        try:
            bg_dir = os.path.join(_data_root(), "ui_bg")
            os.makedirs(bg_dir, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".png"
            dst = os.path.join(bg_dir, "bg" + ext)
            for old in os.listdir(bg_dir):
                try:
                    os.unlink(os.path.join(bg_dir, old))
                except OSError:
                    pass
            shutil.copyfile(path, dst)
        except Exception as exc:
            self.toast.emit("error", "背景图保存失败：%s" % str(exc)[:80])
            return
        self._bgVideo = ""
        self._bgImage = dst
        self._bgWeId = we_id
        self._restoreTune(we_id)
        self._saveUiSettings()
        self.videoBgChanged.emit()
        self.bgChanged.emit()
        if from_preview:
            self.toast.emit("warn", "「%s」的三维场景无法截图，已用封面图代替" % title[:24])
        else:
            self.toast.emit("info", "背景已设为壁纸「%s」" % title[:24])

    @Slot(result=str)
    def clearWallpaperBackground(self):
        """清掉视频背景 / 场景实时渲染（图片背景走既有的 clearBgImage）。"""
        had_scene = bool(self._bgSceneUrl or self._bgSceneDir)
        self._dropSceneLive()
        if not self._bgVideo and not had_scene:
            return "当前没有视频背景"
        self._bgVideo = ""
        self._bgWeId = ""
        self._saveUiSettings()
        self.videoBgChanged.emit()
        self.bgChanged.emit()
        return "已清除视频背景"

    # ---------------------------------------------------------- 画面调节

    @Property(int, notify=videoBgChanged)
    def bgBlur(self):
        return self._bgBlur

    @Slot(int)
    def setBgBlur(self, v: int):
        v = max(0, min(60, int(v)))
        if v == self._bgBlur:
            return
        self._bgBlur = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    @Property(int, notify=videoBgChanged)
    def bgBrightness(self):
        return self._bgBrightness

    @Slot(int)
    def setBgBrightness(self, v: int):
        v = max(50, min(160, int(v)))
        if v == self._bgBrightness:
            return
        self._bgBrightness = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    @Property(int, notify=videoBgChanged)
    def bgSaturation(self):
        return self._bgSaturation

    @Slot(int)
    def setBgSaturation(self, v: int):
        v = max(0, min(200, int(v)))
        if v == self._bgSaturation:
            return
        self._bgSaturation = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    @Property(int, notify=videoBgChanged)
    def bgContrast(self):
        return self._bgContrast

    @Slot(int)
    def setBgContrast(self, v: int):
        v = max(50, min(160, int(v)))
        if v == self._bgContrast:
            return
        self._bgContrast = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    @Property(str, notify=videoBgChanged)
    def bgFit(self):
        return self._bgFit
    @Slot(str)
    def setBgFit(self, v: str):
        v = str(v or "").strip()
        if v not in ("cover", "contain") or v == self._bgFit:
            return
        self._bgFit = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    # ---- 面板磨砂 ----

    @Property(int, notify=videoBgChanged)
    def glassBlur(self):
        """面板（卡片/侧栏/标题栏）自己的背景磨砂档位，0 = 关。"""
        return self._glassBlur

    @Slot(int)
    def setGlassBlur(self, v: int):
        v = max(0, min(60, int(v)))
        if v == self._glassBlur:
            return
        self._glassBlur = v
        self._saveUiSettings()
        self.videoBgChanged.emit()

    #: 预设：**按「文字密集的工具」这个场景**定的，不是按观感定的。
    #: 参考实现那套预设面向网页聊天（壁纸是主角），照搬过来会让本软件的
    #: 词典表、参数行、设置表单更难读。这里四档的共同目标是
    #: 「壁纸看得出来，但永远不妨碍读字」：
    #:   blur 压掉细节争夺注意力；bright 让暗图提亮/亮图压暗到不刺眼；
    #:   scrim 是整窗纱罩；plate 是内容卡片的不透明度。
    BG_PRESETS = {
        # 清晰：壁纸原样，只在整窗压一层很薄的纱
        "clear":   {"blur": 0,  "brightness": 100, "saturation": 100, "contrast": 100,
                    "scrim": 0.12, "plate": 0.80},
        # 柔和：轻度磨砂 + 轻微降饱和，壁纸还在但不抢注意力
        "soft":    {"blur": 12, "brightness": 94,  "saturation": 92,  "contrast": 100,
                    "scrim": 0.20, "plate": 0.85},
        # 磨砂：明显磨砂，壁纸退成色块背景，长时间盯着不累
        "frosted": {"blur": 34, "brightness": 88,  "saturation": 88,  "contrast": 98,
                    "scrim": 0.26, "plate": 0.88},
        # 阅读优先：以读字为第一目标，壁纸只留氛围
        "reading": {"blur": 24, "brightness": 80,  "saturation": 72,  "contrast": 96,
                    "scrim": 0.46, "plate": 0.95},
    }

    @Slot(str)
    def applyBgPreset(self, name: str):
        """套用一档画面预设（同时调磨砂/亮度/饱和/对比/纱罩/底板）。"""
        p = self.BG_PRESETS.get(str(name or "").strip())
        if not p:
            return
        self._bgBlur = p["blur"]
        self._bgBrightness = p["brightness"]
        self._bgSaturation = p["saturation"]
        self._bgContrast = p["contrast"]
        self._bgDim = p["scrim"]
        self._plate = p["plate"]
        self._saveUiSettings()
        self.videoBgChanged.emit()
        self.appearanceChanged.emit()
        self.bgChanged.emit()

    @Property(str, notify=videoBgChanged)
    def currentBgPreset(self):
        """当前参数是否正好等于某个预设 —— 用于把那一档标成选中。

        ★★ 2026-11-17 修：这里原来是 ``@Slot(result=str)``，QML 写
          ``current: backend.currentBgPreset()``。函数调用在绑定里**不会**因为
          后端属性变化而重算（Python 侧没有可监听的 notify），于是点预设
          "效果生效了但高亮不动"。改成带 notify 的 Property，QML 用属性访问。
        """
        for name, p in self.BG_PRESETS.items():
            if (self._bgBlur == p["blur"]
                    and self._bgBrightness == p["brightness"]
                    and self._bgSaturation == p["saturation"]
                    and self._bgContrast == p["contrast"]):
                return name
        return ""

    # ---------------------------------------------------------- 属性

    @Property(str, notify=videoBgChanged)
    def bgVideo(self):
        """视频背景的 file:/// URL；无则空串。"""
        if not self._bgVideo:
            return ""
        if not os.path.isfile(self._bgVideo):
            return ""
        return QUrl.fromLocalFile(self._bgVideo).toString()

    @Property(bool, notify=videoBgChanged)
    def hasVideoBg(self):
        return bool(self._bgVideo) and os.path.isfile(self._bgVideo)
