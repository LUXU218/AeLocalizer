"""命令行：停用/恢复需要授权的插件（供提权调用）。"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.compat import check_plugin_dir, disable, enable  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--disable", action="store_true")
    g.add_argument("--enable", action="store_true")
    args = ap.parse_args()

    mc = os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                      "Adobe", "Common", "Plug-ins", "7.0", "MediaCore")
    issues = check_plugin_dir(mc)

    for i in issues:
        if i.vendor != args.vendor:
            continue
        ok, msg = (enable(i) if args.enable else disable(i))
        print(msg)
        return 0 if ok else 1

    print("找不到厂商:", args.vendor)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
