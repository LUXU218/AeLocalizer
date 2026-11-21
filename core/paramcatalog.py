"""AE 参数目录主动枚举:只读扫描运行中 AE 的内存,列出全部已加载效果的
全部参数(显示名 + 底层原名),不再依赖"用户逐个点过效果"的被动采集。

读路径全部用跨进程 RPM,不注入、不写入、不需要脚本权限:
  TDB 内部标识池(哈希表)→ parade 根(全内存按虚表指针扫描+GUID 校验)
  → 每个效果的效果流组 → 每条参数流(显示名 wstring 缓存 + PF 内联原名)。
布局字段来自 core.adapter 的 profile(TDB 侧),构造时做 getter 指令校验,
不符即拒绝。移植自 After-Effects-Plugin-Localizer 的参数读取路径(MIT,
Copyright (c) 2026 verse),单线程简化,去掉统计与缓存层。
"""
from __future__ import annotations

import json
import struct

from .proc import MBI, virtual_query  # noqa: F401  (virtual_query 顶层暴露,便于测试拦截)

BLOCK = 4 * 1024 * 1024
LIMIT = 0x7FFFFFFEFFFF
PARADE_NAME = "ADBE Effect Parade"
MAX_STREAMS = 8192


def bounded_ascii(p, address: int, limit: int = 256, block: int = 32) -> str:
    """跨进程读 ASCII 标识:每次读都限在一页内,读不动就缩窗重试。"""
    raw = bytearray()
    while len(raw) < limit:
        at = address + len(raw)
        size = min(block, limit - len(raw), 4096 - (at % 4096))
        while True:
            try:
                data = p.read(at, size)
                break
            except (OSError, RuntimeError):
                if size == 1:
                    raise
                size = max(1, size // 2)
        end = data.find(b"\0")
        if end >= 0:
            return bytes(raw + data[:end]).decode("ascii")
        raw.extend(data)
    raise RuntimeError("参数内部标识未终止")


def _display_name(p, address: int, offset: int, pf_name: str):
    """Best-effort read of the parameter display-name cache.

    Returns (name, True) on a consistent cache and (pf_name, False) when the
    layout is unknown (offset 0) or inconsistent.  Callers keep the row:
    address and matchName are what translation needs, and the row's
    guid/parent ownership was already checked, so a bad cache must not abort
    the whole enumeration.
    """
    if not offset:
        return pf_name, False
    try:
        cache = p.read(address + offset, 32)
        length, capacity = struct.unpack_from("<QQ", cache, 16)
        if not 0 <= length <= capacity <= 4096:
            return pf_name, False
        if not length:
            return pf_name, True
        pointer = (address + offset if capacity <= 7
                   else struct.unpack_from("<Q", cache)[0])
        text = p.read(pointer, (length + 1) * 2)
        if text[-2:] != b"\0\0":
            return pf_name, False
        return text[:-2].decode("utf-16le").split("\x00")[0], True
    except (OSError, RuntimeError, struct.error):
        return pf_name, False


def rows_from_effect_roots(p, roots, profile, limit: int = 5000,
                           display_off: int = 0) -> list:
    """已知效果根 → 参数流显示缓存行（不依赖 TDB 标识池/root_vtable）。

    只读参数树容器和 display_cache；用于构建未适配 TDB 标识池时补写
    已有参数 label。roots 必须来自 LiveParams 的效果 parade 根。
    display_off 可由 LiveParams.display_off 校准值传入（本机 profile 可能为 0）。
    """
    display_off = int(display_off or getattr(profile, "display_cache", 0) or 0)
    if not profile or not display_off:
        raise RuntimeError("参数 UI 显示缓存布局未知")
    named_map = int(getattr(profile, "named_map", 0x228) or 0x228)
    group_vector = int(getattr(profile, "group_vector", 0) or 0)
    out = []

    def children(root):
        if getattr(profile, "named_vector", False):
            begin, end = struct.unpack("<QQ", p.read(root + group_vector, 16))
            if end < begin or (end - begin) % 8 or end - begin > MAX_STREAMS * 8:
                raise RuntimeError("参数向量结构不符")
            for address in struct.unpack(
                    "<" + "Q" * ((end - begin) // 8), p.read(begin, end - begin)):
                if address:
                    yield address, p.read(address + 8, 16)
        elif getattr(profile, "named_tree", False):
            head = p.u64(root + named_map)
            size = p.u64(root + named_map + 8)
            seen = set()
            at = p.u64(head)
            while at and at != head and len(seen) < MAX_STREAMS:
                if at in seen:
                    raise RuntimeError("参数树循环")
                seen.add(at)
                b = p.read(at, 0x38)
                if b[0x19] not in (0, 1):
                    raise RuntimeError("参数树结构不符")
                yield struct.unpack_from("<Q", b, 0x30)[0], b[0x20:0x30]
                at = struct.unpack_from("<Q", b, 0)[0]
            if size > MAX_STREAMS:
                raise RuntimeError("参数树过大")
        else:
            head = p.u64(root + named_map)
            node = p.u64(head)
            seen = set()
            while node and node != head:
                if node in seen or len(seen) >= MAX_STREAMS:
                    raise RuntimeError("参数列表循环或过大")
                seen.add(node)
                b = p.read(node, 40)
                yield struct.unpack_from("<Q", b, 32)[0], b[16:32]
                node = struct.unpack_from("<Q", b, 0)[0]

    for root in roots:
        try:
            for address, guid in children(int(root)):
                if len(out) >= limit:
                    return out
                if not address:
                    continue
                if p.read(address + 8, 16) != guid or p.u64(address + 32) != int(root):
                    continue
                label = p.read(address + 0x40, 32)
                if b"\x00" not in label:
                    continue
                try:
                    pf_name = label.split(b"\x00", 1)[0].decode(
                        getattr(profile, "encoding", "mbcs"))
                except UnicodeError:
                    continue
                if not pf_name:
                    continue
                off = display_off
                cache = p.read(address + off, 32)
                length, capacity = struct.unpack_from("<QQ", cache, 16)
                if not 0 <= length <= capacity <= 4096:
                    raise RuntimeError("参数显示缓存结构不匹配")
                name = pf_name
                if length:
                    pointer = address + off if capacity <= 7 else struct.unpack_from("<Q", cache, 0)[0]
                    text = p.read(pointer, (length + 1) * 2)
                    if text[-2:] != b"\0\0":
                        raise RuntimeError("参数显示缓存未终止")
                    try:
                        name = text[:-2].decode("utf-16le").split("\x00")[0]
                    except UnicodeDecodeError:
                        name = pf_name
                out.append({"effectRoot": int(root), "address": address,
                            "name": name, "pfName": pf_name})
        except (OSError, RuntimeError):
            continue
    return out


class ParameterCatalog:
    def __init__(self, p, data_root=None, profile=None):
        from pathlib import Path as _Path

        from .livenames import Registry
        from .proc import module_digest

        self.p = p
        registry = Registry(p, data_root, profile=profile)  # 独立钉死 FLT 哈希与 getter
        self.profile = registry.profile
        if not self.profile.count_get or not self.profile.root_vtable or not self.profile.match_pool:
            raise RuntimeError("此构建未适配参数枚举(缺少 TDB 布局);效果名实时写入不受影响")
        mods = {_Path(m["path"]).name.lower(): m for m in registry.modules}
        self.flt = mods["flt.dll"]["base"]
        tdb = mods.get("tdb.dll")
        if not tdb:
            raise RuntimeError("AE 尚未加载 TDB.dll,参数枚举已停止")
        self.tdb = tdb["base"]
        if module_digest(p, tdb["path"]) != self.profile.tdb_sha:
            raise RuntimeError("TDB.dll 构建不匹配,参数枚举已停止")
        for rva, expected in (
            (self.profile.parent_get, bytes.fromhex("488b4120c3")),
            (self.profile.stream_match_get, bytes.fromhex("488d4108c3")),
            (self.profile.count_get, self.profile.count_bytes),
        ):
            if not expected or p.read(self.tdb + rva, len(expected)) != expected:
                raise RuntimeError("参数结构接口不匹配,未执行读取")
        self.names = self.match_pool()
        self.display_cache_fallbacks = 0

    # ---- TDB 内部标识池 ----
    def match_pool(self) -> dict:
        p = self.p
        pool = self.tdb + self.profile.match_pool
        count = p.u64(pool + 8)
        if not 1 <= count <= 1048576 or count & (count - 1):
            raise RuntimeError("参数内部标识池结构不匹配")
        buckets = p.read(p.u64(pool + 40), count * 8)
        seen = set()
        names = {}
        for bucket in struct.unpack("<" + "Q" * count, buckets):
            node = p.u64(bucket) if bucket else 0
            while node and node not in seen:
                if len(seen) > 200000:
                    raise RuntimeError("参数内部标识池过大")
                seen.add(node)
                b = p.read(node, 80)
                ptr = struct.unpack_from("<Q", b, 16)[0] or node + 24
                # 插件的非 ASCII 内部名(汉化版自带中文名)跳过:列不出就不列
                try:
                    names[b[64:80]] = bounded_ascii(p, ptr)
                except UnicodeDecodeError:
                    pass
                node = struct.unpack_from("<Q", b)[0]
        return names

    # ---- parade 根发现(全内存只读扫描) ----
    def scan_parades(self, progress=None):
        p = self.p
        pattern = struct.pack("<Q", self.flt + self.profile.root_vtable)
        parades = set()
        confirmed = set()
        seen_parents = set()
        p.ensure_alive()
        scratch = bytearray(BLOCK + 7)
        address = 0
        regions = 0
        while address < LIMIT:
            m = virtual_query(p, address)
            if m is None:
                break
            base = m.BaseAddress or 0
            end = base + m.RegionSize
            if end <= address:
                raise RuntimeError("内存区域枚举未前进")
            address = end
            regions += 1
            if regions % 64 == 0 and progress:
                progress(f"已扫描 {address / (1 << 30):.1f} GiB…")
            if m.State != 0x1000 or m.Type != 0x20000 or (m.Protect & 0x100) or (m.Protect & 0xFF) not in (4, 8):
                continue
            for at in range(base, end, BLOCK):
                p.ensure_alive()
                size = min(BLOCK + 7, end - at)
                try:
                    p.read_into(at, scratch, size)
                except (OSError, RuntimeError):
                    continue
                data = bytes(memoryview(scratch)[:size])
                start = 0
                while True:
                    hit = data.find(pattern, start, size)
                    if hit < 0 or hit >= size - 7:
                        break
                    start = hit + 1
                    if (at + hit) % 8:
                        continue
                    try:
                        parent = struct.unpack_from("<Q", data, hit + 32)[0]
                    except struct.error:
                        continue
                    if not parent or parent in confirmed:
                        if parent:
                            parades.add(parent)
                            confirmed.add(parent)
                        continue
                    if parent in seen_parents:
                        continue
                    seen_parents.add(parent)
                    try:
                        if self.names.get(p.read(parent + 8, 16)) == PARADE_NAME:
                            parades.add(parent)
                            confirmed.add(parent)
                    except (OSError, RuntimeError):
                        continue
        p.ensure_alive()
        return parades

    def roots(self, progress=None):
        p = self.p
        vt = self.flt + self.profile.root_vtable
        parades = self.scan_parades(progress)
        roots = []
        invalid = 0
        for parade in parades:
            try:
                if self.names.get(p.read(parade + 8, 16)) != PARADE_NAME:
                    invalid += 1
                    continue
                begin, end = struct.unpack("<QQ", p.read(parade + self.profile.group_vector, 16))
                if end < begin or (end - begin) % 8 or end - begin > MAX_STREAMS * 8:
                    invalid += 1
                    continue
                for root in struct.unpack("<" + "Q" * ((end - begin) // 8), p.read(begin, end - begin)):
                    if root and p.u64(root) == vt and p.u64(root + 32) == parade:
                        roots.append(root)
            except (OSError, RuntimeError):
                invalid += 1
        return sorted(set(roots))

    def children(self, root: int):
        """效果流组的参数流:(流地址, 16字节标识)。三种容器形态。"""
        p = self.p
        if self.profile.named_vector:
            begin, end = struct.unpack("<QQ", p.read(root + self.profile.group_vector, 16))
            if end < begin or (end - begin) % 8 or end - begin > MAX_STREAMS * 8:
                raise RuntimeError("参数向量结构不符")
            for address in struct.unpack("<" + "Q" * ((end - begin) // 8), p.read(begin, end - begin)):
                if address:
                    yield address, p.read(address + 8, 16)
        elif self.profile.named_tree:
            yield from self._tree_children(root + self.profile.named_map)
        else:
            head = p.u64(root + self.profile.named_map)
            node = p.u64(head)
            seen = set()
            while node != head:
                if node in seen or len(seen) > MAX_STREAMS:
                    raise RuntimeError("参数列表循环或过大,已停止")
                seen.add(node)
                b = p.read(node, 40)
                yield struct.unpack_from("<Q", b, 32)[0], b[16:32]
                node = struct.unpack_from("<Q", b)[0]

    def _tree_children(self, container: int):
        """MSVC std::map 命名组(AE 26.5):头哨兵+大小;节点
        _Left/_Parent/_Right/_Color/_Isnil(+0x19)/key(+0x20)/value(+0x30)。"""
        p = self.p

        def node(address):
            b = p.read(address, 0x38)
            if b[0x19] not in (0, 1):
                raise RuntimeError("参数树结构不符")
            return b

        # ★ 2026-10-28 实测修复:这里原来少了 [0] —— struct.unpack_from 返回
        #   元组,整棵树的地址/跳转值全是 1 元组,真机 ctypes 报
        #   "cannot be converted to pointer";离线测试没抓住(fake 的 read
        #   不挑类型)。
        u64 = lambda b, at: struct.unpack_from("<Q", b, at)[0]  # noqa: E731
        head = p.u64(container)
        size = p.u64(container + 8)
        top = node(head)
        if not top[0x19] or size > MAX_STREAMS:
            raise RuntimeError("参数树结构不符")
        seen = set()
        steps = 0
        at = u64(top, 0)
        while at != head:
            b = node(at)
            if b[0x19] or at in seen or len(seen) >= MAX_STREAMS:
                raise RuntimeError("参数列表循环或过大,已停止")
            seen.add(at)
            yield u64(b, 0x30), b[0x20:0x30]
            right = u64(b, 0x10)
            if not node(right)[0x19]:
                at = right
                while not node(u64(node(at), 0))[0x19]:
                    at = u64(node(at), 0)
                    steps += 1
                    if steps > 65536:
                        raise RuntimeError("参数列表循环或过大,已停止")
            else:
                child, parent = at, u64(b, 8)
                while not node(parent)[0x19] and u64(node(parent), 0x10) == child:
                    child, parent = parent, u64(node(parent), 8)
                    steps += 1
                    if steps > 65536:
                        raise RuntimeError("参数列表循环或过大,已停止")
                at = parent
        if len(seen) != size:
            raise RuntimeError("参数树结构不符")

    # ---- 下拉项采集(2026-10-29,ENUMCAP):流对象上的枚举定义串 ----
    # 与守护读法同源(流对象 + 偏移探测,§5.25):每个参数流探测枚举定义串
    # ('A|B|C' 形态),逐项送补词 —— 之前全量枚举只采集参数名,下拉缺词
    # (如 Glitchify 传输模式的 46 项)永远浮不出来。
    ENUM_PROBE_OFFS = (0x70, 0x68, 0x78, 0x60, 0x80, 0x58, 0x88, 0x50,
                       0x90, 0x48, 0x98, 0x40, 0x500)

    def popup_rows(self) -> list:
        """下拉定义串逐项行:{effectMatchName, item, address}。只读。

        严格校验(指针可读 + NUL 结尾 + 全可打印 ASCII + 管道数 ≥2 +
        每项 1-64 字符含字母),防错误偏移的垃圾串混入。"""
        out = []
        seen = set()
        p = self.p
        for root in self.roots():
            effect = self.names.get(p.read(root + 8, 16))
            if not effect:
                continue
            for address, guid in self.children(root):
                if p.read(address + 8, 16) != guid or p.u64(address + 32) != root:
                    continue
                for off in self.ENUM_PROBE_OFFS:
                    try:
                        ep = p.u64(address + off)
                    except Exception:
                        continue
                    if ep < 0x10000:
                        continue
                    try:
                        blob = p.read(ep, 2048)   # 长下拉串(450 字节)需大读(ENUMCAP2)
                    except Exception:
                        continue
                    nul = blob.find(b"\x00")
                    if nul < 4:
                        continue
                    raw = blob[:nul]
                    if any(b < 32 or b > 126 for b in raw):
                        continue
                    s = raw.decode("ascii")
                    if s.count("|") < 2 or len(s) > 2048:
                        continue
                    key = (effect, s)
                    if key in seen:
                        continue
                    seen.add(key)
                    for item in s.split("|"):
                        item = item.strip()
                        if item and any(c.isalpha() for c in item) and len(item) <= 64:
                            out.append({"effectMatchName": effect,
                                        "item": item,
                                        "address": address})
                    break   # 该流的枚举串已找到(多偏移是同一串的候选位)
        return out

    def rows(self, progress=None) -> list:
        """全部参数行:{effectMatchName, matchName, name, pfName, address, guid, parent}。"""
        result = []
        p = self.p
        encoding = self.profile.encoding
        for root in self.roots(progress):
            effect = self.names.get(p.read(root + 8, 16))
            if not effect:
                continue
            for address, guid in self.children(root):
                if p.read(address + 8, 16) != guid or p.u64(address + 32) != root:
                    raise RuntimeError("参数归属校验失败,已停止")
                param = self.names.get(guid)
                label = p.read(address + 0x40, 32)
                if not param or b"\0" not in label:
                    continue
                try:
                    pf_name = label.split(b"\0", 1)[0].decode(encoding)
                except UnicodeError:
                    pf_name = ""
                offset = self.profile.display_cache
                name, ok = _display_name(p, address, offset, pf_name)
                if not ok:
                    self.display_cache_fallbacks += 1
                # Some parameters (notably checkbox toggles such as
                # "Flip Direction" on CS LineSweep) carry an empty PF label but
                # a perfectly good display-name cache. Dropping every row whose
                # prefName was blank silently removed those parameters from the
                # whole pipeline: they were never enumerated, so they could
                # never be observed or translated, while the 补词 page reported
                # "no missing words" because the dictionary did cover them.
                # Keep the row whenever either source yields a usable label.
                if not name:
                    continue
                result.append(
                    {
                        "effectMatchName": effect,
                        "matchName": param,
                        "name": name,
                        "pfName": pf_name,
                        "address": address,
                        "guid": guid.hex(),
                        "parent": root,
                    }
                )
        return result


def enumerate_parameters(p, data_root=None, progress=None) -> list:
    """对外入口:去重后的公开行(不含地址),按效果与参数名排序。"""
    catalog = ParameterCatalog(p, data_root)
    rows = catalog.rows(progress)
    unique = {}
    for row in rows:
        key = json.dumps([row["effectMatchName"], row["matchName"]], ensure_ascii=False, separators=(",", ":"))
        if key not in unique:
            unique[key] = {
                "effectMatchName": row["effectMatchName"],
                "matchName": row["matchName"],
                "name": row["name"],
                "pfName": row["pfName"],
                "instances": 0,
            }
        unique[key]["instances"] += 1
    return sorted(unique.values(), key=lambda r: (r["effectMatchName"], r["matchName"]))
