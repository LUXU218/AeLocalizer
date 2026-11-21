"""读取旧版部署词典（.dat / .json），支持三代加密格式。

为什么要独立实现而不是 import WIN.py：
  · 导出器要能在"没有旧工程树"的机器上跑（用户自己也能导出）
  · 独立实现 + 与 WIN.py 交叉验证（见 tests/test_legacy_reader.py）才能证明等价

三代格式（与旧引擎的词典加载实现严格一致）：
  ① LXAE2  AES-256-GCM，密钥由本机提供的主密钥与 stream_id 派生
  ② LXDC2  zlib 压缩 + 区域派生密钥流（每 64 字节换一把）+ sha256 摘要校验
  ③ 计数器流 zlib 压缩 + sha256 计数器密钥流（最老，现网存量都是这个）
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import zlib
from typing import Any, Dict, List, Optional

from .dict_key import DICT_KEY as _DICT_KEY

_AES_MAGIC = b"LXAE2"
_AES_TAGLEN = 16
_AES_NONCELEN = 12
_MAGIC = b"LXDC2"

#: 引擎 build_dict() 的加载顺序（先者优先，setdefault 语义）
LAYER_ORDER: List[str] = [
    "term_fix",
    "extra_overrides",
    "dict_full_groundtruth",
    "param_names_zh",
    "dict_ae_groundtruth",
    "all_plugins",
]
#: 枚举下拉通道（独立词典，不进 build_dict 主链）
ENUM_LAYERS: List[str] = ["enum_fill_20261011"]
#: 短名覆盖 / 自学习（导出时作为 source 标注）
EXTRA_LAYERS: List[str] = ["short_overrides", "term_fix_short", "learned_dict"]


class LegacyDictError(Exception):
    """词典文件无法解析。"""


_LIB = None


def _aes_key(stream_id: str) -> Optional[bytes]:
    try:
        master = bytes.fromhex(os.environ.get("AELOC_LEGACY_AES_MASTER_HEX", ""))
    except ValueError:
        return None
    if len(master) != 32:
        return None
    sid = stream_id.encode("utf-8")
    return hashlib.sha256(master + b"||" + sid).digest()


def _libcrypto():
    """按候选路径找 libcrypto-3-x64.dll；找不到返回 None（此时 LXAE2 解不开）。"""
    global _LIB
    if _LIB is not None:
        return _LIB
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(os.path.dirname(sys_exe()), "libcrypto-3-x64.dll"),
        os.path.join(here, "libcrypto-3-x64.dll"),
        os.path.join(os.path.dirname(here), "libcrypto-3-x64.dll"),
        os.path.join(os.path.dirname(os.path.dirname(here)), "libcrypto-3-x64.dll"),
        "libcrypto-3-x64.dll",
    ]
    for c in cands:
        try:
            lib = ctypes.CDLL(c)
        except Exception:
            continue
        lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
        lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
        lib.EVP_aes_256_gcm.restype = ctypes.c_void_p
        lib.EVP_DecryptInit_ex.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_char_p, ctypes.c_char_p,
        ]
        lib.EVP_DecryptUpdate.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p, ctypes.c_int,
        ]
        lib.EVP_DecryptFinal_ex.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
        ]
        lib.EVP_CIPHER_CTX_ctrl.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
        ]
        _LIB = lib
        return lib
    return None


def sys_exe() -> str:
    import sys

    return sys.executable


def _aes_decrypt(blob: bytes, stream_id: str) -> Optional[bytes]:
    if len(blob) < 5 + _AES_NONCELEN + 4 + _AES_TAGLEN or blob[:5] != _AES_MAGIC:
        return None
    key = _aes_key(stream_id)
    if key is None:
        return None
    lib = _libcrypto()
    if lib is None:
        return None
    nonce = blob[5:5 + _AES_NONCELEN]
    plen = int.from_bytes(blob[5 + _AES_NONCELEN:5 + _AES_NONCELEN + 4], "little")
    body = blob[5 + _AES_NONCELEN + 4:len(blob) - _AES_TAGLEN]
    tag = blob[len(blob) - _AES_TAGLEN:]
    if plen < 0 or len(body) != plen:
        return None
    aad = stream_id.encode("utf-8")
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        return None
    try:
        if lib.EVP_DecryptInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None) != 1:
            return None
        if lib.EVP_CIPHER_CTX_ctrl(ctx, 0x9, len(nonce), None) != 1:
            return None
        if lib.EVP_DecryptInit_ex(ctx, None, None, key, nonce) != 1:
            return None
        olen = ctypes.c_int(0)
        if aad and lib.EVP_DecryptUpdate(ctx, None, ctypes.byref(olen), aad, len(aad)) != 1:
            return None
        out = ctypes.create_string_buffer(len(body) + 32)
        if body and lib.EVP_DecryptUpdate(ctx, out, ctypes.byref(olen), body, len(body)) != 1:
            return None
        total = olen.value
        if lib.EVP_CIPHER_CTX_ctrl(ctx, 0x11, _AES_TAGLEN, tag) != 1:
            return None
        if lib.EVP_DecryptFinal_ex(ctx, out[total:], ctypes.byref(olen)) != 1:
            return None
        return out.raw[:total + olen.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def _derive(stream_id: str, block_idx: int) -> bytes:
    return hashlib.sha256(
        b"|".join((_DICT_KEY, stream_id.encode("utf-8"), block_idx.to_bytes(8, "little")))
    ).digest()


def _keystream(n: int, stream_id: str, block: int = 64) -> bytes:
    out = bytearray()
    bi = 0
    while len(out) < n:
        out += _derive(stream_id, bi)
        bi += 1
    return bytes(out[:n])


def _keystream_legacy(n: int) -> bytes:
    out = bytearray()
    h = hashlib.sha256(_DICT_KEY).digest()
    while len(out) < n:
        out += h
        h = hashlib.sha256(h).digest()
    return bytes(out[:n])


def _decrypt_lxdc2(dat: bytes, stream_id: str) -> Optional[bytes]:
    if len(dat) < 5 + 4 + 32 or dat[:5] != _MAGIC:
        return None
    try:
        plen = int.from_bytes(dat[5:9], "little")
        digest = dat[9:41]
        body = dat[41:]
        x = zlib.decompress(body)
    except Exception:
        return None
    if plen < 0 or len(x) != plen:
        return None
    ks = _keystream(plen, stream_id)
    raw = bytes(a ^ b for a, b in zip(x, ks))
    return raw if hashlib.sha256(raw).digest() == digest else None


def _decrypt_legacy(dat: bytes) -> Optional[bytes]:
    try:
        x = zlib.decompress(dat)
    except Exception:
        return None
    ks = _keystream_legacy(len(x))
    return bytes(a ^ b for a, b in zip(x, ks))


def read_dict_file(path: str) -> Dict[str, str]:
    """读一个 .dat / .json 词典文件，返回 {英文: 中文}。"""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError as exc:
        raise LegacyDictError(f"无法读取 {path}: {exc}") from exc

    stream_id = os.path.splitext(os.path.basename(path))[0]
    raw: Optional[bytes] = None

    if blob[:5] == _AES_MAGIC:
        raw = _aes_decrypt(blob, stream_id)
        if raw is None:
            raise LegacyDictError(
                f"{path}: LXAE2 解密失败（缺少 libcrypto-3-x64.dll，或本机配置的密钥不可用）"
            )
    elif blob[:5] == _MAGIC:
        raw = _decrypt_lxdc2(blob, stream_id)
        if raw is None:
            raise LegacyDictError(f"{path}: LXDC2 解密失败（文件损坏或 stream_id 不符）")
    else:
        raw = _decrypt_legacy(blob)
        if raw is None:
            # 可能本身就是明文 JSON
            try:
                obj = json.loads(blob.decode("utf-8"))
                if isinstance(obj, dict):
                    return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, str)}
            except Exception:
                pass
            raise LegacyDictError(f"{path}: 无法识别格式（既非三代加密，也非明文 JSON）")

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise LegacyDictError(f"{path}: 解密后不是合法 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise LegacyDictError(f"{path}: 顶层不是对象（实际 {type(obj).__name__}）")
    return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, str)}


def load_legacy_layer(dict_dir: str, name: str) -> Dict[str, str]:
    """按 basename 加载一层；.dat 优先，回退 .json；都没有返回空 dict。"""
    for ext in (".dat", ".json"):
        p = os.path.join(dict_dir, name + ext)
        if os.path.exists(p):
            return read_dict_file(p)
    return {}
