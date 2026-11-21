# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：AeLocalizer 单文件/目录模式。

用法：
  python tools/build_exe.py            # 目录模式（启动快）
  python tools/build_exe.py --onefile  # 单文件（分发方便但启动慢 2-3 秒）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# ★ 允许用环境变量改输出目录，避免旧产物被占用时无法重建
DIST = os.environ.get("AELOC_DIST") or os.path.join(ROOT, "dist")
BUILD = os.environ.get("AELOC_BUILD") or os.path.join(ROOT, "build")

NAME = "AeLocalizer"

UNUSED_QML = (
    "Qt3D", "QtQuick3D", "QtQuick/Scene2D", "QtQuick/Scene3D",
    "QtCharts", "QtDataVisualization", "QtGraphs", "QtLocation",
    "QtRemoteObjects", "QtScxml", "QtSensors", "QtTest", "QtTextToSpeech", "QtWebView",
)
UNUSED_QT_DLL_PREFIXES = (
    "Qt63D", "Qt6Quick3D", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs",
    "Qt6Location", "Qt6RemoteObjects", "Qt6Scxml", "Qt6Sensors",
    "Qt6Test", "Qt6TextToSpeech", "Qt6WebView",
)


def trim_qt(internal: Path) -> int:
    """Strip debug data and unneeded Qt modules from the directory release."""
    from pefile import DIRECTORY_ENTRY, PE

    qt = internal / "PySide6"
    unused_dirs = (
        *(qt / "qml" / name for name in UNUSED_QML),
        *(qt / "plugins" / name for name in ("designer", "sqldrivers", "generic", "qmltooling")),
    )
    discard = {file for folder in unused_dirs for file in folder.rglob("*") if file.is_file()}
    discard.update((qt / "resources").glob("*.debug.*"))
    discard.update((qt / "translations").glob("*.qm"))

    candidates = {file.name.lower(): file for file in qt.glob("Qt6*.dll")
                  if file.name.startswith(UNUSED_QT_DLL_PREFIXES)}
    pending = [file for file in internal.rglob("*")
               if file.suffix.lower() in (".dll", ".pyd", ".exe")
               and file not in discard and file not in candidates.values()]
    imports = ("IMAGE_DIRECTORY_ENTRY_IMPORT", "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT")
    while pending and candidates:
        binary = PE(str(pending.pop()), fast_load=True)
        try:
            binary.parse_data_directories(directories=[DIRECTORY_ENTRY[name] for name in imports])
            for entry in imports:
                for dependency in getattr(binary, entry.removeprefix("IMAGE_"), ()):
                    retained = candidates.pop(dependency.dll.decode("ascii").lower(), None)
                    if retained is not None:
                        pending.append(retained)
        finally:
            binary.close()
    discard.update(candidates.values())
    removed = sum(file.stat().st_size for file in discard)
    for file in discard:
        file.unlink()
    for folder in unused_dirs:
        if folder.is_dir():
            for child in sorted(folder.rglob("*"), key=lambda path: len(path.parts), reverse=True):
                if child.is_dir():
                    child.rmdir()
            folder.rmdir()
    return removed

#: 必须带上的资源
DATAS = [
    (os.path.join(ROOT, "app", "qml"), "app/qml"),
    (os.path.join(ROOT, "spec"), "spec"),
    (os.path.join(ROOT, "core"), "core"),
    # paramnative reads these ExtendScript templates from disk at runtime.
    (os.path.join(ROOT, "jsx"), "jsx"),
    (os.path.join(ROOT, "data"), "data"),
    # ★ 2026-11-17：场景壁纸实时渲染的渲染页与 WebWallGL（MIT，见同目录 LICENSE）。
    #   漏了它打包版就只剩「提取画面」，而且因为 Loader 静默失败并不报错。
    (os.path.join(ROOT, "app", "assets"), "app/assets"),
    # ★★ 2026-11-19 打包验收修正：**运行时引擎必须随包**。
    #   漏了 runtime/ → 打包版报「未找到 guard_uni 引擎（参数名通道需要它）」，
    #   这在虚拟机/新机器上 100% 复现（数据目录里也没有 guard_runtime 可退回）。
    (os.path.join(ROOT, "runtime"), "runtime"),
    # 守卫用的 frida 17.26.3（_frida.pyd 约 161MB）。打包版里由
    # `AeLocalizer.exe --run-guard` 自执行入口加载，走 PYTHONPATH 注入。
    (os.path.join(ROOT, ".runtime-deps"), ".runtime-deps"),
]

