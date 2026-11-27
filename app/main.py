"""应用入口：启动 QML 界面。

用法：
  python -m app.main            # 正常启动
  python -m app.main --snapshot out/ui.png   # 无头渲染一张截图（自检用）
"""
from __future__ import annotations

import os
import sys
import traceback

#: 打包版没有控制台 —— 闪退时什么都留不下。这里把 stderr / 原生崩溃栈 /
#: 关键阶段 / 已加载的非系统 DLL 全部落到 <DATA_ROOT>/run.log，线上排障靠它。
_LOG_FILE = None
_LOG_PATH = ""


def _data_root() -> str:
    root = os.environ.get("AELOC_DATA_ROOT", "").strip()
    if root:
        return root
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AeLocalizer")


def _setup_diagnostics() -> None:
    """冻结版或 AELOC_RUN_LOG=1 时，把诊断信息写进 run.log。"""
    global _LOG_FILE, _LOG_PATH
    try:
        if not getattr(sys, "frozen", False) and os.environ.get("AELOC_RUN_LOG") != "1":
            return
        root = _data_root()
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, "run.log")
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 2 * 1024 * 1024:
                bak = path + ".1"
                if os.path.isfile(bak):
                    os.remove(bak)
                os.replace(path, bak)
        except OSError:
            pass
        f = open(path, "a", encoding="utf-8", buffering=1)
        _LOG_FILE, _LOG_PATH = f, path
        sys.stderr = f
        sys.stdout = f
        import faulthandler
        try:
            faulthandler.enable(file=f, all_threads=True)
        except Exception:
            pass
        _log("=" * 60)
        _log("启动  frozen=%s  argv=%r" % (bool(getattr(sys, "frozen", False)), sys.argv[1:]))
        try:
            from core.version import APP_VERSION
            _log("版本 %s  python %s" % (APP_VERSION, sys.version.split()[0]))
        except Exception:
            pass
        _log("数据目录 %s" % root)
        _log_modules()
    except Exception:
        pass


def _log(msg: str) -> None:
    global _LOG_FILE
    if _LOG_FILE is None:
        return
    try:
        import time as _t
        _LOG_FILE.write("[%s] %s\n" % (_t.strftime("%H:%M:%S"), msg))
        _LOG_FILE.flush()
    except Exception:
        pass


