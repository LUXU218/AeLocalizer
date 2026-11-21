# -*- coding: utf-8 -*-
"""有界、只读的参数发现：TDB match-pool + 真实 parade + ParameterCatalog.rows。

只解决“本机 profile 缺 match_pool”的构建；不扫描任意 UTF-16 字符串，不写
AE/参数值。所有结果按 (pid, creation_time) 缓存，调用方可传 deadline/cancel。
"""
from __future__ import annotations

import dataclasses
import struct
import time

from .livenames import Registry
from .paramcatalog import ParameterCatalog, bounded_ascii
from .liveparams import iter_heap_regions

#: 只发现结构头；不是猜测地址，且必须命中 ADBE Effect Parade GUID。
_POOL_CACHE = {}
_PARADE_CACHE = {}


def _module(p, name):
    for m in p.modules():
        if m["name"].lower() == name.lower():
            return m
    return None


def _writable_sections(path):
    try:
        import pefile
    except Exception:
        return []
    try:
        pe = pefile.PE(path, fast_load=True)
        try:
            return [(s.VirtualAddress, s.Misc_VirtualSize)
                    for s in pe.sections
                    if s.Characteristics & 0x80000000
                    and not s.Characteristics & 0x20000000]
        finally:
            pe.close()
    except Exception:
        return []


def _candidate_names(p, buckets, count, limit=32):
    heads = struct.unpack("<" + "Q" * count, p.read(buckets, count * 8))
    names = []
    for bucket in heads:
        if not bucket:
            continue
        node = p.u64(bucket)
        if not node:
            continue
        data = p.read(node, 80)
        ptr = struct.unpack_from("<Q", data, 16)[0] or node + 24
        try:
            # 名字可能贴近页尾（实测距页尾 72B）；固定 96B 读会跨页失败。
            txt = bounded_ascii(p, ptr, limit=256, block=32)
        except (OSError, RuntimeError, UnicodeDecodeError):
            continue
        if not txt:
            continue
        names.append(txt)
        if len(names) >= limit:
            break
    return names


def _pool_has_parade(p, buckets, count, deadline=None):
    """完整 match_pool 校验，不能只看前 12 个样本。"""
    heads = struct.unpack("<" + "Q" * count, p.read(buckets, count * 8))
    seen = set()
    for bucket in heads:
        if deadline and time.monotonic() > deadline:
            raise TimeoutError("match-pool 校验超时")
        node = p.u64(bucket) if bucket else 0
        while node and node not in seen:
            if len(seen) > 200000:
                raise RuntimeError("match-pool 节点过多")
            seen.add(node)
            data = p.read(node, 80)
            ptr = struct.unpack_from("<Q", data, 16)[0] or node + 24
            try:
                txt = bounded_ascii(p, ptr, limit=256, block=32)
            except UnicodeDecodeError:
                # 合法 pool 里个别内部名非 ASCII：按原 match_pool 约定跳过。
                node = struct.unpack_from("<Q", data, 0)[0]
                continue
            except (OSError, RuntimeError):
                return False
            if txt == "ADBE Effect Parade":
                return True
            node = struct.unpack_from("<Q", data, 0)[0]
    return False


def discover_match_pool(p, profile, deadline=None):
    """TDB 可写节头扫描：返回唯一含 ADBE Effect Parade 的 pool RVA。"""
    key = (int(p.pid), int(getattr(p, "creation_time", 0) or 0))
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]
    tdb = _module(p, "tdb.dll")
    if not tdb:
        raise RuntimeError("TDB.dll 未加载")
    candidates = []
    for rva, size in _writable_sections(tdb["path"]):
        try:
            raw = p.read(tdb["base"] + rva, size)
        except Exception:
            continue
        for at in range(0, max(0, len(raw) - 48), 8):
            if deadline and time.monotonic() > deadline:
                raise TimeoutError("match-pool 发现超时")
            count = struct.unpack_from("<Q", raw, at + 8)[0]
            buckets = struct.unpack_from("<Q", raw, at + 40)[0]
            if not 16 <= count <= 65536 or count & (count - 1):
                continue
            if buckets < 0x10000:
                continue
            try:
                names = _candidate_names(p, buckets, count)
                # 样本只做结构有效 + ADBE 锚点；不能要求前 12 项恰含 Parade。
                if len(names) < 6 or not any(n.startswith("ADBE ") for n in names):
                    continue
                if not _pool_has_parade(p, buckets, count, deadline=deadline):
                    continue
            except TimeoutError:
                raise
            except (OSError, RuntimeError, struct.error):
                # 坏候选头是正常的；继续扫描，不能把整个发现判失败。
                continue
            candidates.append((rva + at, count, tuple(names)))
    # 唯一性：同一构建出现多个同名 pool 就拒绝猜。
    uniq = {c[0]: c for c in candidates}
    if len(uniq) != 1:
        raise RuntimeError("match-pool 候选不唯一(%d)" % len(uniq))
    rva, count, names = next(iter(uniq.values()))
    _POOL_CACHE[key] = rva
    return rva


def profile_with_pool(p, profile, deadline=None):
    pool = int(getattr(profile, "match_pool", 0) or 0)
    if not pool:
        pool = discover_match_pool(p, profile, deadline=deadline)
    return dataclasses.replace(profile, match_pool=pool)