#: ★★ 2026-11-19 发行前修正（实测打包版）：**QtWebEngine 的渲染进程必须有
#:   `translations/qtwebengine_locales/*.pak`**（连 en-US 都没有就会起不来），
#:   缺了它场景壁纸只剩黑底 + 遮罩 —— 表现和"没渲染"一模一样，极难查。
#:   这里显式加进 DATAS，并在瘦身阶段只删 .qm、不删这个子目录。
def webengine_locale_datas() -> list:
    base = _pyside_dir()
    if not base:
        return []
    loc = os.path.join(base, "translations", "qtwebengine_locales")
    if os.path.isdir(loc):
        return [(loc, "PySide6/translations/qtwebengine_locales")]
    return []


def _pyside_dir() -> str:
    """PySide6 安装目录；拿不到返回空串。"""
    try:
        import PySide6

        return os.path.dirname(PySide6.__file__)
    except Exception:
        return ""


def multimedia_datas() -> list:
    """QtMultimedia 的 QML 模块与解码插件（2026-11-16）。

    ★ 为什么必须手工带上：视频背景是 QML 里 `import QtMultimedia` 才用到的，
      Python 侧没有 import 语句。PyInstaller 的 PySide6 hook 不保证把
      qml/QtMultimedia 和 plugins/multimedia（含 FFmpeg 后端）一并收进去 ——
      漏了的话打包版能启动，但视频壁纸会静默不播放，很难查。
    """
    base = _pyside_dir()
    if not base:
        return []
    out = []
    qml_mod = os.path.join(base, "qml", "QtMultimedia")
    if os.path.isdir(qml_mod):
        out.append((qml_mod, "PySide6/qml/QtMultimedia"))
    plug = os.path.join(base, "plugins", "multimedia")
    if os.path.isdir(plug):
        out.append((plug, "PySide6/plugins/multimedia"))
    return out

#: 明确排除的大模块（PySide6 默认会拖进一堆用不到的）
#:
#: ★★ 2026-11-16：**不能排除 QtMultimedia**。壁纸引擎的视频壁纸要靠它实时播放；
#:   排除掉的话打包版就没有动态背景（不过界面仍能启动 —— main.qml 把这一层
#:   关在 VideoBackground.qml 里用 Loader 加载，缺模块只是那一层加载失败）。
#:   QtMultimediaWidgets 仍然排除：本项目只用 QML 的 VideoOutput，不用 QVideoWidget。
EXCLUDES = [
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    # QtWidgets 必须保留（PySide6 包的 __init__ 会导入它）
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    # ★ 注意：不能排除 QtOpenGL —— PySide6.QtWidgets 的导入链会用到它，
    #   排除后 exe 启动直接 ImportError: libshiboken。
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtWebSockets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtStateMachine", "PySide6.QtUiTools", "PySide6.QtXml",
    "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    # ★ 2026-11-19：**不要把 pip 的 frida 打进包**。
    #   PyInstaller 会顺着 live_hook.py 里的 `import frida` 把 site-packages 的
    #   16.6.6 收进去（69MB），而冻结导入器优先级高于 sys.path，结果 AE 注入用的是
    #   旧版而不是项目钉住的 17.26.3 —— 直接排掉，运行时统一走 .runtime-deps。
    "frida",
    "tkinter", "unittest", "pydoc", "doctest", "test", "distutils",
    "setuptools", "pip", "numpy", "pandas", "matplotlib", "PIL",
    "PyQt5", "PyQt6", "IPython", "jedi", "parso",
]


