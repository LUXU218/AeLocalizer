"""设置页六个分区的严格自检（2026-11-16 设置页改版）。

ui_check.py 只截设置页的第 0 个分区（外观）。数据 / 更新 / 诊断 / 关于
这四个分区里有 TextEdit、ProgressBar、ScrollBar、FileDialog 等控件，
其中任何一个写错都只会在真正切过去时才报错 —— 五页渲染全绿也查不出来。

本脚本做两件事：
  1) 逐个分区切过去，装 Qt 消息处理器抓 ReferenceError / TypeError /
     is not a type / binding loop 这类 QML 错误；
  2) 同时截图，供肉眼复核版式。

用法：
  python tools/settings_sections_check.py      # 退出码 0 = 全绿
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["PYTHONIOENCODING"] = "utf-8"

OUT = os.path.join(ROOT, "out")
#: 这些是环境噪音，不是 QML 错误（与 ui_check.py 的 IGNORE 同源）
IGNORE = (
    "FT_New_Face failed",
    "Cannot find font directory",
    "QFontDatabase",
    "qt.qpa",
    "QQuickWindow",
    "propagateSizeHints",
    "OpenThemeData",
)
BAD = re.compile(
    r"(ReferenceError|TypeError|is not a type|unavailable|Cannot assign|"
    r"Unable to assign|failed to load|Binding loop|Cannot read property)"
)

SECTIONS = ["appearance", "general", "data", "update", "diagnostic", "about"]


def main() -> int:
    warnings: list[str] = []

    from PySide6.QtCore import QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow

    def handler(_mode, _ctx, msg):
        if not msg.strip():
            return
        if any(t in msg for t in IGNORE):
            return
        if BAD.search(msg):
            warnings.append(msg.strip())

    qInstallMessageHandler(handler)

    # ★ 沙盒：绝不写真实用户目录
    sandbox = tempfile.mkdtemp(prefix="aeloc_sections_")
    os.environ["AELOC_DATA_ROOT"] = sandbox
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"ui_theme": "dark", "accent_key": "violet"}, f)

    app = QGuiApplication(sys.argv)
    QQuickWindow.setSceneGraphBackend("software")

    import app.main as appmain
    from app.backend import AppBackend

    backend = AppBackend()
    app.aboutToQuit.connect(backend.shutdown)

    engine = QQmlApplicationEngine()
    appmain._add_import_path(engine)
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.load(QUrl.fromLocalFile(os.path.join(appmain.HERE, "qml", "main.qml")))
    if not engine.rootObjects():
        print("[FAIL] QML 加载失败")
        return 1
    win = engine.rootObjects()[0]
    os.makedirs(OUT, exist_ok=True)

    failures: list[str] = []

    def run(i: int):
        if i >= len(SECTIONS):
            finish()
            return
        win.setProperty("settingsSection", i)
        QTimer.singleShot(320, lambda: shot(i))

    def shot(i: int):
        name = SECTIONS[i]
        path = os.path.join(OUT, "settings_%s.png" % name)
        win.grabWindow().save(path)
        size = os.path.getsize(path)
        if size < 8000:
            failures.append("%s 分区渲染异常（仅 %d 字节）" % (name, size))
        print("  %-11s -> out/settings_%s.png  %d 字节" % (name, name, size))
        run(i + 1)

    def finish():
        if warnings:
            failures.extend(warnings)
        print()
        if failures:
            print("[FAIL] %d 项" % len(failures))
            for f in failures[:12]:
                print("  " + f)
            app.quit()
            return
        print("[PASS] 设置页 %d 个分区渲染成功，无 QML 错误" % len(SECTIONS))
        app.quit()

    def start():
        win.setProperty("pageIndex", 4)
        QTimer.singleShot(500, lambda: run(0))

    QTimer.singleShot(600, start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
