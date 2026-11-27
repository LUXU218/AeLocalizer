"""写引擎能直接读的词典文件（.dat 加密格式）。

为什么必须写 .dat
----------------
引擎的词典目录解析逻辑（WIN.py 的 _find_dict_dir）是：

    for mark in ("extra_overrides.dat", "extra_overrides.json"):
        for c in 候选目录:
            if 存在 c/mark:  return c

注意 .dat 是**外层循环**：它会把所有候选目录的 .dat 都看一遍，
然后才轮到 .json。而引擎自己所在的目录（hook 目录）里有一个全量的
extra_overrides.dat —— 所以只放 .json 到我们的目录里根本抢不到，
实测引擎仍加载全量词典，按插件勾选的范围无法生效。

因此必须在本目录写一个 .dat，让目录解析命中我们，再保证内容正确。
格式沿用引擎最老的兼容分支（zlib + SHA256 计数器密钥流），
三代读取逻辑都会先试 LXAE2 / LXDC2 再回退到这里，所以能解开。
"""
from __future__ import annotations

import hashlib
import json
import os
import zlib
from typing import Dict, Optional

from .dict_key import DICT_KEY


_KS_CACHE: bytes = b""
"""DICT_KEY 固定,密钥流是同一条无限链的前缀 —— 缓存已生成过的最长前缀,
   后续调用只是切片(2026-11-01:engine.check 每次解密 990KB 都重推 3 万次
   sha256 纯属浪费)。"""


def _keystream_legacy(n: int) -> bytes:
    """SHA256 计数器密钥流（引擎最老格式，向后兼容分支）。"""
    global _KS_CACHE
    if len(_KS_CACHE) >= n:
        return _KS_CACHE[:n]
    out = bytearray(_KS_CACHE)
    if out:
        # 续链:链头是最后一块的哈希 —— 缓存按块(32B)对齐存放才能续上
        if len(out) % 32:
            out = out[: len(out) - (len(out) % 32)]
        h = hashlib.sha256(out[-32:]).digest()
    else:
        h = hashlib.sha256(DICT_KEY).digest()
    while len(out) < n:
        out += h
        h = hashlib.sha256(h).digest()
    _KS_CACHE = bytes(out)
    return _KS_CACHE[:n]


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    """大整数 XOR 代替逐字节生成器(C 速度,990KB 约 50 倍快)。"""
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(
        len(a), "big")


def _cipher(blob: bytes) -> bytes:
    """keystream XOR：C 优先（native/aeloc_cipher.dll），python 回退。

    ★ 2026-11-01 性能重写：两条路径输出逐字节一致（tests/test_native_cipher.py
      黄金对照锁定），DLL 缺失时行为与旧版完全相同。
    """
    if blob:
        from . import native_cipher

        r = native_cipher.native_xor_keystream(DICT_KEY, blob)
        if r is not None:
            return r
    ks = _keystream_legacy(len(blob))
    return _xor_bytes(blob, ks)


def encrypt_legacy(obj: Dict[str, str]) -> bytes:
    """把 {英文: 中文} 编成引擎可解的 .dat 字节串。"""
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    x = _cipher(raw)
    return zlib.compress(x, 6)


def decrypt_legacy(blob: bytes) -> Optional[Dict[str, str]]:
    """反向验证（测试用）。失败返回 None。"""
    try:
        x = zlib.decompress(blob)
        raw = _cipher(x)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def write_dat(obj: Dict[str, str], path: str) -> int:
    """原子写入 .dat，返回文件字节数。"""
    blob = encrypt_legacy(obj)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    import time
    import uuid

    tmp = path + ".tmp." + uuid.uuid4().hex
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    for i in range(40):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 39:
                raise
            time.sleep(0.02)
    return len(blob)