def _log_modules() -> None:
    """记录进程里加载的**非系统** DLL —— 第三方叠加层（游戏加加/Discord/RTSS…）
    注入是打包版闪退的常见元凶，这里留证据。"""
    try:
        import ctypes
        from ctypes import wintypes as wt

        # EnumProcessModules 在 psapi.dll；新系统里 kernel32 也叫 K32EnumProcessModules
        enum = None
        for dll, name in (("psapi", "EnumProcessModules"), ("kernel32", "K32EnumProcessModules")):
            try:
                enum = getattr(ctypes.WinDLL(dll, use_last_error=True), name)
                break
            except (OSError, AttributeError):
                continue
        if enum is None:
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wt.HANDLE
        enum.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                         ctypes.POINTER(wt.DWORD)]
        enum.restype = wt.BOOL
        k32.GetModuleFileNameW.argtypes = [wt.HMODULE, wt.LPWSTR, wt.DWORD]
        k32.GetModuleFileNameW.restype = wt.DWORD
        h = k32.GetCurrentProcess()
        arr = (ctypes.c_void_p * 1024)()
        need = wt.DWORD()
        if not enum(h, arr, ctypes.sizeof(arr), ctypes.byref(need)):
            _log("EnumProcessModules 失败 err=%d" % ctypes.get_last_error())
            return
        n = min(1024, need.value // ctypes.sizeof(ctypes.c_void_p))
        buf = ctypes.create_unicode_buffer(512)
        rows = []
        for i in range(n):
            if k32.GetModuleFileNameW(arr[i], buf, 512):
                fp = buf.value
                low = fp.lower()
                if fp and not low.startswith("c:\\windows\\"):
                    rows.append(fp)
        _log("非系统模块 %d 个：" % len(rows))
        for fp in rows[:40]:
            _log("  " + fp)
    except Exception:
        pass

def _base_dir() -> str:
    """资源根目录。

    打包后（PyInstaller）资源在 sys._MEIPASS 下；
    源码运行时就是仓库根目录。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HERE = os.path.join(_base_dir(), "app")
ROOT = _base_dir()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 打包版把固定版本的 frida 放在 .runtime-deps。守卫从这里加载，
# 主进程的自检也从同一位置读取版本。
_DEPS_DIR = os.path.join(ROOT, ".runtime-deps")
if os.path.isdir(_DEPS_DIR) and _DEPS_DIR not in sys.path:
    sys.path.insert(0, _DEPS_DIR)

QML_DIR = os.path.join(HERE, "qml")


def _prepare_web_engine() -> bool:
    """Prepare shared graphics before creating the GUI. Scene selection loads WebEngine."""
    # 兼容模式：三方叠加层导致闪退时，可用 AELOC_NO_WEBENGINE=1 跳过 WebEngine
    if os.environ.get("AELOC_NO_WEBENGINE", "") == "1":
        os.environ["AELOC_WEBENGINE"] = "0"
        return False
    try:
        from importlib.util import find_spec
        from PySide6.QtCore import QCoreApplication, Qt

        if find_spec("PySide6.QtWebEngineQuick") is None:
            os.environ["AELOC_WEBENGINE"] = "0"
            return False
        # ★ 2026-11-18 性能：把 Chromium 里与"把画面搬到 QML 里"相关的开关打开。
        #   全是不改画质的性能项：
        #   零拷贝合成（视频帧直接进合成器）、GPU 光栅化、忽略驱动黑名单
        #   （这台是 RTX 4070，实际支持得比黑名单判定的多）。
        base = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        want = ("--enable-zero-copy --enable-gpu-rasterization "
                "--ignore-gpu-blocklist --enable-accelerated-video-decode")
        if "--enable-zero-copy" not in base:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (base + " " + want).strip()
        # With the pinned Qt 6.12, QtWebEngineQuick.initialize() is a no-op.
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        os.environ["AELOC_WEBENGINE"] = "1"
        return True
    except Exception:
        os.environ["AELOC_WEBENGINE"] = "0"
        return False


def _add_import_path(engine) -> None:
    """把 qml/ 目录加进导入路径，让 `import AeLocalizer` 能找到 Theme 单例。"""
    engine.addImportPath(QML_DIR)


def _find_qml_item(item, name: str, depth: int = 0):
    """在 QML 对象树里按类型名找子项（QML 复合类型的 className 带 QML 名）。"""
    if depth > 16 or item is None:
        return None
    try:
        if name in item.metaObject().className():
            return item
    except Exception:
        return None
    try:
        kids = item.childItems()
    except Exception:
        kids = []
    for c in kids:
        r = _find_qml_item(c, name, depth + 1)
        if r is not None:
            return r
    return None


def _snapshot(path: str, index: int = 0, wait_ms: int = 2500, scan: bool = False,
              tab: int = -1) -> int:
    """无头渲染：加载界面、切到指定页/工作台 Tab、截图保存。"""
    from PySide6.QtCore import QTimer, QUrl, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow

    # 无头截图：软件渲染，场景实时渲染一律关掉（Chromium 起不来也不该起）
    os.environ["AELOC_WEBENGINE"] = "0"
    app = QGuiApplication(sys.argv)
    if sys.platform == "win32" and app.platformName() == "offscreen":
        from PySide6.QtGui import QFontDatabase

        # The offscreen font loader skips Windows TrueType collections.
        if not QFontDatabase.hasFamily("Microsoft YaHei UI"):
            font_dir = os.path.join(os.environ["SystemRoot"], "Fonts")
            for filename in ("msyh.ttc", "msyhbd.ttc"):
                QFontDatabase.addApplicationFont(os.path.join(font_dir, filename))
    QQuickWindow.setSceneGraphBackend("software")

    from app.backend import AppBackend

    backend = AppBackend()
    app.aboutToQuit.connect(backend.shutdown)
    backend.exitReady.connect(app.quit, Qt.QueuedConnection)
    engine = QQmlApplicationEngine()
    _add_import_path(engine)
    engine.rootContext().setContextProperty("appBackend", backend)
    qml = os.path.join(HERE, "qml", "main.qml")
    engine.load(QUrl.fromLocalFile(qml))
    if not engine.rootObjects():
        print("[错误] QML 加载失败", file=sys.stderr)
        return 1

    win = engine.rootObjects()[0]
    win.setProperty("currentIndex", index)

    if tab >= 0:
        # 侧边栏五页(2026-11-01 加设置页),--tab 直接设 pageIndex
        win.setProperty("pageIndex", tab)

    if scan:
        # 同步扫描（截图模式允许阻塞）
        from core.inventory import build_inventory
        from core.effects import load_effect_names, merge_into_store

        inv = build_inventory(backend._root, backend._version)
        backend._inv = inv
        # 效果名表：优先用仓库内自带的样例，其次跳过
        eff = os.path.join(ROOT, "data", "effect_names.json")
        table = load_effect_names(eff)
        if table:
            merge_into_store(backend._store, table)
        backend._status = "扫描完成：%d 组 / %d 文件" % (len(inv.groups), inv.scanned_files)
        for g in inv.groups[:6]:
            g.selected = True
        backend._syncGroupModel(reset=True)
        backend.inventoryChanged.emit()
        backend.stateChanged.emit()

        if "--demo-ai" in sys.argv:
            # 造一批草稿，验证草稿区渲染
            backend._aiDraft = [
                {"en": "S_Glow", "zh": "发光", "status": "ok"},
                {"en": "S_Blur", "zh": "模糊", "status": "ok"},
                {"en": "S_HueSatBright", "zh": "色相与饱和度", "status": "ok"},
                {"en": "S_ClampChroma", "zh": "色度限制", "status": "ok"},
                {"en": "S_Transition", "zh": "转场", "status": "ok"},
                {"en": "S_EdgeFlash", "zh": "边缘闪光", "status": "ok"},
                {"en": "S_MathOps", "zh": "数学运算", "status": "ok"},
                {"en": "S_MatteOps", "zh": "遮罩运算", "status": "ok"},
            ]
            backend._aiDraftSummary = "通过 8 · 拒绝 0 · 缓存命中 0 · 花费 $0.0021"
            backend._aiProgress = 100
            backend._aiCost = 0.0021
            backend._aiProgressText = "完成"
            backend.aiDraftChanged.emit()
            backend.aiProgressChanged.emit()

        wait_ms = max(wait_ms, 1200)

    def shoot():
        img = win.grabWindow()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        img.save(path)
        print("[OK] 截图已保存 %s  (%dx%d)" % (path, img.width(), img.height()))
        app.quit()

    QTimer.singleShot(wait_ms, shoot)
    return app.exec()


def main() -> int:
    # 守卫子进程由 engine 重定向 stdout/stderr 到引擎日志，这里不要再抢成 run.log
    if "--run-guard" not in sys.argv[1:] and sys.argv[1:] != ["--settings-workspace"]:
        _setup_diagnostics()
    try:
        return _main_inner()
    except SystemExit:
        raise
    except BaseException:
        _log("未捕获异常：\n" + traceback.format_exc())
        raise
    finally:
        _log("退出")


def _run_guard(argv) -> int:
    """打包版专用：用本 exe 当解释器跑守卫脚本。

    ★ 为什么需要（2026-11-19 打包验收）：core/engine.py 启动守卫用的是
      `sys.executable`。源码版那是 python.exe；**打包版那是 AeLocalizer.exe** ——
      直接 spawn 等于再开一个 GUI。这里给它一个自执行入口：
      `AeLocalizer.exe --run-guard <脚本> [参数...]`。
    """
    if not argv:
        _log("--run-guard 缺少脚本路径")
        return 2
    script = os.path.abspath(argv[0])
    rest = list(argv[1:])
    if not os.path.isfile(script):
        _log("--run-guard 脚本不存在：%s" % script)
        return 2
    # 守卫用项目自带的 frida 17.26.3（.runtime-deps），不动系统依赖
    deps = os.path.join(ROOT, ".runtime-deps")
    if os.path.isdir(deps) and deps not in sys.path:
        sys.path.insert(0, deps)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    # run_path 对单个 .py 文件不会像 python script.py 那样加入脚本目录。
    # 部署后的三个通道都从这里导入守卫旁边的模块。
    sys.path.insert(0, os.path.dirname(script))
    _log("run-guard: %s %r" % (script, rest))
    import runpy
    sys.argv = [script] + rest
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        return int(getattr(exc, "code", 0) or 0)
    except BaseException:
        _log("守卫脚本异常：\n" + traceback.format_exc())
        return 1
    return 0


def _self_check() -> int:
    """`--self-check`：不打界面，把关键依赖打出来（排障/虚拟机验收用）。"""
    import platform

    out = []
    try:
        from core.version import APP_VERSION
        out.append("版本 %s" % APP_VERSION)
    except Exception as exc:
        out.append("版本读取失败：%s" % exc)
    out.append("frozen=%s python=%s %s" % (bool(getattr(sys, "frozen", False)),
                                           platform.python_version(), platform.platform()))
    out.append("ROOT=%s" % ROOT)
    out.append("数据目录=%s" % _data_root())
    try:
        import frida

        out.append("frida %s  %s" % (getattr(frida, "__version__", "?"), frida.__file__))
    except Exception as exc:
        out.append("frida 不可用：%s  ← 守卫无法加载实时汉化依赖" % exc)
    try:
        from core import engine

        gp, reason = engine.find_guard()
        out.append("守卫脚本=%s" % (gp or ("未找到：" + reason)))
    except Exception as exc:
        out.append("守卫检查失败：%s" % exc)
    try:
        import importlib.util

        spec = importlib.util.find_spec("PySide6.QtWebEngineQuick")
        out.append("WebEngine=%s" % ("可用" if spec else "未安装（场景壁纸退回提取画面）"))
    except Exception as exc:
        out.append("WebEngine 检查失败：%s" % exc)
    # ---- 能不能汉化的关键：AE 在不在跑、这个构建认不认、守卫部署了没 ----
    procs = []
    try:
        from core import proc as _proc

        procs = _proc.list_ae_processes()
        if not procs:
            out.append("AE：未检测到运行中的 AfterFX.exe（汉化必须 AE 开着）")
        for pr in procs[:4]:
            ver = str(pr.get("fileVersion") or "")
            verdict = "未知"
            try:
                from core import adapter as _adapter

                verdict = ("已适配（能实时写入）" if _adapter.is_supported(ver, data_root=_data_root())
                           else "未适配（此构建不允许实时写入）")
            except Exception as exc:
                verdict = "判定失败：%s" % str(exc)[:60]
            extra = ""
            try:
                msg = _proc.elevation_mismatch(int(pr.get("pid") or 0))
                if msg:
                    extra = "  ⚠ " + str(msg)
            except Exception:
                pass
            out.append("AE：pid=%s %s v%s → %s%s"
                       % (pr.get("pid"), pr.get("image"), ver or "?", verdict, extra))
    except Exception as exc:
        out.append("AE 检测失败：%s" % str(exc)[:80])

    engine_running = False
    try:
        from core import engine as _engine

        engine_running = _engine.engine_running()
        out.append("引擎进程=%s" % ("运行中" if engine_running else "未运行 ← 效果名/参数名都不会变"))
    except Exception as exc:
        out.append("引擎状态检查失败：%s" % str(exc)[:80])

    if engine_running:
        try:
            from core.effect_broker import BrokerClient

            host = _engine.control_status().get("host")
            if not host and len(procs) == 1:
                pr = procs[0]
                host = {"pid": pr.get("pid"), "ctime": pr.get("creation_time"),
                        "image": pr.get("image")}
            effect = BrokerClient(_data_root()).call("ping", host=host)
            out.append("效果名通道=守卫已响应，挂钩%s"
                       % ("已运行（AE PID %s）" % effect["pid"]
                          if effect["running"] else "未启用"))
        except Exception as exc:
            out.append("效果名通道=守卫未响应：%s" % str(exc)[:140])
    else:
        out.append("效果名通道=等待守卫启动")

    try:
        dep = os.path.join(_data_root(), "guard_runtime")
        out.append("守卫部署=%s 存在=%s" % (dep, os.path.isdir(dep)))
        if os.path.isdir(dep):
            scripts = sorted(n for n in os.listdir(dep) if n.endswith(".py"))
            out.append("  脚本=%s" % (", ".join(scripts) or "(空)"))
    except Exception as exc:
        out.append("守卫部署检查失败：%s" % str(exc)[:80])

    try:
        import glob

        logs = sorted(glob.glob(os.path.join(_data_root(), "engine*.log")))
        if logs:
            with open(logs[-1], encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-10:]
            out.append("引擎日志 %s 末尾：" % os.path.basename(logs[-1]))
            out.extend("  " + t for t in tail)
        else:
            out.append("引擎日志：还没有（引擎没启动过）")
    except Exception as exc:
        out.append("读引擎日志失败：%s" % str(exc)[:80])

    text = "\n".join(out)
    print(text)
    if _LOG_FILE is not None:
        _log(text)
    return 0


def _main_inner() -> int:
    args = sys.argv[1:]
    if args == ["--settings-workspace"]:
        from app.settings_workspace import run_settings_workspace

        return run_settings_workspace(QML_DIR)
    if "--self-check" in args:
        return _self_check()
    if "--run-guard" in args:
        i = args.index("--run-guard")
        return _run_guard(args[i + 1:])
    if "--snapshot" in args:
        i = args.index("--snapshot")
        path = args[i + 1] if i + 1 < len(args) else "out/ui.png"
        index = 0
        if "--page" in args:
            j = args.index("--page")
            index = int(args[j + 1]) if j + 1 < len(args) else 0
        tab = -1
        if "--tab" in args:
            j = args.index("--tab")
            tab = int(args[j + 1]) if j + 1 < len(args) else 0
        return _snapshot(path, index, scan="--scan" in args, tab=tab)

    from app.background_session import should_open_workspace
    from core import windows_startup

    automatic = "--from-ae-startup" in args
    if automatic and not should_open_workspace(automatic=True):
        return 0
    lease = windows_startup.acquire_ui(automatic=automatic)
    if lease is None:
        if not automatic:
            windows_startup.activate_ui()
        return 0
    try:
        if not automatic:
            should_open_workspace(automatic=False)
        return _run_gui(lease)
    finally:
        lease.close()


def _run_gui(lease) -> int:
    from PySide6.QtCore import Qt

    from app.workspace_window import WorkspaceApplication, WorkspaceWindow

    ok = _prepare_web_engine()
    _log("场景渲染组件: %s" % ("按需加载" if ok else "不可用（降级为提取画面）"))
    app = WorkspaceApplication(sys.argv)
    app.setApplicationName("AeLocalizer")

    from app.backend import AppBackend

    app.setQuitOnLastWindowClosed(False)
    backend = AppBackend()
    app.aboutToQuit.connect(backend.shutdown)
    backend.exitReady.connect(app.quit, Qt.QueuedConnection)
    workspace = WorkspaceWindow(app, backend, lease, QML_DIR)
    from core.engine import background_on_ae_startup

    start_background = "--from-ae-startup" in sys.argv[1:] and background_on_ae_startup()
    if start_background and app.platformName() == "windows" and workspace.start_background():
        _log("已建立后台入口，首次打开工作台前不加载 Qt Quick")
        return app.exec()
    if not workspace.open():
        _log("QML 加载失败")
        return 1
    if start_background:
        _close_after_language_restore(app, workspace.window, backend.background_session, workspace.runInBackground)
    _log("界面已加载，进入事件循环")
    return app.exec()


def _close_after_language_restore(app, window, session, keep_background):
    """Let ordinary startup finish; only retire a confirmed, unused automatic window."""
    from PySide6.QtCore import QEvent, QObject, QTimer
    from app.background_session import AutomaticClose

    class CancelOnInput(QObject):
        def eventFilter(self, watched, event):
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.TouchBegin):
                cancel_watch()
            return False

    timer = QTimer(window)
    timer.setInterval(1000)
    cancel = CancelOnInput(window)
    app.installEventFilter(cancel)
    checks = 0

    def cancel_watch():
        timer.stop()
        app.removeEventFilter(cancel)
        cancel.deleteLater()
        timer.deleteLater()

    def check():
        nonlocal checks
        checks += 1
        if checks > 120:
            cancel_watch()
            return
        action = session.automatic_close(supports_effects_background=app.platformName() == "windows")
        if action is not AutomaticClose.WAIT:
            cancel_watch()
            if action is AutomaticClose.CLOSE:
                window.close()
            elif action is AutomaticClose.KEEP_BACKGROUND:
                keep_background()

    timer.timeout.connect(check)
    timer.start()


if __name__ == "__main__":
    raise SystemExit(main())
