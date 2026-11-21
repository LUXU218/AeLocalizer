"""aeloc_cipher.dll 桥（2026-11-01 性能重写）。

把 core/legacy/writer.py 的「SHA256 密钥流 + XOR」核心放进 C
（native/aeloc_cipher.c，tools/build_native.py 编译），python 侧只做
一次调用。DLL 缺失 / 加载失败时返回 None，调用方回退纯 python 实现 ——
两条路径的输出逐字节一致，由 tests/test_native_cipher.py 黄金对照锁定。
"""
from __future__ import annotations

import ctypes as C
import os
import sys

_LIB = None
_TRIED = False


def _dll_path() -> str:
    """aeloc_cipher.dll 的候选路径：打包资源 → 仓库 native/ → exe 同目录。"""
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "aeloc_cipher.dll"))
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands.append(os.path.join(root, "native", "aeloc_cipher.dll"))
    try:
        cands.append(os.path.join(os.path.dirname(sys.executable), "aeloc_cipher.dll"))
    except Exception:
        pass
    for p in cands:
        if os.path.exists(p):
            return p
    return ""


def _load():
    global _LIB, _TRIED
    if _TRIED:
        return _LIB
    _TRIED = True
    p = _dll_path()
    if not p:
        return None
    try:
        lib = C.CDLL(p)
        lib.aeloc_cipher.argtypes = [C.c_void_p, C.c_size_t,
                                     C.c_void_p, C.c_size_t, C.c_void_p]
        lib.aeloc_cipher.restype = None
        _LIB = lib
    except OSError:
        _LIB = None
    return _LIB


def native_xor_keystream(key: bytes, blob: bytes):
    """blob XOR SHA256-keystream(key)。DLL 不可用返回 None。

    key/blob 允许为空（等价返回 b"" / 原 bytes 异或零密钥流 = 原样）。
    """
    if not blob:
        return b""
    lib = _load()
    if lib is None:
        return None
    out = C.create_string_buffer(len(blob))
    lib.aeloc_cipher(
        C.c_char_p(key) if key else None, C.c_size_t(len(key)),
        C.c_char_p(blob), C.c_size_t(len(blob)),
        out)
    return out.raw[:len(blob)]
