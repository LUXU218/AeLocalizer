"""测试用的假 AE 进程:稀疏内存区 + 真实临时模块文件。

配合 core.adapter / core.livenames / core.paramcatalog 的离线测试:
- 模块文件是真实临时文件,module_digest 的 SHA-256 校验走真路径;
- 内存是稀疏字典式区域,read/write_buffer/read_into 行为对齐真实 Process
  (写入 ≤48 字节 + 回读校验;读不到整段就报错);
- 效果目录按 profile 布局摆好,host_catalog 给 registry_layout 做交叉校验。

测试里用 mock.patch 把 core.livenames 的 file_version / list_ae_processes /
Process / main_window 指到这里,即可全离线跑完写入/回滚/恢复流程。
"""
from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

FLT_BASE = 0x7FF000000000
TDB_BASE = FLT_BASE + 0x100000
HEAP_BASE = 0x10000000
HEAP_SIZE = 0x400000

MANAGER_ADDR = HEAP_BASE + 0x1000
VECTOR_ADDR = HEAP_BASE + 0x100000
OBJ_STRIDE = 0x200
OBJ_BASE = HEAP_BASE + 0x20000
PARADE_ADDR = HEAP_BASE + 0x200000
PARADE_NAME = "ADBE Effect Parade"
ROOT_BASE = HEAP_BASE + 0x280000
ROOT_STRIDE = 0x4000
POOLNODE_BASE = HEAP_BASE + 0x300000
POOLNODE_STRIDE = 0x100
LISTNODE_BASE = HEAP_BASE + 0x380000
LISTNODE_STRIDE = 0x100
STREAM_BASE = HEAP_BASE + 0x3A0000
STREAM_STRIDE = 0x400


class FakeProcess:
    def __init__(self, fake, pid=4242, write=False):
        self.fake = fake
        self.pid = pid
        self.creation_time = fake.creation_time
        self.image = fake.image
        self.write_enabled = write
        self.write_calls = 0
        self.fail_next_write = False
        self.fail_on_write = 0  # 第 N 次写入抛错(0 = 不注入故障),测部分回滚用

    # ---- Process 协议 ----
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ensure_alive(self):
        pass

    def _region(self, address, size):
        for base, data in self.fake.regions.items():
            if base <= address and address + size <= base + len(data):
                return base, data
        raise RuntimeError(f"AE 内存读取失败(假进程无此区域:{address:#x}+{size})")

    def read(self, address: int, size: int) -> bytes:
        base, data = self._region(address, size)
        offset = address - base
        return bytes(data[offset : offset + size])

    def read_into(self, address: int, buffer, size=None):
        view = memoryview(buffer)
        count = view.nbytes if size is None else size
        base, data = self._region(address, count)
        offset = address - base
        view[:count] = data[offset : offset + count]
        return count

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def write_buffer(self, address: int, data: bytes):
        if not 1 <= len(data) <= 48:
            raise ValueError("拒绝超出名称字段范围的写入")
        if self.fail_next_write:
            self.fail_next_write = False
            raise RuntimeError("名称写入失败(注入的故障)")
        if self.fail_on_write and self.write_calls + 1 == self.fail_on_write:
            raise RuntimeError("名称写入失败(第 %d 次,注入的故障)" % self.fail_on_write)
        self.write_calls += 1
        base, region = self._region(address, len(data))
        offset = address - base
        region[offset : offset + len(data)] = data
        if self.read(address, len(data)) != data:
            raise RuntimeError("名称写入后回读不一致")

    def modules(self):
        return self.fake.modules


def _name_field(text: str, encoding: str, size: int) -> bytes:
    value = text.encode(encoding) if encoding != "mbcs" else text.encode("mbcs")
    return (value + b"\0").ljust(size, b"\0")


