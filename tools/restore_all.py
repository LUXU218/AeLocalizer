"""还原所有可逆的汉化层（效果名 / 参数名）。

用法:
    python tools/restore_all.py          # 真的还原
    python tools/restore_all.py --dry    # 只预演

由 `一键还原AE原版.bat` 调用；也可单独跑。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import restore  # noqa: E402


def main() -> int:
    dry = "--dry" in sys.argv or os.environ.get("AELOC_DRY") == "1"
    if dry:
        print("[预演] 不写入任何文件")
    bad = 0
    for key, fn in restore.RESTORERS.items():
        try:
            n, msg = fn(dry=dry)
            print("%-14s %s  %s" % (key, n, msg))
        except Exception as exc:
            bad += 1
            print("%-14s 失败: %s" % (key, exc))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
