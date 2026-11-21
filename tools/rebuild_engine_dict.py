# -*- coding: utf-8 -*-
"""从真实 store 重建引擎词典(恢复工具,2026-10-29 事故产物)。

背景: core/engine.py 曾无视 AELOC_DATA_ROOT, 测试套件经 prepare_dict 把
真实 engine_dict/extra_overrides.dat 覆盖成测试词条(7 条), 遮蔽完整词典,
守护重启后词典 51,649 -> 189。本工具按 backend._hot_reload_notify 的同一条
路径(DictStore.resolve 全量 -> engine.prepare_dict)重建, 不碰 store 本身。

用法: python tools/rebuild_engine_dict.py
(绝不设 AELOC_DATA_ROOT —— 要在真实数据根上重建)
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

assert not os.environ.get("AELOC_DATA_ROOT"), \
    "本工具必须在真实数据根上运行, 请去掉 AELOC_DATA_ROOT"

from core.dictstore import DictStore          # noqa: E402
from core import engine                       # noqa: E402


def main() -> int:
    path = os.path.join(engine.DATA_ROOT, "store.json")
    store = DictStore.load(path)
    entries = {k: r.zh for k, r in store.resolve().items() if r.zh}
    print("store: %s | 有效词条 %d" % (path, len(entries)))
    res = engine.prepare_dict(entries)
    print("重建: %s" % (res,))
    dat = os.path.join(engine.ENGINE_DICT_DIR, "extra_overrides.dat")
    if os.path.exists(dat):
        print("extra_overrides.dat: %d 字节" % os.path.getsize(dat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