def _valid_parade(p, cat, address) -> bool:
    try:
        begin, end = struct.unpack(
            "<QQ", p.read(address + cat.profile.group_vector, 16))
        if not begin or end <= begin or (end - begin) % 8 or end - begin > 4096:
            return False
        raw = p.read(begin, end - begin)
        for root in struct.unpack("<" + "Q" * ((end - begin) // 8), raw):
            if not root or p.u64(root + 32) != address:
                continue
            for child, guid in cat.children(root):
                if (child and p.read(child + 8, 16) == guid
                        and p.u64(child + 32) == root):
                    return True
    except (OSError, RuntimeError, struct.error):
        return False
    return False


def discover_parades(p, cat, deadline=None, cancel=None, *, rescan_interval=5.0):
    """真实 parade GUID 的增量搜索：小块优先、cursor 续扫、保留多 parade。"""
    key = (int(p.pid), int(getattr(p, "creation_time", 0) or 0),
           int(cat.profile.match_pool or 0))
    needles = [k for k, v in cat.names.items() if v == "ADBE Effect Parade"]
    if not needles:
        raise RuntimeError("match-pool 中没有 ADBE Effect Parade")
    state = _PARADE_CACHE.get(key)
    now = time.monotonic()
    if state is None:
        state = {"regions": sorted(iter_heap_regions(p), key=lambda r: r[1]),
                 "idx": 0, "off": 0, "parades": set(), "done": False,
                 "next_rescan": 0.0}
        _PARADE_CACHE[key] = state
    elif state.get("done") and now >= float(state.get("next_rescan") or 0.0):
        # done 后定期刷新私有区快照；保留旧 parades，从新 regions 起点
        # 做有界增量扫描，避免新图层/新 parade 永远看不到。
        state["regions"] = sorted(iter_heap_regions(p), key=lambda r: r[1])
        state["idx"] = 0
        state["off"] = 0
        state["done"] = False
    # 会话缓存不能盲信：每次重验，失效 parade 丢掉；若旧会话 parade
    # 全部失效（切项目/同 PID 重开），cursor 归零重新做有界发现。
    state["parades"] = {a for a in state["parades"] if _valid_parade(p, cat, a)}
    if state.get("done") and not state["parades"]:
        # 旧 parade 全失效时必须连私有区快照一起刷新；只重置游标会在
        # 旧 regions 上空转，新区域里的 parade 永远看不到。
        state["regions"] = sorted(iter_heap_regions(p), key=lambda r: r[1])
        state.update({"idx": 0, "off": 0, "done": False})
    if state.get("done"):
        # 已完成且未到重扫时间：这是缓存命中，不是一次真实扫描。
        # 不能在这里重设 next_rescan，否则每秒调用会把重扫窗口无限
        # 往后推，否则定期重扫永远不会触发。
        return set(state["parades"])
    while state["idx"] < len(state["regions"]):
        base, size = state["regions"][state["idx"]]
        offset = int(state["off"])
        while offset < size:
            if deadline and time.monotonic() > deadline:
                state["off"] = offset
                return set(state["parades"])
            if cancel and cancel():
                state["off"] = offset
                return set(state["parades"])
            chunk = min(1 << 20, size - offset)
            try:
                data = p.read(base + offset,
                              min((1 << 20) + 32, size - offset))
            except (OSError, RuntimeError, ValueError):
                # Heap/protection snapshot can go stale between VirtualQuery
                # and ReadProcessMemory. Skip this chunk and advance cursor;
                # this is not an AE-death signal and must not pin retry.
                offset += max(1, chunk)
                state["off"] = offset
                continue
            for needle in needles:
                at = data.find(needle)
                while at >= 0:
                    address = base + offset + at - 8
                    at = data.find(needle, at + 1)
                    if address % 8 or not address:
                        continue
                    if _valid_parade(p, cat, address):
                        state["parades"].add(address)
            offset += 1 << 20
        state["idx"] += 1
        state["off"] = 0
    state["done"] = True
    state["next_rescan"] = time.monotonic() + rescan_interval
    return set(state["parades"])


def parade_scan_pending(p, cat) -> bool:
    """本次 parade 搜索是否还有未扫区域（调用方应续步）。"""
    key = (int(p.pid), int(getattr(p, "creation_time", 0) or 0),
           int(cat.profile.match_pool or 0))
    st = _PARADE_CACHE.get(key)
    return bool(st is not None and not st.get("done"))


def parameter_rows(p, data_root, deadline=None, cancel=None,
                   with_state: bool = False, *, rescan_interval: float = 5.0,
                   parade_scan_budget=None):
    """返回当前参数；单独指定的 parade 预算从目录准备完成后开始计时。"""
    reg = Registry(p, data_root)
    profile = profile_with_pool(p, reg.profile, deadline=deadline)
    cat = ParameterCatalog(p, data_root=data_root, profile=profile)
    scan_deadline = deadline if parade_scan_budget is None else time.monotonic() + parade_scan_budget
    parades = discover_parades(p, cat, deadline=scan_deadline, cancel=cancel,
                              rescan_interval=rescan_interval)
    pending = parade_scan_pending(p, cat)
    if not parades:
        if pending:
            return (profile, [], set(parades), True) if with_state else (
                profile, [], set(parades))
        # A verified host may be empty during startup/project loading.
        # Keep scanning; this is not an unsupported popup build.
        return (profile, [], set(), False) if with_state else (profile, [], set())
    cat.scan_parades = lambda progress=None: set(parades)
    # 不再缓存 rows 快照：删除/新加参数、切项目必须当下向量可见。
    rows = cat.rows()
    if with_state:
        return profile, rows, parades, pending
    return profile, rows, parades


def clear_cache(pid=None):
    for cache in (_POOL_CACHE, _PARADE_CACHE):
        if pid is None:
            cache.clear()
        else:
            for k in [k for k in cache if k[0] == int(pid)]:
                cache.pop(k, None)
