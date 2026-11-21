"""从 AI 缓存里找回被误覆盖的译文（2026-10-23 事故善后）。

背景：
  测试脚本把真实 store.json 覆盖成了空库（详见 tests/test_backend.py 顶部）。
  已从 store.json.bak-20261022-232215 恢复到 22:51 的状态，
  但 02:11 新接受的一批译文只在缓存（ai-cache.json）里还有。

做法：
  · 缓存键 = sha256(en + "\\0" + plugin + "\\0" + gver + "\\0" + model + "\\0" + PROMPT_VERSION)
  · 当时那批是用 plugin="" / 空术语表 / deepseek-chat / p3 写进去的
  · 逐条重算键 -> 命中且当前词典里没有译文的，补进 user 层
  · 补之前先备份 store.json，并过一遍「太短垃圾译文」闸门

用法：
  python tools/recover_store_from_cache.py            # 只报告（dry-run）
  python tools/recover_store_from_cache.py --apply    # 真写回
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ai.gates import check_too_short
from core.ai.pipeline import Cache
from core.dictstore import DictStore
from core.inventory import build_inventory
from core.nameeditor import collect_rows
from core.pipl import find_ae_installs

DATA_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AeLocalizer"
)
STORE = os.path.join(DATA_ROOT, "store.json")
CACHE = os.path.join(DATA_ROOT, "ai-cache.json")

#: 这批译文写入缓存时用的参数（由 2026-10-23 实测反推）
RECOVER_PLUGIN = ""
RECOVER_GVER = hashlib.sha256().hexdigest()[:16]   # 空术语表
RECOVER_MODEL = "deepseek-chat"
RECOVER_PROMPT_VERSION = "p3"

_CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> int:
    apply = "--apply" in sys.argv
    if not os.path.exists(STORE):
        print("找不到 store.json：", STORE)
        return 2
    store = DictStore.load(STORE)
    cache = Cache(CACHE)

    installs = dict(find_ae_installs())
    version = os.environ.get("AELOC_AE_VERSION") or (
        "2025" if "2025" in installs else (list(installs)[0] if installs else ""))
    ae_root = installs.get(version)
    if not ae_root:
        print("没找到 AE 安装")
        return 2
    inv = build_inventory(ae_root, version)
    rows = collect_rows(inv, store)

    # 复用当时的 PROMPT_VERSION 计算键
    import core.ai.pipeline as pipeline_mod

    old_pv = pipeline_mod.PROMPT_VERSION
    pipeline_mod.PROMPT_VERSION = RECOVER_PROMPT_VERSION
    recovered = []
    try:
        for r in rows:
            if (r.zh or "").strip():
                continue
            k = Cache.key(r.en, RECOVER_PLUGIN, RECOVER_GVER, RECOVER_MODEL)
            zh = cache.get(k)
            if not zh:
                continue
            bad, _why = check_too_short(r.en, zh)
            if bad or not _CJK.search(zh):
                continue
            try:
                if r.slot and len(zh.encode("gbk")) > r.slot:
                    continue
            except UnicodeEncodeError:
                continue
            recovered.append((r.en, zh))
    finally:
        pipeline_mod.PROMPT_VERSION = old_pv

    print("AE %s · 效果名 %d 条 · 当前已译 %d · 缓存可找回 %d"
          % (version, len(rows), sum(1 for r in rows if r.zh.strip()), len(recovered)))
    for en, zh in recovered[:10]:
        print("   %s -> %s" % (en, zh))
    if not apply:
        print("\n[dry-run] 加 --apply 才会写回 store.json")
        return 0

    bak = STORE + ".bak-before-cacherecover-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(STORE, bak)
    for en, zh in recovered:
        store.upsert_user(en, zh)
    store.save(STORE)
    print("\n已写回 %d 条 · 备份：%s" % (len(recovered), bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
