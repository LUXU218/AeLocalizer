# -*- coding: utf-8 -*-
"""测试包加固(2026-11-02 02:49 事故):裸跑 unittest 也必须沙盒。

事故复盘:首次全量测试未设 AELOC_DATA_ROOT,测试套件把真实
<LOCALAPPDATA>/AeLocalizer/engine_dict/extra_overrides.dat 覆盖成
7 条测试词(§5.27 同款事故复发,25,951 条词库被遮蔽)。当时唯一
闸门是「跑测试必须手动设环境变量」的文档约定 —— 约定会被人忘。

本文件在包导入处(任何 unittest 入口都会先 import tests)做两道锁:
  1. AELOC_DATA_ROOT 未设时 setdefault 到固定临时目录 —— engine /
     backend 的 DATA_ROOT 解析都认它,任何跑法都落沙盒;
  2. atexit 快照比对真实 engine_dict 关键文件 —— 就算第 1 道被绕过
     (比如测试里 os.environ 弹掉变量),退出时也当场炸出来。

真实数据根只允许 tools/rebuild_engine_dict.py 触达(它断言必须
无此变量,即"在真实根上跑"是显式行为)。
"""
import atexit
import hashlib
import os
import tempfile

if not os.environ.get("AELOC_DATA_ROOT"):
    os.environ["AELOC_DATA_ROOT"] = os.path.join(
        tempfile.gettempdir(), "aeloc_test_sandbox_default")
    os.makedirs(os.environ["AELOC_DATA_ROOT"], exist_ok=True)

# ---- 真实 engine_dict 快照(与 test_backend 的 ai_key 快照护栏同思路) ----
_REAL_DICT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "AeLocalizer", "engine_dict")
_REAL_DICT_FILES = ("extra_overrides.dat", "extra_overrides.json",
                    "term_fix.dat", "term_fix.json", "learned_dict.dat")


def _snapshot_real_dict():
    snap = {}
    for name in _REAL_DICT_FILES:
        p = os.path.join(_REAL_DICT_DIR, name)
        try:
            with open(p, "rb") as f:
                snap[name] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            snap[name] = None
    return snap


_SNAPSHOT = _snapshot_real_dict()


def _assert_real_dict_untouched():
    for name, want in _SNAPSHOT.items():
        p = os.path.join(_REAL_DICT_DIR, name)
        try:
            with open(p, "rb") as f:
                now = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            now = None
        assert now == want, (
            "测试改动了真实 engine_dict/%s!(§5.27 同款事故) "
            "恢复: python tools/rebuild_engine_dict.py"
            % name)


atexit.register(_assert_real_dict_untouched)
