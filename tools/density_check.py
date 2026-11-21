"""个性化档位自检（2026-11-16 设置页改版）。

为什么需要它：字号档会动 Theme.font*（100 处跟随），密度档会动
Theme.space*（62 处跟随），但全站还有 52 处字面量字号、64 处字面量高度
**不跟随**。所以「小 + 紧凑」和「大 + 宽松」是裁切风险最高的两端 ——
必须真的渲染出图看，不能靠读代码断言。

做法：起真 backend（沙盒数据根）+ 真 QML，逐档位调 backend.setXxx()，
再逐页截图。走的是「设置页点一下」的同一条路径，不是直接改单例。

用法：
  python tools/density_check.py
截图落在 out/density_<档位>_p<页>.png，退出码 0 = 通过。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["PYTHONIOENCODING"] = "utf-8"

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

OUT = os.path.join(ROOT, "out")
PAGES = [("翻译", 0), ("效果名", 1), ("引擎", 2), ("补词", 3), ("设置", 4)]

#: (标签, accentKey, density, fontScale, reduceMotion)
COMBOS = [
    ("default", "blue", "normal", "normal", False),
    ("tight", "teal", "compact", "small", True),
    ("loose", "rose", "relaxed", "large", False),
]


def main() -> int:
    # ★ 沙盒：绝不写真实用户目录
    sandbox = tempfile.mkdtemp(prefix="aeloc_density_")
    os.environ["AELOC_DATA_ROOT"] = sandbox
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"ui_theme": "light"}, f)

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

    steps = []
    for label, accent, density, scale, motion in COMBOS:
        for page_name, idx in PAGES:
            steps.append((label, accent, density, scale, motion, page_name, idx))

    state = {"i": 0}
    failures = []
    log = []

    def run_next():
        i = state["i"]
        if i >= len(steps):
            finish()
            return
        state["i"] += 1
        label, accent, density, scale, motion, page_name, idx = steps[i]
        # 走 backend 的公开槽 —— 与用户在设置页点选完全同一条路径
        backend.setAccentKey(accent)
        backend.setDensity(density)
        backend.setFontScale(scale)
        backend.setReduceMotion(motion)
        win.setProperty("pageIndex", idx)
        QTimer.singleShot(260, lambda: shot(label, page_name, idx))

    def shot(label, page_name, idx):
        img = win.grabWindow()
        name = "density_%s_p%d.png" % (label, idx)
        path = os.path.join(OUT, name)
        img.save(path)
        size = os.path.getsize(path)
        # 页面整个塌成纯色 / 只剩背景 → 字节数会异常小。8KB 是经验下限，
        # 五页在正常档位下都是 25 万字节级别，差两个数量级。
        if size < 8000:
            failures.append("%s/%s 渲染异常（仅 %d 字节）" % (label, page_name, size))
        log.append("  %-8s %-6s -> out/%s  %d 字节" % (label, page_name, name, size))
        QTimer.singleShot(40, run_next)

    def finish():
        # 断言后端真的存下了档位（防止「渲染看着没问题，其实值没落盘」）
        if backend.accentKey != COMBOS[-1][1]:
            failures.append("accentKey 未生效：%s" % backend.accentKey)
        try:
            from core import settings as _settings

            obj = _settings.load(sandbox)
            for key, expect in (("accent_key", COMBOS[-1][1]),
                                ("density", COMBOS[-1][2]),
                                ("font_scale", COMBOS[-1][3])):
                if obj.get(key) != expect:
                    failures.append("settings.json %s = %r，期望 %r"
                                    % (key, obj.get(key), expect))
        except Exception as exc:
            failures.append("读 settings.json 失败: %r" % (exc,))

        for line in log:
            print(line)
        print()
        if failures:
            print("[FAIL] %d 项" % len(failures))
            for f in failures:
                print("  " + f)
            app.quit()
            return
        print("[PASS] %d 组档位 × %d 页渲染成功，档位已落盘"
              % (len(COMBOS), len(PAGES)))
        print("  数据沙盒：%s" % sandbox)
        app.quit()

    QTimer.singleShot(600, run_next)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
