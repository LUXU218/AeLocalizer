"""把词典写成内存映射二进制文件（供 AE 里的 aex 零拷贝读取）。

格式见 native/utf8fixer/dictmap.h：
  [0..7]    魔数 "AELOCD01"
  [8..11]   条目数
  [12..15]  字符串池字节数
  [16..19]  桶数（预留）
  [20..27]  索引表偏移
  [28..35]  字符串池偏移
  [36..63]  保留
  [64..]    索引表（按 hash 排序的 12 字节项）+ 字符串池
"""
from __future__ import annotations

import os
import struct
from typing import Dict, Iterable, Tuple

MAGIC = b"AELOCD01"
HDR = 64
ENTRY = 12          # hash(u32) + keyOff(u32) + valOff(u32)


def fnv1a(b: bytes) -> int:
    h = 2166136261
    for c in b:
        h ^= c
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def build(
    entries: Dict[str, str],
    path: str,
    *,
    encoding: str = "utf-8",
) -> Tuple[int, int]:
    """把 {英文: 中文} 写成 .bin。返回 (条目数, 文件字节数)。"""
    if not entries:
        raise ValueError("词典为空")

    pool = bytearray()
    pool_off: Dict[str, int] = {}

    def add(s: str) -> int:
        b = s.encode(encoding)
        off = pool_off.get(s)
        if off is not None:
            return off
        off = len(pool)
        pool.extend(b)
        pool.append(0)
        pool_off[s] = off
        return off

    rows = []
    for k, v in entries.items():
        if not k or not v:
            continue
        ko = add(k)
        vo = add(v)
        rows.append((fnv1a(k.encode(encoding)), ko, vo))

    rows.sort(key=lambda r: r[0])

    index_bytes = b"".join(struct.pack("<III", h, ko, vo) for h, ko, vo in rows)
    index_off = HDR
    pool_start = index_off + len(index_bytes)

    header = bytearray(HDR)
    header[0:8] = MAGIC
    struct.pack_into("<I", header, 8, len(rows))
    struct.pack_into("<I", header, 12, len(pool))
    struct.pack_into("<I", header, 16, 0)
    struct.pack_into("<Q", header, 20, index_off)
    struct.pack_into("<Q", header, 28, pool_start)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(header))
        f.write(index_bytes)
        f.write(bytes(pool))
    os.replace(tmp, path)
    return len(rows), os.path.getsize(path)


def verify(path: str, *, encoding: str = "utf-8") -> int:
    """读回来验证，返回条目数；异常抛 ValueError。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HDR or data[0:8] != MAGIC:
        raise ValueError("魔数不对")
    count = struct.unpack_from("<I", data, 8)[0]
    pool_size = struct.unpack_from("<I", data, 12)[0]
    index_off = struct.unpack_from("<Q", data, 20)[0]
    pool_off = struct.unpack_from("<Q", data, 28)[0]

    if index_off + count * ENTRY > len(data):
        raise ValueError("索引越界")
    if pool_off + pool_size > len(data):
        raise ValueError("字符串池越界")

    pool = data[pool_off:pool_off + pool_size]

    def get(off: int) -> str:
        end = pool.index(b"\x00", off)
        return pool[off:end].decode(encoding)

    prev = -1
    for i in range(count):
        h, ko, vo = struct.unpack_from("<III", data, index_off + i * ENTRY)
        if h < prev:
            raise ValueError("索引未按 hash 排序")
        prev = h
        get(ko)
        get(vo)
    return count


def lookup(path: str, key: str, *, encoding: str = "utf-8") -> str:
    """模拟 C++ 侧的二分查找，用于验证逻辑一致。"""
    with open(path, "rb") as f:
        data = f.read()
    count = struct.unpack_from("<I", data, 8)[0]
    index_off = struct.unpack_from("<Q", data, 20)[0]
    pool_off = struct.unpack_from("<Q", data, 28)[0]

    target = fnv1a(key.encode(encoding))
    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi) // 2
        h = struct.unpack_from("<I", data, index_off + mid * ENTRY)[0]
        if h < target:
            lo = mid + 1
        elif h > target:
            hi = mid
        else:
            j = mid
            while j > 0:
                h2 = struct.unpack_from("<I", data, index_off + (j - 1) * ENTRY)[0]
                if h2 != target:
                    break
                j -= 1
            while j < count:
                h2, ko, vo = struct.unpack_from("<III", data, index_off + j * ENTRY)
                if h2 != target:
                    break
                koff = pool_off + ko
                kend = data.index(b"\x00", koff)
                if data[koff:kend].decode(encoding) == key:
                    vstart = pool_off + vo
                    vend = data.index(b"\x00", vstart)
                    return data[vstart:vend].decode(encoding)
                j += 1
            return ""
    return ""
