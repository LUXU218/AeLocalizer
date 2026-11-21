"""主题即时切换自检 —— 2026-11-01「点了深色没反应」事故的回归护栏。

复现事故路径：起真窗口（offscreen 渲染）→ 截浅色 → 在【运行中】调
backend.setUiTheme("dark") → 断言窗口颜色真的变了 → 再截深色。
上次就是缺这一步：只验证了「启动时是深色」（settings.json 预置），
没验证「运行中切过去」，结果 QML 侧根本没把值灌进 Theme 单例。

用法：
  python tools/theme_live_check.py          # 截图存 out/theme_*.png，打印结论
退出码 0 = 通过，1 = 失败。
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

OUT = os.path.join(ROOT, "out")
LIGHT_BG = "#ffffff"    # Theme.bgBase 浅色
DARK_BG = "#0f131a"     # Theme.bgBase 深色（2026-11-01 色板重调后的值）


def main() -> int:
    # ★ 沙盒：绝不写真实用户目录
    sandbox = tempfile.mkdtemp(prefix="aeloc_theme_live_")
    os.environ["AELOC_DATA_ROOT"] = sandbox
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"ui_theme": "light"}, f)

    app = QGuiApplication(sys.argv)

    import app.main as appmain
    from app.backend import AppBackend

    backend = AppBackend()
    assert backend.uiTheme == "light", "初始主题应为 light"

    engine = QQmlApplicationEngine()
    appmain._add_import_path(engine)
    engine.rootContext().setContextProperty("appBackend", backend)
    engine.load(QUrl.fromLocalFile(os.path.join(appmain.HERE, "qml", "main.qml")))
    if not engine.rootObjects():
        print("[FAIL] QML 加载失败")
        return 1
    win = engine.rootObjects()[0]
    os.makedirs(OUT, exist_ok=True)

    log = []

    def shot(name):
        img = win.grabWindow()
        img.save(os.path.join(OUT, name))
        log.append("  截图 out/%s" % name)

    def fail(msg):
        print("[FAIL] " + msg)
        for line in log:
            print(line)
        app.quit()
        return 1

    def check_color(tag, expect):
        actual = win.property("color").name().lower()
        log.append("  %s 窗口颜色 = %s（期望 %s）" % (tag, actual, expect))
        if actual != expect:
            return fail("%s：窗口颜色没跟上（%s != %s）—— 主题没即时生效" % (tag, actual, expect))
        return None

    steps = []

    def step1_light():  # 浅色：设置页 + 翻译页
        if check_color("浅色", LIGHT_BG):
            return
        win.setProperty("pageIndex", 4)
        shot("theme_light_p4.png")
        win.setProperty("pageIndex", 0)
        shot("theme_light_p0.png")
        run_next()

    def step2_switch_dark():  # ★ 事故路径：运行中切深色
        backend.setUiTheme("dark")
        # 给绑定循环一圈事件
        QTimer.singleShot(200, step3_verify_dark)

    def step3_verify_dark():
        if backend.uiTheme != "dark":
            return fail("backend.uiTheme 不是 dark")
        if check_color("切深色后", DARK_BG):
            return
        win.setProperty("pageIndex", 4)
        shot("theme_dark_p4.png")
        win.setProperty("pageIndex", 0)
        shot("theme_dark_p0.png")
        run_next()

    def step4_system_roundtrip():  # system 模式往返不崩、颜色合法
        backend.setUiTheme("system")
        QTimer.singleShot(200, step5_finish)

    def step5_finish():
        mode = backend.uiTheme
        color = win.property("color").name().lower()
        log.append("  system 模式窗口颜色 = %s（跟随系统，浅或深都合法）" % color)
        if color not in (LIGHT_BG, DARK_BG):
            return fail("system 模式窗口颜色异常：%s" % color)
        if mode != "system":
            return fail("mode != system")
        # 收尾：切回 dark 再确认一次（连切三次不崩）
        backend.setUiTheme("dark")
        QTimer.singleShot(200, done)

    def done():
        if check_color("再切回深色", DARK_BG):
            return
        print("[PASS] 主题运行中切换即时生效：light → dark → system → dark 全程窗口颜色跟踪正确")
        for line in log:
            print(line)
        print("  数据沙盒：%s" % sandbox)
        app.quit()

    steps.extend([step1_light, step2_switch_dark, step3_verify_dark,
                  step4_system_roundtrip, step5_finish])
    state = {"i": 0}

    def run_next():
        i = state["i"]
        state["i"] += 1
        steps[i]()

    QTimer.singleShot(300, run_next)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