class FakeAE:
    """一个可写入的假 AE:按 profile 布局摆好效果目录。"""

    def __init__(self, tmp_path, version="25.5.0.66", encoding="mbcs", effects=None, save=True, with_params=False):
        from core.adapter import Profile, save_local

        self.tmp_path = Path(tmp_path)
        self.version = version
        self.encoding = encoding
        self.creation_time = 133400000000000000
        self.image = str(self.tmp_path / "AfterFX.exe")
        self.pid = 4242

        # 真实临时 FLT.dll,让 module_digest 的哈希链路走真路径
        support = self.tmp_path / "Support Files"
        support.mkdir(parents=True, exist_ok=True)
        flt_path = support / "FLT.dll"
        flt_bytes = bytes(range(256)) * 16
        flt_path.write_bytes(flt_bytes)
        self.flt_path = flt_path
        flt_sha = hashlib.sha256(flt_bytes).hexdigest()

        self.profile = Profile(
            version=version,
            flt_sha=flt_sha,
            manager=0x1000,
            registry_vector=0x20,
            fc_vtable=0x2000,
            name_get=0x3000,
            match_get=0x3010,
            category_get=0x3020,
            tdb_sha=flt_sha,
            root_vtable=0x2010,
            parent_get=0x3100,
            stream_match_get=0x3110,
            count_get=0x3120,
            count_bytes=b"\x8b\x81\x02\x00\x00\x00\xc3",
            display_get=0x3130,
            match_pool=0x20,
            display_cache=0xF0,
            group_vector=0x220,
            encoding=encoding,
            enabled=True,
        )
        if save:
            save_local(self.tmp_path, {version: self.profile}, merge=False)

        names = effects or [
            ("S_Glow", "辉光", "Sapphire"),
            ("S_Blur", "模糊", "Sapphire"),
            ("CC Snowfall", "下雪", "Cycore"),
            ("BCC Dissolve", "溶解", "BCC"),
        ]
        self.effects = []  # (displayName, matchName, category)
        for index, (match, display, category) in enumerate(names):
            self.effects.append((display, match, category))

        # 内存区域:FLT 模块镜像 + 堆
        self.flt = bytearray(0x10000)
        self.heap = bytearray(HEAP_SIZE)
        self.regions = {FLT_BASE: self.flt, HEAP_BASE: self.heap}

        def put(base, offset, data):
            self.regions[base][offset : offset + len(data)] = data

        # getter 指令形态
        put(FLT_BASE, self.profile.name_get, b"\x48\x8d\x41\x10\xc3")
        put(FLT_BASE, self.profile.match_get, b"\x48\x8d\x41\x40\xc3")
        put(FLT_BASE, self.profile.category_get, b"\x48\x8d\x41\x60\xc3")

        # 对象表(虚表指回 FLT 基址 + fc_vtable)
        vtable = struct.pack("<Q", FLT_BASE + self.profile.fc_vtable)
        entries = []
        for index, (display, match, category) in enumerate(self.effects):
            address = OBJ_BASE + index * OBJ_STRIDE
            obj = bytearray(160)
            obj[0:8] = vtable
            obj[16:64] = _name_field(display, encoding, 48)
            obj[64:96] = _name_field(match, encoding, 32)
            obj[96:160] = _name_field(category, encoding, 64)
            put(HEAP_BASE, address - HEAP_BASE, bytes(obj))
            entries.append(address)
        self.object_addresses = entries

        # 注册表向量:每项 16 字节(指针 + 8 字节填充)
        vector = b"".join(struct.pack("<Q", address) + b"\0" * 8 for address in entries)
        put(HEAP_BASE, VECTOR_ADDR - HEAP_BASE, vector)
        begin = VECTOR_ADDR
        end = VECTOR_ADDR + len(vector)
        put(HEAP_BASE, MANAGER_ADDR - HEAP_BASE + self.profile.registry_vector, struct.pack("<QQ", begin, end))

        # FLT 模块管理器指针槽
        put(FLT_BASE, self.profile.manager, struct.pack("<Q", MANAGER_ADDR))

        tdb_path = support / "TDB.dll"
        tdb_path.write_bytes(flt_bytes)  # 同字节:tdb_sha 与 flt_sha 相同
        self.tdb_path = tdb_path
        self.modules = [
            {"base": FLT_BASE, "path": str(self.flt_path), "name": "FLT.dll"},
            {"base": FLT_BASE + 0x100000, "path": str(tdb_path), "name": "TDB.dll"},
        ]

        self.param_spec = {}
        if with_params:
            self._build_params()

        self.processes = [
            {"pid": self.pid, "image": self.image, "creation_time": self.creation_time, "fileVersion": self.version}
        ]

    def _build_params(self):
        """参数侧假结构:TDB match 池 + parade + 每效果流组 + 参数流。

        每个效果两条参数:一条短名(显示名走 SSO 内联),一条长名(走堆指针)。"""
        import hashlib

        tdb = bytearray(0x10000)
        self.regions[TDB_BASE] = tdb

        def key16(name: str) -> bytes:
            return hashlib.md5(("param:" + name).encode("ascii")).digest()[:16]

        def put_tdb(offset, data):
            tdb[offset : offset + len(data)] = data

        # getter 指令形态(parent / stream_match / count)
        put_tdb(self.profile.parent_get, b"\x48\x8b\x41\x20\xc3")
        put_tdb(self.profile.stream_match_get, b"\x48\x8d\x41\x08\xc3")
        put_tdb(self.profile.count_get, self.profile.count_bytes)

        # match 池:count(2 的幂) 在 +8,桶数组指针在 +40
        pool_entries = list({m for _d, m, _c in self.effects}) + [PARADE_NAME]
        self.param_spec = {}
        for _display, match, _category in self.effects:
            self.param_spec[match] = [
                ("Amount", "Amount", "Amount"),
                ("Opacity Param", "OpacityParam", "Opacity Param Long"),
            ]
            self.param_spec[match] = [
                (pf, pf, display) for pf, _x, display in self.param_spec[match]
            ]
            pool_entries += [pf for pf, _d, _s in self.param_spec[match]]
        count = 1
        while count < len(pool_entries):
            count *= 2

        # 池节点:80 字节,next +0 / 字符串指针 +16(0 → 节点+24)/ key +64..80
        bucket_array_addr = HEAP_BASE + 0x35F000
        nodes = []
        for index, name in enumerate(dict.fromkeys(pool_entries)):
            address = POOLNODE_BASE + index * POOLNODE_STRIDE
            node = bytearray(80)
            node[24 : 24 + len(name) + 1] = name.encode("ascii") + b"\0"
            node[64:80] = key16(name)
            off = address - HEAP_BASE
            self.heap[off : off + 80] = bytes(node)
            nxt = nodes[-1] if nodes else 0
            # 桶内链表:新节点指向前一个(逆序);walker 顺着 next 走到 0 停止
            self.heap[off : off + 8] = struct.pack("<Q", nxt)
            nodes.append(address)

        # 桶槽是指向指针单元的二级指针:walker 会先解引用一次再走链
        cell_addr = HEAP_BASE + 0x35E000
        self.heap[cell_addr - HEAP_BASE : cell_addr - HEAP_BASE + 8] = struct.pack("<Q", nodes[-1])
        buckets = struct.pack("<Q", cell_addr) + b"\0" * 8 * (count - 1)
        self.heap[bucket_array_addr - HEAP_BASE : bucket_array_addr - HEAP_BASE + len(buckets)] = buckets
        put_tdb(self.profile.match_pool + 8, struct.pack("<Q", count))
        put_tdb(self.profile.match_pool + 40, struct.pack("<Q", bucket_array_addr))

        # parade:+8 名字 key;+group_vector → roots 向量
        roots = []
        for index, (_display, match, _category) in enumerate(self.effects):
            roots.append(ROOT_BASE + index * ROOT_STRIDE)
        roots_vector_addr = HEAP_BASE + 0x360000
        vector = b"".join(struct.pack("<Q", r) for r in roots)
        self.heap[roots_vector_addr - HEAP_BASE : roots_vector_addr - HEAP_BASE + len(vector)] = vector
        self.heap[PARADE_ADDR - HEAP_BASE + 8 : PARADE_ADDR - HEAP_BASE + 24] = key16(PARADE_NAME)
        self.heap[PARADE_ADDR - HEAP_BASE + self.profile.group_vector : PARADE_ADDR - HEAP_BASE + self.profile.group_vector + 16] = struct.pack(
            "<QQ", roots_vector_addr, roots_vector_addr + len(vector)
        )

        # 每个效果:根对象 + 命名组链表 + 参数流
        stream_index = 0
        for index, (_display, match, _category) in enumerate(self.effects):
            root = roots[index]
            head = HEAP_BASE + 0x370000 + index * 0x40
            param_list = self.param_spec[match]
            node_addrs = [LISTNODE_BASE + j * LISTNODE_STRIDE for j in range(stream_index, stream_index + len(param_list))]
            self.heap[root - HEAP_BASE : root - HEAP_BASE + 8] = struct.pack("<Q", FLT_BASE + self.profile.root_vtable)
            self.heap[root - HEAP_BASE + 8 : root - HEAP_BASE + 24] = key16(match)
            self.heap[root - HEAP_BASE + 32 : root - HEAP_BASE + 40] = struct.pack("<Q", PARADE_ADDR)
            self.heap[root - HEAP_BASE + self.profile.named_map : root - HEAP_BASE + self.profile.named_map + 8] = struct.pack("<Q", head)
            # 链表头:head.next → node0 → node1 → head
            self.heap[head - HEAP_BASE : head - HEAP_BASE + 8] = struct.pack("<Q", node_addrs[0])
            for j, node_addr in enumerate(node_addrs):
                _pf, _pf2, display = param_list[j]
                stream = STREAM_BASE + stream_index * STREAM_STRIDE
                nxt = node_addrs[j + 1] if j + 1 < len(node_addrs) else head
                node = bytearray(40)
                node[0:8] = struct.pack("<Q", nxt)
                node[16:32] = key16(param_list[j][0])
                node[32:40] = struct.pack("<Q", stream)
                self.heap[node_addr - HEAP_BASE : node_addr - HEAP_BASE + 40] = bytes(node)
                # 参数流:+8 guid;+0x20 parent;+0x40 PF 原名;+display_cache 显示名
                self.heap[stream - HEAP_BASE + 8 : stream - HEAP_BASE + 24] = key16(param_list[j][0])
                self.heap[stream - HEAP_BASE + 0x20 : stream - HEAP_BASE + 0x28] = struct.pack("<Q", root)
                pf_bytes = param_list[j][1].encode("mbcs" if self.encoding == "mbcs" else "utf8")
                self.heap[stream - HEAP_BASE + 0x40 : stream - HEAP_BASE + 0x40 + len(pf_bytes) + 1] = pf_bytes + b"\0"
                cache = bytearray(32)
                chars = display.encode("utf-16le")
                if len(display) <= 7:  # SSO:字符内联在缓存头部
                    cache[0 : len(chars)] = chars
                else:  # 堆:指针在 +0,字符另放
                    chars_addr = HEAP_BASE + 0x3F0000 + stream_index * 64
                    self.heap[chars_addr - HEAP_BASE : chars_addr - HEAP_BASE + len(chars) + 2] = chars + b"\0\0"
                    cache[0:8] = struct.pack("<Q", chars_addr)
                struct.pack_into("<QQ", cache, 16, len(display), max(len(display), 8) if len(display) > 7 else len(display))
                self.heap[stream - HEAP_BASE + self.profile.display_cache : stream - HEAP_BASE + self.profile.display_cache + 32] = bytes(cache)
                stream_index += 1

    # ---- host_catalog:registry_layout 的对照面 ----
    @property
    def host_catalog(self):
        return [{"displayName": d, "matchName": m, "category": c} for d, m, c in self.effects]

    def process(self, write=False):
        cache = getattr(self, "_procs", None)
        if cache is None:
            cache = self._procs = {}
        key = bool(write)
        if key not in cache:
            cache[key] = FakeProcess(self, write=write)
        return cache[key]

    def display_name_bytes(self, index):
        """第 index 个效果的显示名字段当前字节(断言用)。"""
        address = self.object_addresses[index] + 16
        return self.process().read(address, 48)

    def set_display_name_bytes(self, index, raw: bytes):
        address = self.object_addresses[index] + 16
        base, region = self.process()._region(address, len(raw))
        region[address - base : address - base + len(raw)] = raw

    def current_display_name(self, index, encoding=None):
        raw = self.display_name_bytes(index)
        return raw.split(b"\0", 1)[0].decode(encoding or self.encoding)

    # ---- 便捷补丁入口 ----
    def patches(self):
        from unittest import mock

        def fake_version(_path):
            return self.version

        return mock.patch.multiple(
            "core.livenames",
            file_version=fake_version,
            list_ae_processes=lambda: list(self.processes),
            Process=lambda pid, write=False: self.process(write=write),
            main_window=lambda pid: 1,
        )