def main() -> int:
    onefile = "--onefile" in sys.argv
    for template in ("catalog_probe.jsx", "parameter_bridge.jsx"):
        if not os.path.isfile(os.path.join(ROOT, "jsx", template)):
            print("[错误] 缺少运行时 JSX 模板：" + template)
            return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[错误] 未安装 PyInstaller：pip install pyinstaller")
        return 2

    for d in (DIST, BUILD):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    entry = os.path.join(ROOT, "app", "main.py")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        # 调试用：AELOC_CONSOLE=1 时留控制台，能看到 Python 层 traceback / Qt 报错
        ("--console" if os.environ.get("AELOC_CONSOLE") == "1" else "--windowed"),
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", BUILD,
    ]
    if onefile:
        cmd.append("--onefile")
    for src, dst in DATAS:
        if os.path.exists(src):
            cmd += ["--add-data", "%s%s%s" % (src, os.pathsep, dst)]
    for src, dst in multimedia_datas() + webengine_locale_datas():
        cmd += ["--add-data", "%s%s%s" % (src, os.pathsep, dst)]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]

    # 隐藏导入：PySide6 的 QML 插件是运行时才加载的，PyInstaller 扫不到
    # ★ 2026-11-16 加 QtMultimedia：它只被 VideoBackground.qml 通过
    #   `import QtMultimedia` 用到，Python 侧没有任何 import —— 不显式带上
    #   打包版就只剩静态背景（视频壁纸会静默退化成不播放）。
    for mod in (
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickControls2",
        "PySide6.QtGui", "PySide6.QtCore", "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
    # ★ 2026-11-17：场景实时渲染（可选依赖 PySide6-WebEngine）。没装的机器上
    #   这三个 import 会失败，但 app/main.py 里是 try 包裹的，降级为「提取画面」。
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel",
    # ★ 2026-11-19：守卫（runtime/managed_guard.py）在 `--run-guard` 里 import frida，
    #   而 frida 来自 .runtime-deps（不在 PyInstaller 分析范围内）。它依赖的这几个
    #   标准库必须一起收进来，否则打包版报 "No module named 'asyncio'"。
    "asyncio", "asyncio.events", "asyncio.base_events", "concurrent.futures",
    "selectors", "ssl",
    ):
        cmd += ["--hidden-import", mod]

    # ★ 打包后剔除不需要的大文件（PyInstaller 会无条件带上）
    cmd.append(entry)
    print("执行：")
    print("  " + " ".join(cmd[:8]) + " …")
    windows_dir = os.environ["SystemRoot"]
    build_env = os.environ.copy()
    # PyInstaller scans PATH for DLLs. Qt expects Windows ICU, whose exports
    # differ from those of third-party ICU builds with the same DLL name.
    build_env["PATH"] = os.pathsep.join((
        os.path.dirname(sys.executable),
        sys.base_prefix,
        os.path.join(sys.base_prefix, "DLLs"),
        os.path.join(windows_dir, "System32"),
        windows_dir,
    ))
    proc = subprocess.run(cmd, cwd=ROOT, env=build_env)
    if proc.returncode != 0:
        print("[错误] 打包失败")
        return proc.returncode

    if not onefile:
        removed = trim_qt(Path(DIST) / NAME / "_internal")
        print("\n[瘦身] 剔除 %.1f MB" % (removed / (1024 * 1024)))

    out = os.path.join(DIST, NAME + (".exe" if onefile else ""))
    if onefile:
        size = os.path.getsize(out) / (1024 * 1024)
        print("\n[OK] %s  %.1f MB" % (out, size))
    else:
        total = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, fs in os.walk(out) for f in fs
        )
        print("\n[OK] %s  目录共 %.1f MB" % (out, total / (1024 * 1024)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
