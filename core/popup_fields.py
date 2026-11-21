# -*- coding: utf-8 -*-
"""当前 typed parameter stream 的 popup definition 读写（只读发现 + 严格原地）。

证据：AE 2025 当前 stream 上 0x70/0x500 等字段可指向 GBK 'A|B|C' definition；
物理指针可能别名。写入只允许当前 raw 完全匹配且不扩容，绝不分配 C++ 内存。
"""
from __future__ import annotations

import struct
import time

OFFSETS = (0x70, 0x68, 0x78, 0x60, 0x80, 0x58, 0x88, 0x50,
           0x90, 0x48, 0x98, 0x40, 0x500)


def read_cstring(p, address: int, max_len: int = 2048):
    """Bounded C-string read; only returns after a real NUL.

    Every touched page must be committed MEM_PRIVATE PAGE_READWRITE, not
    PAGE_GUARD. A readable prefix at a page end is not a complete definition:
    if the next page is unreadable/no NUL within max_len, return None.
    """
    out = bytearray()
    at = int(address)
    while len(out) < max_len:
        page = at & ~0xFFF
        if hasattr(p, "k"):
            try:
                from core.proc import virtual_query
                mbi = virtual_query(p, at)
                if (not mbi or mbi.State != 0x1000 or mbi.Type != 0x20000
                        or mbi.Protect != 4 or (mbi.Protect & 0x100)):
                    return None
            except Exception:
                return None
        n = min(max_len - len(out), 0x1000 - (at & 0xFFF))
        if n <= 0:
            break
        try:
            data = p.read(at, n)
        except Exception:
            return None
        if not data:
            return None
        nul = data.find(b"\0")
        if nul >= 0:
            out.extend(data[:nul])
            return bytes(out)
        out.extend(data)
        at += len(data)
    return None


def _valid_row(p, row) -> bool:
    try:
        addr = int(row["address"])
        guid = str(row.get("guid") or "")
        if guid and p.read(addr + 8, 16).hex() != guid:
            return False
        if row.get("parent") and p.u64(addr + 32) != int(row["parent"]):
            return False
        return True
    except Exception:
        return False


def read_popup_fields(p, rows, encoding="gbk", deadline=None,
                      require_ascii=False) -> list:
    """按当前 stream 身份读 popup definitions；不保留/恢复旧指针。"""
    out = []
    seen = set()
    for row in rows or ():
        if deadline is not None and time.monotonic() > deadline:
            break
        if not _valid_row(p, row):
            continue
        addr = int(row["address"])
        for off in OFFSETS:
            try:
                ep = p.u64(addr + off)
                if not 0x10000 < ep < 0x7FFFFFFEFFFF or ep in seen:
                    continue
                seen.add(ep)
                raw = read_cstring(p, ep, 2048)
                if raw is None or not (3 < len(raw) < 2048):
                    continue
                if raw.count(b"|") < 1 or any(b < 32 for b in raw):
                    continue
                if require_ascii and any(b > 126 for b in raw):
                    continue
                try:
                    text = raw.decode(encoding)
                except UnicodeError:
                    continue
                parts = text.split("|")
                if len(parts) > 100 or any(len(s) > 100 for s in parts):
                    continue
                out.append({"effectMatchName": row.get("effectMatchName") or "",
                            "matchName": row.get("matchName") or "",
                            "address": addr, "guid": row.get("guid") or "",
                            "parent": row.get("parent") or 0,
                            "offset": off, "pointer": ep,
                            "raw_hex": raw.hex(), "text": text,
                            "items": parts})
            except Exception:
                continue
    return out


def write_popup_field(p, field, text: str, encoding="gbk") -> bool:
    """仅当当前 raw 与捕获完全一致、且新串不扩容时原地写；不做分配。"""
    try:
        addr = int(field["address"])
        off = int(field["offset"])
        expected = bytes.fromhex(field["raw_hex"])
        pointer = p.u64(addr + off)
        if pointer != int(field["pointer"]):
            return False
        if not _valid_row(p, field):
            return False
        cur = p.read(pointer, len(expected) + 1)
        if cur[:len(expected)] != expected or cur[len(expected):len(expected) + 1] != b"\0":
            return False
        new = text.encode(encoding)
        if len(new) + 1 > len(expected) + 1:
            return False
        payload = new + b"\0"
        # 不覆盖原分配块之外；无法确认容量时只允许相同/更短长度。
        if len(payload) > len(expected) + 1:
            return False
        p.write_exact(pointer, payload)
        return p.read(pointer, len(payload)) == payload
    except Exception:
        return False
