"""界面自检：无头渲染所有页面并检查是否有 QML 报错。

用法：
  python tools/ui_check.py            # 渲染 2 个工作台 Tab 到 out/ui_p*.png
  python tools/ui_check.py --strict   # 有任何 QML 警告就失败
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "out")

# 2026-11-01 起:侧边栏五页(--tab 切页,新增「设置」)
PAGES = [
    ("翻译", 0),
    ("效果名", 1),
    ("引擎", 2),
    ("补词", 3),
    ("设置", 4),
    ("面板与脚本", 5),
]

#: 这些是环境限制导致的噪音，不算错误
IGNORE = (
    "FT_New_Face failed",
    "Cannot find font directory",
    "QFontDatabase",
    "qt.qpa",
    "QQuickWindow",
    "propagateSizeHints",
)


def main() -> int:
    strict = "--strict" in sys.argv
    os.makedirs(OUT, exist_ok=True)

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 2026-11-16 沙盒：这个自检以前跑在真实数据根上，于是渲染结果取决于
    #   「用户当前把背景/底板/强调色调成了什么」—— 无法复现，也不符合
    #   「测试与脚本一律不许碰真实用户目录」的项目纪律。现在固定一个干净沙盒：
    #   每次都从默认观感开始渲染，图才有可比性。
    env["AELOC_DATA_ROOT"] = tempfile.mkdtemp(prefix="aeloc_ui_check_")

    failed = 0
    for label, idx in PAGES:
        png = os.path.join(OUT, "ui_p%d.png" % idx)
        cmd = [
            sys.executable, "-m", "app.main",
            "--snapshot", png, "--tab", str(idx), "--scan",
        ]
        if idx == 0:            # 翻译页额外灌入示例草稿，验证草稿区渲染
            cmd.append("--demo-ai")
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
        out = (proc.stdout or "") + (proc.stderr or "")

        problems = []
        for line in out.splitlines():
            if not line.strip():
                continue
            if any(t in line for t in IGNORE):
                continue
            if re.search(r"(ReferenceError|TypeError|is not a type|unavailable|failed to load)", line):
                problems.append(line.strip())
            elif "QML Connections" in line:
                problems.append(line.strip())

        ok_file = os.path.exists(png) and os.path.getsize(png) > 5000
        status = "✔" if ok_file and not problems else "✘"
        print("%s %-10s %s  %s" % (
            status, label,
            "%7d 字节" % os.path.getsize(png) if os.path.exists(png) else "   缺失",
            "" if not problems else problems[0][:110]))

        if not ok_file or problems:
            failed += 1
            if strict:
                for p in problems[:5]:
                    print("     " + p)

    print()
    if failed:
        print("%d / %d 页有问题" % (failed, len(PAGES)))
        return 1
    print("全部 %d 页渲染正常" % len(PAGES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
