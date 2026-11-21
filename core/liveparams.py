# -*- coding: utf-8 -*-
"""不重启 AE 的实时名称切换（只写效果控件标题，不写 FLT 效果注册表）。

为什么可以做到「当场生效」
-------------------------
Ghidra 反编译 TDB_StreamBase::GetDisplayName（TDB.dll RVA 0x31A20）::

    TDB_StreamBase::GetDisplayName(TDB_StreamBase *this) {
      RecursiveMutex::Lock((RecursiveMutex *)(this + 0x180), 0, 60000);
      if (*(longlong *)(this + 0xf8) == 0) {              // 显示名还没生成过
        (**(code **)(*(longlong *)this + 0x160))(this);   // vtable+0x160 = FillInDisplayName
      }
      RecursiveMutex::Unlock((RecursiveMutex *)(this + 0x180));
      return (basic_string<wchar_t, ...> *)(this + 0xe8);  // ★ 名字缓存就在 +0xE8
    }

也就是说每个参数流/效果流对象自己缓存一份 UTF-16 显示名：

    +0x040  32 字节 ANSI 的 PF 名（FLT 参数流才有）
    +0x0E8  std::wstring 缓存 {缓冲指针@+0x00, _Mysize@+0x10, _Myres@+0x18}
    +0x180  RecursiveMutex（保护这段缓存）

界面每次绘制都调 GetDisplayName 取这个引用，所以直接改写这段缓存并同步
_Mysize，正在运行、已经建好的效果会立刻换名，不用重启 AE。

★★ 2026-11-06 关键边界（用户硬要求，勿回退）
-------------------------------------------
AE「效果控件」面板的效果标题走的是这里的 TDB 流显示名缓存；
而 AE 顶部「效果」菜单和「效果和预设」面板的效果名走的是
FLT_FCSpec 注册表 +0x10 的 ANSI 名段（IG5 反编译
FLT_FCSpec::GetEffectName_MB @0x180066b90 → return (char *)(this + 0x10)）。

所以本模块只负责「已有的效果实例显示名」，绝不能去写 FLT_FCSpec 注册表 ——
那正是「顶部菜单也被汉化」的根因（见 core/livenames.py 的说明）。写注册表会让
菜单和面板一起变中文，而且改的是进程内持久目录，必须重启 AE 才恢复英文。

找目标的做法
-----------
不扫「所有内存里像名字的字符串」，而是只找当前真正加载的效果：由效果名
清单（插件目录快照里的 displayName）锚定，再在内存里按 UTF-16 精确匹配，
并用结构校验确认命中处确实是一个流对象：

    u64(cand + off) == 命中地址        （std::wstring 的缓冲指针指回自己）
    u64(cand)      落在 FLT.dll / TDB.dll（vtables 必须在模块代码段）
    读回 +off 的文本 == 名字

★★ 版本无关（2026-11-06）
-------------------------
显示名缓存偏移和效果流根 vtable 随构建变化，不能写死：
    18.2 系列 = 0xE8
    22.6、23.0 = 0xE8（root_vtable 未适配）
    25.3、25.5、26.x = 0xF0 + 各自不同的 root_vtable
本机适配产出的 profile（core/aescan.py）从来没有探测过这两个字段，常常是 0。
旧版把 0xE8 / 0x978F8 当常量用，在 AE 2025 上整体错 8 字节：每个读写都错位 →
一个槽都校验不过 → 「关掉开关还是中文 / 加新效果不生效」在这台机器上根本修不好。
现在优先用 adapter profile 的真值，profile 不足时从真实内存反推
（_calibrate_layout，判据全是结构性的，不是猜）。
"""
from __future__ import annotations

import array
import ctypes as C
import json
import os
import re
import struct
import sys
import time
import threading
from typing import Dict, Iterable, List, Optional

from .proc import Process, virtual_query

#: 显示名 wstring 缓存的默认偏移（AE 18.2/22.6/23.0 实测值）。
#: 25/26 上是 0xF0 —— 运行时要走 display_offset(profile) 取真值。
DISPLAY_OFF = 0xE8
PF_NAME_OFF = 0x40
INLINE_CAP = 7          # MSVC wstring 的 SSO 容量（16 字节内联 / 2 字节宽字符 - 1）
MAX_NAME_BYTES = 512    # 单个显示名上限，超过视为异常数据
CHUNK = 8 << 20

SESSION_FILE = "live_names.json"

# ---- 进程级缓存（LiveParams 一次调用一个实例，状态必须挂在模块上） --------

#: 「profile 里的偏移确实能用」的会话内缓存 {(pid, image, off, vt): bool}
_LAYOUT_OK: Dict[tuple, bool] = {}
#: 已校准好的布局 {(pid, 创建时间, 镜像): (display_off, root_vt, note)}
_LAYOUT_MEMO: Dict[tuple, tuple] = {}
#: 校准失败计数 / 下次最早重试时刻
_LAYOUT_FAILS: Dict[tuple, int] = {}
_LAYOUT_RETRY_AT: Dict[tuple, float] = {}
#: 已扫到的根流地址（流对象在同一个 AE 会话里不会搬家）
_ROOTS_MEMO: Dict[tuple, List[int]] = {}
_ROOTS_AT: Dict[tuple, float] = {}
#: 上次扫到的名字命中 {key: (at, {addr: raw}, searched_frozenset)}
_HITS_MEMO: Dict[tuple, tuple] = {}
#: 上次全量扫描时各私有区域的提交大小 {key: {base: size}} —— 增量扫描的基准
_REGIONS_MEMO: Dict[tuple, Dict[int, int]] = {}
#: 上次全量扫私有堆（自愈/名字兜底那条路）的时刻 {key: 时间} —— 节流用
_FULL_SCAN_AT: Dict[tuple, float] = {}
#: 上次「按名字锚定」全量扫描的时刻 {key: 时间} —— 节流用
_NAME_SCAN_AT: Dict[tuple, float] = {}
#: 模块列表 {key: [module dict]} —— EnumProcessModules 在 AE 上要走几百次
#: GetModuleFileNameExW，0.4 秒一拍绝不能每拍重来。同一 AE 会话的模块基址不变。
_MODULES_MEMO: Dict[tuple, list] = {}
#: adapter profile {key: profile} —— profile_of() 要读一次本机适配 JSON，
#: 每拍读盘同样不可接受。
_PROFILE_MEMO: Dict[tuple, object] = {}

#: 根流缓存的**下限**有效期（秒）。缓存命中且未过期时只扫新增的内存
#: （毫秒级）；过期才做一次全量重扫。
#: ★ 实际 TTL 不是常数，而是按「上一轮全量重扫实测耗时」自适应：
#:     ttl = clamp(_TTL_FACTOR * 上次全量耗时, _ROOTS_TTL, _ROOTS_TTL_MAX)
#:   恒定 8 秒时，若重扫要 1.4 秒则占空比 17%；恒定为 25 秒又会让
#:   「增量的边角情形」最多要等 25 秒。自适应把占空比钉在 ~12%，同时
#:   在便宜的机器上（重扫 0.2 秒）就把 TTL 压到 2 秒 —— 越快的机器越快。
_ROOTS_TTL = 2.0
_ROOTS_TTL_MAX = 20.0
_TTL_FACTOR = 8.0
#: 最近一次全量重扫的实测耗时（秒）—— 自适应 TTL 的依据，也写进诊断日志
#: 上一拍全量扫描的诊断计数（写进 live_watch.log，用来分辨「堆根本没那么大」
#: 和「名字不在我们能扫的那部分堆里」这两种完全不同的故障）
_LAST_SCAN_SECS = 0.0
_LAST_SCAN_REGIONS = 0
#: 最近一次「通用名字扫描」的命中数（runs=堆里像名字的串，deep=流）
_LAST_RUNS = 0
_LAST_DEEP_STREAMS = 0
_LAST_PROC_REGIONS = 0
_LAST_PRIV_REGIONS = 0
_LAST_PRIV_MB = 0.0
_LAST_READABLE_MB = 0.0
#: 最近一次量到的「可扫私有堆」大小（MB）—— 只用于诊断日志
_LAST_HEAP_MB = 0.0
#: 上一拍的耗时统计（秒）—— 诊断日志用
_LAST_TICK_SECS = 0.0
#: 上一拍的诊断快照 —— app/backend.py 把它写进 live_watch.log。
#: ★ 没有它就只能看到「完成:N 条」，扫不到流和「已经是中文」在日志里长得一模一样，
#:   这正是 2026-11-06 那版「效果控件标题还是英文却报无事可做」查不动的原因。
_LAST_DIAG: Dict[str, object] = {}


def last_diag() -> Dict[str, object]:
    """上一拍的诊断快照（只读）。"""
    return dict(_LAST_DIAG)


def roots_ttl() -> float:
    """根流缓存的有效期（秒）：按实测全量重扫耗时自适应，占空比钉在 ~12%。"""
    if _LAST_SCAN_SECS <= 0:
        return _ROOTS_TTL
    return max(_ROOTS_TTL, min(_ROOTS_TTL_MAX, _TTL_FACTOR * _LAST_SCAN_SECS))
#: 一拍里「增量区间」的最大读取预算（字节）。堆在小幅持续增长时增量区间
#: 可能很大，不设上限就会有一拍读几百 MB（界面卡顿）。超了本拍只读前面
#: 这些，剩下的下一拍继续 —— 差分基准照常推进，不会漏。
_DELTA_BUDGET = 16 << 20
#: 名字命中缓存的寿命要更短：新写入的名字会让旧快照失真
_HITS_TTL = 3.0

_MEMO_MAX = 32


def _trim(table: Dict, keep: int = _MEMO_MAX) -> None:
    while len(table) > keep:
        table.pop(next(iter(table)))


def _slot_text_ok(raw: bytes) -> bool:
    """32 字节名字槽的内容像不像一个「名字」（可打印 ASCII 或中文）。

    提出来是为了让 scan() 能对同一块 data 直接切片判断 —— 原来每个命中都
    单独 ReadProcessMemory 一次，上千次系统调用全花在这上面。
    """
    txt = raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
    if len(txt) < 2:
        return False
    return all(" " <= ch <= "~" or "\u4e00" <= ch <= "\u9fff" for ch in txt)


def names_regex(names) -> "re.Pattern":
    """把一批名字编成一个 UTF-16LE 正则；没名字返回 None。"""
    pats = [re.escape(n.encode("utf-16-le"))
            for n in sorted({n for n in (names or ())
                             if isinstance(n, str) and len(n) >= 2},
                            key=len, reverse=True)]
    return re.compile(b"|".join(pats)) if pats else None


#: 效果实例/注册表条目 +0x10 的 48 字节 MBCS 名字字段（IG5 实证）：
#:   FLT_FCSpec::GetEffectName_MB @0x180066b90 = `mov rax,rcx; add rax,0x10; ret`
#:   FLT_FCSpec::SetEffectName @0x18006BA20/BAE0 = `strcpy_s(this+0x10, 0x30, src)`
#:   FLT_FCSeqSpec::Read @0x1800399D0 读工程文件的 'fnma' chunk 后同时
#:   SetEffectName(this, name)（写 +0x10）与 SetDisplayName(this, name)（写 +0xF0）。
#: 注册表条目（vtable = flt+fc_vtable）和效果实例（vtable = 另一个 FLT 虚表）
#: 都用这一段布局，所以「只写实例、不写注册表」就靠虚表区分。
#: （结构常量 EFFECT_NAME_OFF / EFFECT_NAME_SIZE / EFFECT_NAME_MAX 在下面
#:   「效果实例自身的效果名字段」那一节统一定义，别在这里重复。）
#: 内存里「像名字」的 MBCS/ASCII 串（至少 4 个可打印 ASCII 字符）。
#: 用它一次扫出所有候选名字串，再在 Python 里按名字集合过滤 —— 与词典大小无关。
_RUN_MB_RX = re.compile(rb"[\x20-\x7e]{4,}")

#: 堆里「像名字」的 UTF-16LE 串（至少 4 个可打印 ASCII 字符）。
#: ★ 用它替掉「把几千个译名拼成一条正则」：词典有 2.6 万条、选区里常用上千条，
#:   拼成交替式正则在 30MB+ 堆上要跑好几秒；而一条通用正则可以一次拿到所有
#:   名字样子的串，再在 Python 里按集合过滤 —— 只有一次扫描，与词典大小无关。
_RUN_RX = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def _readable(mbi) -> bool:
    if mbi is None or mbi.State != 0x1000:
        return False
    prot = mbi.Protect or 0
    if prot & 0x100:                      # PAGE_GUARD
        return False
    return (prot & 0xFF) in (0x02, 0x04, 0x08, 0x20, 0x40)


def iter_regions(p: Process):
    """遍历进程里可读已提交区域（按 VirtualQueryEx 线性走）。"""
    start = 0x10000
    limit = 0x7FFFFFFFFFFF
    while start < limit:
        mbi = virtual_query(p, start)
        if mbi is None or (mbi.RegionSize or 0) <= 0:
            start += 0x10000
            continue
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if _readable(mbi):
            yield base, size
        nxt = base + size
        start = nxt if nxt > start else start + 0x10000


def iter_writable_regions(p: Process):
    """所有「可读且可写」的已提交区域（不限 MEM_PRIVATE）。

    ★ 这是 2026-11-07 真机排障的关键修正：原来只扫 MEM_PRIVATE 的 RW 区，
      用户机器上一共只有 34MB —— AE 2025 的工程对象大量落在 MEM_MAPPED /
      其他已提交区里，私有堆过滤把它们全丢了，于是「按名字锚定」永远找不到
      名字、面板永远英文。写入目标是显示名缓存，必须是可写内存，所以按
      「可写」筛，比按「私有」筛安全得多。
    """
    start = 0x10000
    limit = 0x7FFFFFFFFFFF
    while start < limit:
        mbi = virtual_query(p, start)
        if mbi is None or (mbi.RegionSize or 0) <= 0:
            start += 0x10000
            continue
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        prot = mbi.Protect or 0
        if (mbi.State == 0x1000 and not (prot & 0x100)
                and (prot & 0xFF) in (0x04, 0x08, 0x40)):
            yield base, size
        nxt = base + size
        start = nxt if nxt > start else start + 0x10000


#: 名字/流扫描的取证范围 = 所有「可读且可写」的已提交区域。
#: ★ 2026-11-07 真机（AE 2025 25.5，pid 14348）：只扫 MEM_PRIVATE 时全量扫描
#:   一共只有 34MB，一个槽都找不到 —— 面板标题永远是英文，而日志却写
#:   「效果标题已经是中文(本拍无改动)」。AE 的工程对象大量落在 MEM_MAPPED
#:   等已提交区里，按「可写」筛才能覆盖到它们。写入目标仍是显示名缓存
#:   （必须可写），所以放宽扫描范围不会带来写错地方的风险。
#: 测试会 patch 这个名字来指到假内存，改扫描范围时别改调用点。
def iter_scan_regions(p: Process):
    """扫描源（宽）：可读且可写、已提交、非 PAGE_GUARD 的区域。"""
    return iter_writable_regions(p)


def iter_heap_regions(p: Process):
    """扫描源（窄）：可写 MEM_PRIVATE 区域 —— AE 自己 new 出来的对象在这里。

    ★ 为什么分两档：宽扫要遍历所有可写区（含各 DLL 的可写节、MEM_MAPPED），
      在万级 MB 上每拍都跑会把 0.4 秒一拍拖垮；而效果实例是 AE 堆里的对象，
      窄扫 30~40MB 只要几十毫秒，先窄后宽就能同时要「快」和「不漏」。
    """
    start = 0x10000
    limit = 0x7FFFFFFFFFFF
    while start < limit:
        mbi = virtual_query(p, start)
        if mbi is None or (mbi.RegionSize or 0) <= 0:
            start += 0x10000
            continue
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        prot = mbi.Protect or 0
        if (mbi.State == 0x1000 and mbi.Type == MEM_PRIVATE
                and not (prot & 0x100)
                and (prot & 0xFF) in (0x04, 0x08, 0x40)):
            yield base, size
        nxt = base + size
        start = nxt if nxt > start else start + 0x10000


def private_region_map(p: Process) -> Dict[int, int]:
    """私有堆的 {基址: 提交大小}。用来做「这次比上次多提交了哪些页」的差分。

    顺手统计「整个地址空间走了多少个区域 / 其中私有可写多少 MB / 所有可读
    区域多少 MB」—— 用户在真机上只给一行日志，没有这三个数就没法判断到底是
    「堆根本没这么大」还是「名字不在私有可写堆里」。
    """
    global _LAST_PROC_REGIONS, _LAST_PRIV_REGIONS, _LAST_PRIV_MB, _LAST_READABLE_MB
    out: Dict[int, int] = {}
    nproc = npriv = 0
    priv_bytes = readable_bytes = 0
    start = 0x10000
    limit = 0x7FFFFFFFFFFF
    while start < limit:
        mbi = virtual_query(p, start)
        if mbi is None or (mbi.RegionSize or 0) <= 0:
            start += 0x10000
            continue
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        prot = mbi.Protect or 0
        nproc += 1
        if _readable(mbi):
            readable_bytes += size
        if (mbi.State == 0x1000 and (mbi.Type or 0) == MEM_PRIVATE
                and (prot & 0xFF) in (0x04, 0x08, 0x20, 0x40) and not (prot & 0x100)):
            out[base] = size
            npriv += 1
            priv_bytes += size
        nxt = base + size
        start = nxt if nxt > start else start + 0x10000
    _LAST_PROC_REGIONS = nproc
    _LAST_PRIV_REGIONS = npriv
    _LAST_PRIV_MB = priv_bytes / 1048576.0
    _LAST_READABLE_MB = readable_bytes / 1048576.0
    return out


def scan_heap(p: Process, needle: bytes = b"", rx=None, regions=None):
    """**一次**遍历私有堆，同时收集 vtable needle 命中与名字 UTF-16 命中。

    两条通道（效果名流 / 参数名槽）原来各自把整块私有堆读一遍，再加上布局
    校验那遍，同一个进程内存被读 3 次。两次扫描的区域完全相同，只有匹配方式
    不同，所以合并成一次：同一块 data 上先找 8 字节 vtable，再用正则找名字。

    regions 给定时只扫这些区间（增量扫描：只读上次之后新提交的内存）。
    返回 (roots, hits)。
    """
    global _LAST_SCAN_SECS, _LAST_HEAP_MB, _LAST_SCAN_REGIONS
    roots: List[int] = []
    hits: Dict[int, bytes] = {}
    nreg = 0
    source = regions if regions is not None else iter_scan_regions(p)
    full = regions is None
    t0 = time.monotonic()
    scanned = 0
    for base, size in source:
        nreg += 1
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            try:
                data = p.read(base + off, n)
            except Exception:
                off += n
                continue
            if needle:
                i = data.find(needle)
                while i >= 0:
                    if i % 8 == 0:
                        roots.append(base + off + i)
                    i = data.find(needle, i + 1)
            if rx is not None:
                for m in rx.finditer(data):
                    hits[base + off + m.start()] = m.group(0)
            off += n
            scanned += n
    if full:
        # 只记全量扫描的耗时/体积：增量扫描是毫秒级，拿它算 TTL 会把 TTL 压到下限。
        _LAST_SCAN_SECS = time.monotonic() - t0
        _LAST_HEAP_MB = scanned / 1048576.0
        _LAST_SCAN_REGIONS = nreg
    return roots, hits


def display_offset(profile=None) -> int:
    """取该构建的显示名缓存偏移；profile 没有就退回默认 0xE8。"""
    off = int(getattr(profile, "display_cache", 0) or 0)
    return off or DISPLAY_OFF


#: effect / parade 流的 vtable 偏移兜底值（本机 AE 2022 22.6.0.64 实测）。
ROOT_VT = 0x978F8


def root_vtable(profile=None) -> int:
    """取该构建的效果 parade 根对象虚表 RVA；没有就退回默认值。"""
    vt = int(getattr(profile, "root_vtable", 0) or 0)
    return vt or ROOT_VT


#: parade（效果组）对象的地址集合 {(pid,创建时间,镜像,root_vt): [addr]}
#: ★ 这是「新加效果 0.4 秒内一定发现」的关键：parade 的根向量是工程里现存
#:   效果流的**权威清单**，读它只要几次跨进程小读（毫秒级）；而「猜新增的
#:   私有页」在堆复用已提交内存时可能什么都看不到。
_PARADES_MEMO: Dict[tuple, List[int]] = {}
#: 上次用 parade 向量枚举根流的时刻 —— 定期做一次私有堆差分，好让新出现的
#: 图层/合成（=新的 parade 对象）也能被发现
_PARADES_AT: Dict[tuple, float] = {}
#: parade 根向量的最大字节数（防御性上限：解析到垃圾时不要读一大片）
PARADE_VECTOR_MAX = 8192 * 8
#: 全量找 parade 失败的退避计数 / 下次最早重试时刻
_PARADE_FAILS: Dict[tuple, int] = {}
_PARADE_FAIL_AT: Dict[tuple, float] = {}
#: 多久做一次「私有堆差分」（发现新建的 parade = 新图层/新合成），秒。
#: parade 向量负责发现「老图层上新加的效果」（零扫描），差分负责发现
#: 「新图层/新合成」（要扫一次新提交的页）。
_PARADE_RESCAN = 0.2


def _mem_key(p) -> tuple:
    """会话级缓存的 key：同一 (pid, 创建时间, 镜像) 就是同一个 AE 进程。"""
    return (getattr(p, "pid", 0), getattr(p, "creation_time", 0),
            getattr(p, "image", ""))


def _modules_of(p) -> list:
    """取进程模块列表（带会话缓存）。

    `EnumProcessModules` + 每个模块一次 `GetModuleFileNameExW`，AE 里三四百个
    模块就是几百次系统调用；0.4 秒一拍的常驻换名每拍都建 LiveParams，
    绝不接受每拍重来一次。同一 AE 会话里模块基址不会变。
    """
    key = _mem_key(p)
    got = _MODULES_MEMO.get(key)
    if got is not None:
        return got
    mods = p.modules()
    if mods:
        _MODULES_MEMO[key] = mods
        _trim(_MODULES_MEMO)
    return mods


def profile_of(p: Process, data_root=None) -> object:
    """按进程实际加载的 FLT.dll 取 adapter profile（取不到返回 None）。

    偏移必须跟着进程里真正跑的那个构建走；并且必须带 FLT.dll 的
    SHA-256 去选候选 —— 同一版本号下可能有多个构建，哈希不符时要拒绝。
    """
    key = _mem_key(p) + (data_root,)
    if key in _PROFILE_MEMO:
        return _PROFILE_MEMO[key]
    try:
        from . import adapter
        from .proc import file_version, module_digest

        try:
            version = file_version(getattr(p, "image", "") or "")
        except Exception:
            version = ""
        if not version:
            return None
        try:
            modules = _modules_of(p)
            module = next((m for m in modules
                           if m["name"].lower() == "flt.dll"), None)
            if module:
                digest = module_digest(p, module["path"])
            else:
                return None          # FLT 还没加载 → 不能猜偏移
        except Exception:
            return None
        prof = adapter.get_profile(version, digest, data_root)
        # 取不到（None）不缓存：AE 可能还在加载 FLT，下一拍再试。
        if prof is not None:
            _PROFILE_MEMO[key] = prof
            _trim(_PROFILE_MEMO)
        return prof
    except Exception:
        return None


#: AE 给「同类第二个及以后实例」自动加的编号后缀（"S_WarpPolar 2"）。
#: 实测（AE 2022/2025/2026 一致）：第 2、3、4… 个同类效果的显示名是
#: 「原名 + U+0020 + 十进制序号」，词典里当然没有这个键。
_INSTANCE_SUFFIX = re.compile(r"^(.*?)\s+(\d{1,3})$")


def split_instance_suffix(name: str, known: Optional[set] = None) -> tuple:
    """把 "CC Light Rays 2" 拆成 ("CC Light Rays", " 2")；无后缀返回 (name, "")。

    ★ 只拆「空白 + 纯数字」。真实产品名里也有数字（"BCC Noise Map 2"、
      "Primatte Keyer 6"、"Depth Scanner 2"），所以：
        1. 调用方必须先精确查表，查不到才用本函数兜底；
        2. 给了 known（已知英文原名集合）时，还必须 base in known 才认。
    """
    m = _INSTANCE_SUFFIX.match(name or "")
    if not m:
        return name, ""
    base = m.group(1)
    if known is not None and base not in known:
        return name, ""
    return base, " " + m.group(2)


class Slot(object):
    """一个可改写的显示名槽位。"""

    __slots__ = ("stream", "kind", "pf_name", "english", "inline", "capacity")

    def __init__(self, stream, kind, pf_name, english, inline, capacity):
        self.stream = stream
        self.kind = kind
        self.pf_name = pf_name
        self.english = english
        self.inline = inline
        self.capacity = capacity

    def as_dict(self):
        return {
            "stream": self.stream,
            "kind": self.kind,
            "pf_name": self.pf_name,
            "english": self.english,
        }


def _decode(buf: bytes) -> str:
    text = buf.decode("utf-16-le", "replace")
    cut = text.find("\x00")
    return text if cut < 0 else text[:cut]


#: 认作「已经是中文」的字符（CJK 统一表意文字 + 兼容表意 + 中文标点）
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff08\uff09\u3001\u3002]")


def _looks_cjk(text: str) -> bool:
    return bool(text) and bool(_CJK_RE.search(text))


class LiveParams(object):
    """对运行中 AE 的效果实例显示名做实时读写（只碰 TDB 流 + 显示名缓存）。"""

    def __init__(self, p: Process, translations: Optional[Dict[str, str]] = None,
                 profile=None, data_root=None, samples=None):
        self.p = p
        self.translations = dict(translations or {})
        self.samples = [s for s in (samples or []) if isinstance(s, str) and s]
        #: 已知英文原名（词典的键）—— 重复实例后缀只在 base 属于它时才拆
        self.known = {k.strip() for k in self.translations if k.strip()}
        prof = profile if profile is not None else profile_of(p, data_root)
        self.profile = prof
        self.data_root = data_root
        self.display_off = display_offset(prof)
        self.root_vt = root_vtable(prof) if prof is not None else ROOT_VT
        self._layout_done = False
        self._layout_tries = 0
        self.layout_note = ""
        #: 本拍诊断（写进 live_watch.log 的那一行）—— 见 last_diag()
        self.diag: Dict[str, object] = {
            "off": self.display_off,
            "vt": self.root_vt,
            "roots_src": "",
            "roots": 0,
            "slots": 0,
        }
        self._key = (p.pid, getattr(p, "creation_time", 0),
                     getattr(p, "image", ""))
        memo = _LAYOUT_MEMO.get(self._key)
        if memo:
            self.display_off, self.root_vt, self.layout_note = memo
            self._layout_done = True
            self._layout_tries = _LAYOUT_FAILS.get(self._key, 0)
        self.modules = {m["name"].lower(): m for m in _modules_of(p)}
        self.code = []
        for name in ("flt.dll", "tdb.dll"):
            mod = self.modules.get(name)
            if not mod:
                raise RuntimeError("AE 还没加载 %s，请等启动完成" % name.upper())
            try:
                size = os.path.getsize(mod["path"])
            except OSError:
                size = 0x200000
            self.code.append((mod["base"], mod["base"] + size))

    @property
    def flt_base(self) -> int:
        mod = (getattr(self, "modules", {}) or {}).get("flt.dll")
        return int(mod["base"]) if mod else 0

    # ---- 基础读写 -------------------------------------------------
    def _in_code(self, value: int) -> bool:
        return any(lo <= value < hi for lo, hi in self.code)

    def read_display(self, stream: int, off: Optional[int] = None) -> str:
        off = self.display_off if off is None else off
        head = self.p.read(stream + off, 0x20)
        buf, _res, size, cap = struct.unpack("<QQQQ", head)
        if size <= 0 or size > MAX_NAME_BYTES:
            return ""
        if cap < 8:
            return _decode(head[: size * 2])
        return _decode(self.p.read(buf, size * 2))

    def write_display(self, slot: Slot, text: str) -> bool:
        """写 UTF-16 显示名并同步 _Mysize。容量不够/校验不过返回 False。"""
        data = text.encode("utf-16-le")
        if not data or len(text) > slot.capacity:
            return False
        head = self.p.read(slot.stream + self.display_off, 0x20)
        buf, _res, _size, cap = struct.unpack("<QQQQ", head)
        inline = cap < 8
        if inline != slot.inline:
            return False
        target = slot.stream + self.display_off if inline else buf
        payload = data + b"\x00\x00"
        if not self._write(target, payload):
            return False
        return self._write(slot.stream + self.display_off + 0x10,
                           struct.pack("<Q", len(text)))

    def _write(self, address: int, data: bytes) -> bool:
        if not data:
            return True
        n = C.c_size_t()
        buf = C.create_string_buffer(data, len(data))
        ok = self.p.k.WriteProcessMemory(
            self.p.handle, C.c_void_p(address), buf, len(data), C.byref(n)
        )
        if not ok or n.value != len(data):
            return False
        try:
            return self.p.read(address, len(data)) == data
        except Exception:
            return False

    # ---- 布局自校准 -----------------------------------------------
    #
    # 本机适配产出的 profile 里 display_cache / root_vtable 可能是 0
    # （core/aescan.py 从来没探测过这两个字段）。于是 display_offset()
    # 回退到 2022 的 0xE8、root_vtable() 回退到 0x978F8；在 25/26 上这两个
    # 值都不对，scan_streams() 一个流都扫不到，整条通道静默空转。
    # 对策：不再相信 profile 是否填对，改成从真实内存反推这两个值。

    #: 候选显示名偏移：实测只有 0xE8 / 0xF0 两种，按 8 字节步进给足余量。
    _OFF_CANDIDATES = tuple(range(0xC0, 0x141, 8))
    #: 校准最多试几次（失败不锁死，但也不能每拍都全内存扫一遍）
    _LAYOUT_TRIES = 6
    #: 两次失败重试之间的最小间隔（秒）
    _LAYOUT_COOLDOWN = 20.0
    #: 退避上限（秒）
    _LAYOUT_MAX_COOLDOWN = 300.0

    def _sample_names(self, names, allow_cjk: bool = False) -> List[str]:
        """挑一批肯定在内存里出现过的名字当校准样本。"""
        pool: List[str] = []

        def ok(text: str) -> bool:
            if not isinstance(text, str) or len(text) < 4:
                return False
            return True if allow_cjk else not _looks_cjk(text)

        for n in (names or []):
            if ok(n):
                pool.append(n)
        if len(pool) < 3:
            for n in self.samples:
                if ok(n):
                    pool.append(n)
        if len(pool) < 3 and not allow_cjk:
            for k in self.translations:
                if isinstance(k, str) and len(k) >= 4 and not _looks_cjk(k):
                    pool.append(k)
                if len(pool) > 400:
                    break
        seen = set()
        out: List[str] = []
        for n in sorted(pool, key=len, reverse=True):
            if n in seen:
                continue
            seen.add(n)
            out.append(n)
            if len(out) >= 8:          # 8 个样本足够投票，且别让扫描太贵
                break
        return out

    def _find_utf16_hits(self, names: Iterable[str], regions=None) -> List[tuple]:
        """在私有堆里一次性找出这批名字的 UTF-16 副本 [(地址, 名字)]。"""
        rx = names_regex(names)
        if rx is None:
            return []
        out: List[tuple] = []
        source = regions if regions is not None else iter_scan_regions(self.p)
        for base, size in source:
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                try:
                    data = self.p.read(base + off, n)
                except Exception:
                    off += n
                    continue
                for m in rx.finditer(data):
                    out.append((base + off + m.start(),
                                m.group(0).decode("utf-16-le", "replace")))
                off += n
        return out

    def _find_pointers_to(self, targets) -> List[tuple]:
        """找出所有「值属于 targets」的 8 字节槽 [(该槽地址, 目标地址)]。

        ★ 长名字（>=8 字符）不在 wstring 头内联，而是存在独立的堆缓冲区里，
          头里只放一个指向它的指针。所以 hit - off 这种「按偏移反推」对长名字
          完全不成立 —— 必须反过来找「谁指向这个名字」。
        """
        if not targets:
            return []
        # 别再逐槽 struct.unpack_from（1.5GB 私有堆就是上亿次调用）；
        # array.frombytes 一次成型，实测快 2 倍多。
        wanted = set(int(t) for t in targets)
        out: List[tuple] = []
        for base, size in iter_scan_regions(self.p):
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                try:
                    data = self.p.read(base + off, n)
                except Exception:
                    off += n
                    continue
                usable = len(data) - (len(data) % 8)
                if usable:
                    arr = array.array("Q")
                    arr.frombytes(data[:usable])
                    if sys.byteorder != "little":
                        arr.byteswap()
                    for i, v in enumerate(arr):
                        if v in wanted:
                            out.append((base + off + i * 8, v))
                off += n
        return out

    def _calibrate_layout(self, names=None, allow_cjk: bool = False) -> bool:
        """从真实内存反推 display_off / root_vt；成功返回 True。

        判据（全部是结构性的，不是猜）：
          1. 头 32 字节解成 (buf, res, size, cap) 必须自洽；
          2. cap >= 8 时长名字在 buf 里、cap < 8 时内联在头后面；
          3. 回读出来的字符串必须等于我们找到的那个样本；
          4. 流对象的 vtable 必须落在 FLT/TDB 代码段。
        """
        samples = self._sample_names(names, allow_cjk=allow_cjk)
        if not samples:
            self.layout_note = "no-sample"
            return False
        hits = self._find_utf16_hits(samples)
        if not hits:
            self.layout_note = "no-hit"
            return False
        by_addr = {a: t for a, t in hits}
        flt = (self.modules.get("flt.dll") or {}).get("base") or 0
        votes: Dict[int, int] = {}
        vt_votes: Dict[int, int] = {}
        seen_slots = set()

        def vote(stream: int, off: int) -> None:
            votes[off] = votes.get(off, 0) + 1
            if not flt:
                return
            try:
                vt = self.p.u64(stream)
            except Exception:
                vt = 0
            if vt > flt:
                vt_votes[vt - flt] = vt_votes.get(vt - flt, 0) + 1

        def consider_inline(addr: int, off: int, expect: str) -> None:
            slot = self._slot_at(addr, off)
            if slot is None or (slot.stream, off) in seen_slots:
                return
            try:
                if self.read_display(slot.stream, off) != expect:
                    return
            except Exception:
                return
            seen_slots.add((slot.stream, off))
            vote(slot.stream, off)

        def consider_long(head_addr: int, off: int, target: int, expect: str) -> None:
            stream = head_addr - off
            if stream <= 0x10000 or (stream, off) in seen_slots:
                return
            try:
                head = self.p.read(head_addr, 0x20)
            except Exception:
                return
            if len(head) < 0x20:
                return
            buf, _res, size, cap = struct.unpack("<QQQQ", head)
            if cap < 8 or buf != target:
                return
            if not 0 < size <= MAX_NAME_BYTES:
                return
            try:
                vt = self.p.u64(stream)
                if vt and not self._in_code(vt):
                    return
                if self.read_display(stream, off) != expect:
                    return
            except Exception:
                return
            seen_slots.add((stream, off))
            vote(stream, off)

        # 第一遍：只认内联名字（短名占多数，够投出偏移就收工，省一次全内存扫描）
        for addr, text in hits:
            for off in self._OFF_CANDIDATES:
                consider_inline(addr, off, text)
        if len(votes) < 2 or max(votes.values()) < 3:
            # 第二遍：长名字（>=8 字符）在独立缓冲区里，靠「谁指向它」反查头
            ptrs = self._find_pointers_to(set(by_addr))
            for head_addr, target in ptrs:
                expect = by_addr.get(target)
                if not expect:
                    continue
                for off in self._OFF_CANDIDATES:
                    consider_long(head_addr, off, target, expect)
        if not votes:
            self.layout_note = "no-slot"
            return False
        prof_off = int(getattr(self.profile, "display_cache", 0) or 0)
        prof_vt = int(getattr(self.profile, "root_vtable", 0) or 0)
        # 先判定 profile 里的旧值是否有效（必须显式传旧值：此刻
        # self.display_off 可能已经被校准结果覆盖了）。
        stale = bool(prof_off and not self._profile_layout_works(prof_off, prof_vt))
        if prof_off and prof_off in votes:
            self.display_off = prof_off        # profile 对得上就尊重它
        else:
            self.display_off = max(votes, key=lambda k: (votes[k], k))
        if vt_votes:
            self.root_vt = max(vt_votes, key=lambda k: vt_votes[k])
        self.layout_note = "calibrated(off=0x%X,vt=0x%X,%d票)" % (
            self.display_off, self.root_vt, votes.get(self.display_off, 0))
        self._layout_done = True
        # profile 里已有非 0 值、但按它扫不到流 → 那是错的旧值，必须覆盖。
        self._remember_layout(force=stale)
        self._memo_layout()
        if stale:
            self.layout_note += "(已覆盖失效的profile值)"
        return True

    def _remember_layout(self, force: bool = False) -> None:
        """把校准结果写回本机适配条目（默认只补 0 字段，不改已验证的地址）。

        ★ 注意：这里写的是本机适配档案（adapter_profiles.json），不是插件文件、
          不是 .aex、也不是 AE 安装目录 —— 它只是「这个构建的偏移是多少」的
          缓存，和效果名译文落盘是两件事。
        """
        prof = self.profile
        data_root = self.data_root
        if prof is None or not data_root:
            return
        cur_off = int(getattr(prof, "display_cache", 0) or 0)
        cur_vt = int(getattr(prof, "root_vtable", 0) or 0)
        if cur_off and cur_vt and not force:
            return                             # 已经是对的，不必写
        try:
            from dataclasses import replace

            from . import adapter

            local = adapter.load_local(data_root)
            cur = local.get(prof.version)
            if cur is None or cur.flt_sha != prof.flt_sha:
                return                          # 不是本机验证条目，不动
            if cur.parameter_draw_sha and cur.root_vtable:
                # 参数目录的 RTTI 类型已由本机适配保存。显示名样本
                # 校准只更新自己的缓存偏移，不能覆盖参数目录的类型。
                force_root = cur.root_vtable
            else:
                force_root = self.root_vt
            if force:
                upd = replace(cur, display_cache=self.display_off,
                              root_vtable=force_root)
            else:
                upd = replace(cur,
                              display_cache=cur.display_cache or self.display_off,
                              root_vtable=cur.root_vtable or force_root)
            if upd == cur:
                return
            adapter.save_local(data_root, {prof.version: upd})
            self.profile = upd
        except Exception:
            pass                                # 写不进去也不影响本次写入

    def _profile_layout_works(self, off: Optional[int] = None,
                              vt: Optional[int] = None, names=None) -> bool:
        """profile 里那两个值到底还能不能扫到流？

        以前只要 profile 里两个字段非 0 就直接信。但本机适配写进去的值可能是
        上一次校准投错的结果，一旦写错，以后每个会话都「已适配」再也不校准，
        用户就永久卡在错偏移上。所以现在要先证明：用这两个值真的能锚到一个
        回读得出名字的流。
        """
        off = self.display_off if off is None else off
        vt = self.root_vt if vt is None else vt
        key = (self.p.pid, getattr(self.p, "creation_time", 0),
               getattr(self.p, "image", ""), off, vt)
        if key in _LAYOUT_OK:
            return _LAYOUT_OK[key]
        ok = False
        mod = self.modules.get("flt.dll")
        try:
            if mod:
                needle = struct.pack("<Q", mod["base"] + vt)
                roots, hits = scan_heap(self.p, needle, names_regex(names))
                for addr in roots[:32]:
                    slot = self._slot_from_stream(addr, off)
                    if slot is not None and slot.english:
                        ok = True
                        break
                # 顺手把这次验证扫到的根流/名字命中记下来：验证通过就意味着
                # 「用这个 vt 找到的流确实是效果流」，不记的话紧接着
                # apply() 又要为同一件事把整块私有堆读一遍。
                if ok and roots:
                    self._remember_roots(roots)
                if ok and hits:
                    self._remember_hits(hits, names)
        except Exception:
            ok = False
        if len(_LAYOUT_OK) > _MEMO_MAX:
            _trim(_LAYOUT_OK)
        _LAYOUT_OK[key] = ok
        return ok

    def _retry_wait(self, tries: int) -> float:
        """第 tries 次失败后要等多久（指数退避，封顶 _LAYOUT_MAX_COOLDOWN）。"""
        return min(self._LAYOUT_COOLDOWN * (2 ** min(max(tries - 1, 0), 8)),
                   self._LAYOUT_MAX_COOLDOWN)

    def _ensure_layout(self, names=None, allow_cjk: bool = False) -> None:
        """首次用到时校准一次（profile 已经把两个值填对就直接信 profile）。

        ★ 校准失败必须允许重试：第一次调用时若样本还没进内存（工程刚打开、
          效果刚加上、扫描撞上正在改的堆），永久锁死就等于这个 AE 会话里整条
          通道一直是错的。现在按指数退避重试，永不永久放弃。
        """
        if self._layout_done:
            return
        if self._layout_tries:
            last = _LAYOUT_RETRY_AT.get(self._key)
            wait = self._retry_wait(self._layout_tries)
            if last is not None and time.monotonic() - last < wait:
                return                      # 冷却中：沿用当前值，下一拍再看
        prof_off = int(getattr(self.profile, "display_cache", 0) or 0)
        prof_vt = int(getattr(self.profile, "root_vtable", 0) or 0)
        if prof_off and prof_vt and self._profile_layout_works(names=names):
            self.layout_note = "profile"
            self._layout_done = True
            self._memo_layout()
            return
        try:
            if self._calibrate_layout(names, allow_cjk=allow_cjk):
                return                          # 成功：内部已置 _layout_done
            note = self.layout_note
        except Exception as exc:               # 校准失败绝不影响正常路径
            note = "error:%s" % exc
        self._layout_tries += 1
        _LAYOUT_FAILS[self._key] = self._layout_tries
        _LAYOUT_RETRY_AT[self._key] = time.monotonic()
        _trim(_LAYOUT_FAILS)
        _trim(_LAYOUT_RETRY_AT)
        wait = self._retry_wait(self._layout_tries)
        self.layout_note = "%s(第%d次失败,%.0fs后重试)" % (
            note, self._layout_tries, wait)
        if self._layout_tries >= self._LAYOUT_TRIES:
            # 校不出来就用 profile 里的值兜底（宁可不动，也不能拿错偏移去写内存），
            # 但不标记完成、不写成功 memo —— 下次仍会再试。
            self.display_off = display_offset(self.profile)
            self.root_vt = root_vtable(self.profile)
        self._memo_layout(ok=False)

    def _memo_layout(self, ok: bool = True) -> None:
        """把本次布局结论记到进程级，供同一 AE 会话里的后续实例直接复用。

        ★ 只记成功结论。以前连「试满 N 次退回 profile 默认值」也写进 memo，
          于是那个会话里所有后续实例一进来就 _layout_done = True、再也不重试。
        """
        try:
            key = self._key
        except AttributeError:
            return                              # 单测里的裸实例没有 _key
        try:
            if not ok:
                _LAYOUT_FAILS[key] = self._layout_tries
                return
            _LAYOUT_MEMO[key] = (self.display_off, self.root_vt,
                                 self.layout_note)
            _LAYOUT_FAILS.pop(key, None)
            _trim(_LAYOUT_MEMO)
            _trim(_LAYOUT_FAILS)
        except Exception:
            pass

    # ---- 查找 -----------------------------------------------------
    def _slot_at(self, hit: int, off: Optional[int] = None) -> Optional[Slot]:
        """命中处若是一个流的显示名（缓冲指针指回自己）就返回槽位。"""
        off = self.display_off if off is None else off
        stream = hit - off
        if stream <= 0x10000:
            return None
        try:
            head = self.p.read(stream + off, 0x20)
        except Exception:
            return None
        buf, _res, size, cap = struct.unpack("<QQQQ", head)
        inline = cap < 8
        if inline:
            if hit != stream + off:
                return None
            capacity = INLINE_CAP
        else:
            if buf != hit:
                return None
            capacity = min(cap - 1, MAX_NAME_BYTES)
        if not 0 < size <= MAX_NAME_BYTES:
            return None
        try:
            vtable = self.p.u64(stream)
        except Exception:
            return None
        if not self._in_code(vtable):
            return None
        try:
            pf = self.p.read(stream + PF_NAME_OFF, 32)
        except Exception:
            pf = b""
        pf_name = pf.split(b"\x00", 1)[0].decode("latin-1")
        return Slot(stream, "effect" if not pf_name else "param", pf_name, "", inline, capacity)

    def find(self, names: Iterable[str]) -> List[Slot]:
        """按显示名找流。内联短名直接认，长名反查「谁指向它」。

        ★ 长名字必须走指针反查：真实效果名几乎都超过 7 字符（S_WarpPolar、
          BCC Unsharp Mask、CC Ball Action），字符存在独立堆缓冲区里，
          hit - off 得到的根本不是 stream。
        """
        names = [n for n in (names or ()) if isinstance(n, str) and n]
        self._ensure_layout(names, allow_cjk=any(_looks_cjk(n) for n in names))
        rx = names_regex(names)
        if rx is None:
            return []
        found: List[Slot] = []
        seen = set()
        missed: Dict[int, str] = {}
        for base, size in iter_scan_regions(self.p):
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                try:
                    data = self.p.read(base + off, n)
                except Exception:
                    off += n
                    continue
                for m in rx.finditer(data):
                    hit = base + off + m.start()
                    if hit in seen:
                        continue
                    seen.add(hit)
                    text = m.group(0).decode("utf-16-le", "replace")
                    slot = self._slot_at(hit)
                    if slot is not None:
                        slot.english = text
                        found.append(slot)
                    elif len(text) > INLINE_CAP:
                        # 长名字：留到第一遍扫完再统一反查，避免每个命中都全内存扫一遍
                        missed[hit] = text
                off += n
        if missed:
            got = {slot.stream for slot in found}
            heads: Dict[int, List[int]] = {}
            for head_addr, target in self._find_pointers_to(set(missed)):
                heads.setdefault(target, []).append(head_addr)
            for target, addrs in heads.items():
                text = missed.get(target)
                if not text:
                    continue
                for head_addr in addrs:
                    stream = head_addr - self.display_off
                    if stream in got:
                        break
                    slot = self._slot_from_stream(stream)
                    if slot is None or slot.inline:
                        continue
                    if slot.english != text:
                        continue
                    got.add(stream)
                    found.append(slot)
                    break
        return found

    # ---- 结构遍历(快路径: 不扫全内存) -----------------------------
    def _groups(self, stream: int) -> List[int]:
        """stream 前 0x300 字节里的候选「流组」指针(堆对象, 不是代码地址)。"""
        try:
            data = self.p.read(stream, 0x300)
        except Exception:
            return []
        out: List[int] = []
        for off in range(0, len(data) - 8, 8):
            v = struct.unpack_from("<Q", data, off)[0]
            if v > 0x10000 and v not in out:
                out.append(v)
        return out[:64]

    def _param_table(self, group: int):
        """group 里找「参数表向量」; 返回 (元素地址列表, 向量偏移) 或 ([], 0)。

        ★ 判据(2026-10-30 实测): 真参数表 vtable 命中率 ≈1.0、有 ASCII 名比例
          ≈0.9; 假阳性只有 0.12~0.16。要求 vt>=0.9 且 named>=0.3。
        """
        try:
            data = self.p.read(group, 0x300)
        except Exception:
            return [], 0
        best_ents: List[int] = []
        best_off = 0
        for goff in range(0, len(data) - 16, 8):
            begin, end = struct.unpack_from("<QQ", data, goff)
            if not begin or not end or end <= begin:
                continue
            span = end - begin
            if span % 8 or not (8 <= span <= 4096 * 8):
                continue
            cnt = span // 8
            try:
                raw = self.p.read(begin, cnt * 8)
            except Exception:
                continue
            if len(raw) < 8:
                continue
            vt_ok = named = 0
            ents: List[int] = []
            for i in range(len(raw) // 8):
                e = struct.unpack_from("<Q", raw, i * 8)[0]
                if not e:
                    continue
                ents.append(e)
                try:
                    vt = self.p.u64(e)
                except Exception:
                    continue
                if not self._in_code(vt):
                    continue
                vt_ok += 1
                try:
                    pf = self.p.read(e + PF_NAME_OFF, 16)
                except Exception:
                    continue
                if pf and 0x20 <= pf[0] < 0x7f:
                    named += 1
            if not ents:
                continue
            if vt_ok / len(ents) >= 0.9 and named / len(ents) >= 0.3:
                if len(ents) > len(best_ents):
                    best_ents, best_off = ents, goff
        return best_ents, best_off

    def _slot_from_stream(self, stream: int, off: Optional[int] = None):
        """已知 stream 地址 → Slot(或 None)。"""
        off = self.display_off if off is None else off
        try:
            head = self.p.read(stream + off, 0x20)
        except Exception:
            return None
        if len(head) < 0x20:
            return None
        _buf, _res, size, cap = struct.unpack("<QQQQ", head)
        if size > MAX_NAME_BYTES:
            return None
        inline = cap < 8
        try:
            vtable = self.p.u64(stream)
        except Exception:
            return None
        if not self._in_code(vtable):
            return None
        try:
            english = self.read_display(stream, off)
        except Exception:
            english = ""
        try:
            pf = self.p.read(stream + PF_NAME_OFF, 32)
        except Exception:
            pf = b""
        pf_name = pf.split(b"\x00", 1)[0].decode("latin-1")
        capacity = INLINE_CAP if inline else min(max(int(cap) - 1, 0), MAX_NAME_BYTES)
        return Slot(stream, "effect" if not pf_name else "param", pf_name, english,
                    inline, capacity)

    # ---- parade 向量：新效果的权威发现路径 --------------------------
    def _parade_key(self):
        return self._roots_key()

    def _parade_vector(self, parade: int) -> List[int]:
        """读 parade 对象自己的「效果流向量」→ 各效果流的地址。

        ★ 布局来自 adapter profile 的 group_vector（25.5.0.4 = 0x220），
          与 paramcatalog.py 的 roots() 是同一个结构与同一段偏移。
          parade+0x8 是 16 字节内部标识；读不出/不匹配就返回空。
        """
        gv = int(getattr(self.profile, "group_vector", 0) or 0)
        if not gv:
            return []
        try:
            begin, end = struct.unpack("<QQ", self.p.read(parade + gv, 16))
        except Exception:
            return []
        if not begin or end <= begin or (end - begin) % 8:
            return []
        span = end - begin
        if span > PARADE_VECTOR_MAX:
            return []
        try:
            raw = self.p.read(begin, span)
        except Exception:
            return []
        vt = self.flt_base + self.root_vt
        out = []
        for i in range(len(raw) // 8):
            root = struct.unpack_from("<Q", raw, i * 8)[0]
            if not root:
                continue
            try:
                # 主判据：+0 必须是本构建的效果流 vtable。
                if self.p.u64(root) != vt:
                    continue
                # 副判据（满足其一即可）：
                #   a) +0x20 反指回 parade —— 旧构建（22.6）实测如此；
                #   b) 这个流自己的显示名缓存结构自洽 —— 25.5 实测 +0x20 是
                #      TDB_StreamBase 的 parent，未必等于 parade（IG5:
                #      TDB_StreamBase::GetParent = mov rax,[rcx+0x20]），
                #      只认 (a) 会让 25.x 上一个 parade 都校验不过。
                if self.p.u64(root + 32) != parade:
                    if self._slot_from_stream(root) is None:
                        continue
            except Exception:
                continue
            out.append(root)
        return out

    def known_parades(self) -> List[int]:
        """已知 parade 对象（会话内只增不减）。"""
        return list(_PARADES_MEMO.get(self._parade_key()) or ())

    def remember_parades(self, parades) -> None:
        try:
            key = self._parade_key()
            memo = _PARADES_MEMO.setdefault(key, [])
            have = set(memo)
            for a in parades or ():
                if a and a not in have:
                    memo.append(a)
                    have.add(a)
            _trim(_PARADES_MEMO)
        except Exception:
            pass

    def roots_from_parades(self) -> List[int]:
        """用已知 parade 的根向量枚举效果流（毫秒级，零全堆扫描）。"""
        out: List[int] = []
        seen = set()
        for parade in self.known_parades():
            for root in self._parade_vector(parade):
                if root in seen:
                    continue
                seen.add(root)
                out.append(root)
        return out

    def _roots_from_known_parades(self) -> List[int]:
        """从已知根流反查 parade → 再用 parade 向量取全量根流。

        ★ 首拍省钱：布局校验（_profile_layout_works）本来就要全堆扫一次并把
          命中的根流记进 _ROOTS_MEMO，那里面的每个流 +0x20 都指回它所属的
          parade —— 拿它去校验 parade 向量，就不必再全堆扫第二遍。
        """
        known = self.cached_roots()
        if not known:
            return []
        cand = set()
        for root in known[:2048]:
            try:
                parade = self.p.u64(root + 32)
            except Exception:
                continue
            if parade:
                cand.add(parade)
        good = [p for p in cand if self._parade_vector(p)]
        if not good:
            return []
        self.remember_parades(good)
        return self.roots_from_parades()

    #: 全量扫私有堆的最小间隔（秒）。命不中时不要每拍烧几百毫秒。
    _FULL_SCAN_MIN = 1.5
    #: 按名字锚定（也要全量扫一遍堆）的最小间隔（秒）。比自愈那条路宽一点：
    #: 它是 root_vt 失效时唯一能改到面板标题的路径，不能太吝啬。
    _NAME_SCAN_MIN = 0.8

    def _may_name_scan(self) -> bool:
        """这一拍允许做一次「按名字锚定」的全量扫描吗（按会话节流）。"""
        key = self._roots_key()
        now = time.monotonic()
        last = _NAME_SCAN_AT.get(key)
        if last is not None and now - last < self._NAME_SCAN_MIN:
            return False
        _NAME_SCAN_AT[key] = now
        _trim(_NAME_SCAN_AT)
        return True

    def _may_full_scan(self) -> bool:
        """这一拍允许做一次全量私有堆扫描吗（按会话节流）。"""
        key = self._roots_key()
        now = time.monotonic()
        last = _FULL_SCAN_AT.get(key)
        if last is not None and now - last < self._FULL_SCAN_MIN:
            return False
        _FULL_SCAN_AT[key] = now
        _trim(_FULL_SCAN_AT)
        return True

    def _may_discover(self) -> bool:
        """全量找 parade 的节流闸：失败后指数退避，绝不每拍烧 0.8 秒。"""
        key = self._parade_key()
        if _PARADES_MEMO.get(key):
            return False                # 已经有 parade 了，不需要再全量找
        tries = _PARADE_FAILS.get(key, 0)
        if tries:
            if time.monotonic() - (_PARADE_FAIL_AT.get(key) or 0.0) < \
                    min(60.0, 1.0 * (2 ** tries)):
                return False
        return True

    def discover_parades(self, budget: int = 64 << 20) -> List[int]:
        """重新做一次 parade 发现（**分片**：每次最多扫 budget 字节）。

        ★ 2026-11-07 晚真机：一次性全量扫要 80~110 秒（3200MB 可写区，很多是
          页文件后盾，读速 0.5MB/s），用户开通道时界面整段卡死。改成分片：
          调用方每拍给 64MB 预算，游标记进度，十几拍拼完；单拍耗时封顶约
          0.5 秒，不影响交互。
        """
        out: List[int] = []
        try:
            needle = struct.pack("<Q", self.flt_base + self.root_vt)
            if not self.flt_base:
                return out
            key = self._parade_key()
            # ★ 2026-11-07 晚：parade 对象在**私有堆**里（约 105MB），
            #   而 iter_scan_regions 的自然顺序要先走完一大片映射区才轮到它
            #   → 分片后十几拍都拿不到根，用户体感「直接不汉化」。
            #   所以把私有堆区间排在前面：两拍内就能拿到 parade。
            regs = [(b, sz) for b, sz in iter_heap_regions(self.p)
                    if sz <= _EFFECT_REGION_MAX]
            _seen = set(regs)
            for _b, _sz in iter_scan_regions(self.p):
                if _sz > _EFFECT_REGION_MAX:
                    continue
                _item = (_b, _sz)
                if _item in _seen:
                    continue
                _seen.add(_item)
                regs.append(_item)
            if not regs:
                return out
            idx, off0 = _DISCOVER_CURSOR.get(key, (0, 0))
            if idx >= len(regs):
                idx, off0 = 0, 0
            found = set()
            used = 0
            done = False
            for ri in range(idx, len(regs)):
                base, size = regs[ri]
                off = off0 if ri == idx else 0
                while off < size:
                    n = min(CHUNK, size - off)
                    try:
                        data = self.p.read(base + off, n)
                    except Exception:
                        off += n
                        continue
                    i = data.find(needle)
                    while i >= 0:
                        if i % 8 == 0:
                            # 候选：这处在效果流的 +0；流+0x20 指回它的 parade
                            try:
                                parade = self.p.u64(base + off + i + 32)
                            except Exception:
                                parade = 0
                            if parade:
                                found.add(parade)
                        i = data.find(needle, i + 1)
                    off += n
                    used += n
                    if used >= budget:
                        _DISCOVER_CURSOR[key] = (ri, off)
                        _trim(_DISCOVER_CURSOR)
                        break
                else:
                    continue
                break
            else:
                done = True
                _DISCOVER_CURSOR.pop(key, None)
            # 校验：parade 必须能读出一组「vtable + 反指都正确」的根流
            for parade in found:
                if self._parade_vector(parade):
                    out.append(parade)
            if out:
                self.remember_parades(out)
                _PARADE_FAILS.pop(key, None)
            elif done:
                _PARADE_FAILS[key] = _PARADE_FAILS.get(key, 0) + 1
                _PARADE_FAIL_AT[key] = time.monotonic()
                self._delta_regions()
                _trim(_PARADE_FAILS)
                _trim(_PARADE_FAIL_AT)
        except Exception:
            return out
        return out

    def scan_streams(self) -> List[int]:
        """按「FLT vtable = flt+root_vt」这个 8 字节 needle 找所有 effect/parade 流。"""
        needle = self._needle()
        if not needle:
            return []
        roots, _hits = scan_heap(self.p, needle)
        return roots

    # ---- 会话缓存（这是「一进工程就变中文」的关键） -----------------
    def _roots_key(self):
        return (self.p.pid, getattr(self.p, "creation_time", 0),
                getattr(self.p, "image", ""), self.root_vt)

    def _needle(self) -> bytes:
        """vtable needle（FLT 基址 + root_vt）；FLT 没加载就返回空。"""
        mod = (getattr(self, "modules", {}) or {}).get("flt.dll")
        return struct.pack("<Q", mod["base"] + self.root_vt) if mod else b""

    def _remember_hits(self, hits: Dict[int, bytes], names=None) -> None:
        """记住这次顺手扫到的名字命中，供 NameTable 复用（免得再扫一遍堆）。

        必须记住「这批命中是拿哪些名字去搜的」—— 否则下游换了一批名字
        复用这份快照时，没被搜过的名字会静默缺席，表现为「这个参数名就是不
        变中文」，比慢更糟。
        """
        try:
            # 空快照不许缓存：cached_hits() 会把「有快照但为空」当成
            # 「确实一个都没搜到」，下游就不再自己扫。
            if not hits:
                return
            key = self._roots_key()
            searched = None if names is None else frozenset(
                n for n in names if isinstance(n, str) and n)
            _HITS_MEMO[key] = (time.monotonic(), hits, searched)
            _trim(_HITS_MEMO)
        except Exception:
            pass

    def cached_hits(self, names=None) -> Optional[Dict[int, bytes]]:
        """同一次扫描留下的名字命中；过期/未覆盖本次要搜的名字就返回 None。"""
        try:
            key = self._roots_key()
            got = _HITS_MEMO.get(key)
            if not got:
                return None
            at, hits, searched = got
            if time.monotonic() - at > _HITS_TTL:
                return None
            if names is not None:
                want = {n for n in names if isinstance(n, str) and n}
                # searched is None = 当初不知道拿哪些名字搜的（调用方没传）。
                # 这种「覆盖面不明」的快照一样不能用：宁可多扫一遍，也不能让
                # 某个参数名静默不翻。
                if searched is None or not want.issubset(searched):
                    return None
            return hits
        except Exception:
            return None

    def _remember_roots(self, roots: List[int]) -> None:
        """把这次扫到的根流并进会话缓存（只增不减：流地址在会话内不变）。"""
        try:
            key = self._roots_key()
            memo = _ROOTS_MEMO.setdefault(key, [])
            old = set(memo)
            for r in roots:
                if r not in old:
                    memo.append(r)
                    old.add(r)
            _ROOTS_AT[key] = time.monotonic()
            _trim(_ROOTS_MEMO)
            _trim(_ROOTS_AT)
        except Exception:
            pass

    def cached_roots(self) -> List[int]:
        """可以省掉这次全堆扫吗？能就返回已记住的根流，否则返回空。"""
        try:
            key = self._roots_key()
            roots = _ROOTS_MEMO.get(key)
            if not roots:
                return []
            if time.monotonic() - (_ROOTS_AT.get(key) or 0.0) > roots_ttl():
                return []
            return list(roots)
        except Exception:
            return []

    def _delta_regions(self):
        """上次扫过之后新提交/新变大的私有内存区间；无法差分时返回 None。

        ★ 这是「新加效果几百毫秒内变中文」的核心：流对象在同一个 AE 会话里
          不搬家，用户新加一个效果只会让私有堆多提交几页。只读这几页
          （毫秒级）就能发现新流，不必每拍重读 1.5GB。
        """
        try:
            key = self._roots_key()
            prev = _REGIONS_MEMO.get(key)
            cur = private_region_map(self.p)
            _REGIONS_MEMO[key] = cur
            _trim(_REGIONS_MEMO)
            if prev is None:
                return None                 # 没有基准 → 必须全量扫一次
            out = []
            budget = _DELTA_BUDGET
            for base, size in cur.items():
                old = prev.get(base)
                if old is None:
                    seg = (base, size)
                elif size > old:
                    seg = (base + old, size - old)
                else:
                    continue
                if seg[1] > budget:
                    # 预算用完：本拍只读这一段的前 budget 字节，剩下的下一拍
                    # 继续（差分基准已推进，不会漏；只是分两拍读完）。
                    if budget <= 0:
                        break
                    out.append((seg[0], budget))
                    break
                out.append(seg)
                budget -= seg[1]
            return out
        except Exception:
            return None

    def walk(self, effect_names: Optional[Iterable[str]] = None,
             expand: bool = True) -> List[Slot]:
        """找到所有参与显示的名字流: vtable needle 锚定 + 结构遍历参数表。

        expand=False 只返回根流（效果/parade 流本身的显示名），不展开参数表
        —— 常驻换名每 0.4 秒一拍时用这个：效果标题只需要根流，展开 600 个
        效果的参数树是几百次额外跨进程读，白花 CPU。参数名由守护那条路走。
        """
        names = [n for n in (effect_names or ()) if isinstance(n, str) and n] or None
        self._ensure_layout(
            names,
            allow_cjk=any(_looks_cjk(n) for n in (names or ())),
        )
        # ★★ 发现路径（2026-11-07 重构）：parade 根向量优先。
        #   工程里现存的效果流全部挂在 parade 对象的根向量上，读它只需要
        #   每个 parade 两次跨进程小读（实测 600 个流 × 4 次读 = 12ms），
        #   而且**不依赖堆是否新提交页** —— 堆会复用已提交内存，「只读新增页」
        #   的差分在真实 AE 里经常什么都看不到（这正是新加效果要等 2 秒以上
        #   才变中文的根因）。读向量是每次都能看到当前真值的。
        roots: List[int] = self.roots_from_parades()
        if roots:
            self._remember_roots(roots)
            self.diag["roots_src"] = "parade"
        if not roots:
            # 已知根流（上一拍/校准扫出来的）里就带着 parade 指针：反查一下
            # 就能拿到 parade，省掉一次全堆扫描（首拍约 0.8 秒）。
            roots = self._roots_from_known_parades()
            if roots:
                self.diag["roots_src"] = "known"
        if not roots and self._may_discover():
            # 第一拍 / 换了工程 / 关掉重开：全量找一次 parade（约 0.8 秒）
            self.discover_parades()
            roots = self.roots_from_parades()
            if roots:
                self._remember_roots(roots)
                self.diag["roots_src"] = "discover"
        # 新图层/新合成会新建 parade 对象 —— 记录已知 parade 之后，定期只扫
        # 新提交的私有页把它们找出来（毫秒级；没有新页就直接跳过）。
        key = self._parade_key()
        now = time.monotonic()
        if now - (_PARADES_AT.get(key) or 0.0) > _PARADE_RESCAN:
            _PARADES_AT[key] = now
            delta = self._delta_regions()
            if delta:
                found, new_hits = scan_heap(
                    self.p, self._needle(), names_regex(names), regions=delta)
                if found:
                    parades = set()
                    for root in found:
                        try:
                            parades.add(self.p.u64(root + 32))
                        except Exception:
                            pass
                    self.remember_parades(p for p in parades if p)
                    self._remember_roots(found)
                    roots = roots or found
                if new_hits:
                    self._remember_hits(new_hits, names)
        if not roots:
            roots = self.cached_roots()
            if roots:
                self.diag["roots_src"] = "cache"
        slots: List[Slot] = []
        seen = set()
        for stream in roots:
            if stream in seen:
                continue
            slot = self._slot_from_stream(stream)
            if slot is None:
                continue
            seen.add(stream)
            slots.append(slot)
        # 缓存失效自愈：关掉工程再打开会释放旧流对象，记住的地址全部变成野指针。
        # 这时必须把缓存丢掉重扫一次，否则整个会话都停在「0 候选」上。
        # ★ 自愈 / 兜底（2026-11-07 修正）：条件从「roots 非空但一个槽都校验
        #   不过」放宽到「一个槽都没有」。之前 parade 明明没找到（roots 为空）时
        #   这条捷径根本走不到，于是整条通道空转 —— 这正是「面板还是英文，日志
        #   却说已经是中文」的现场。
        #   ★ 必须节流：全量扫私有堆是几十到几百毫秒，0.4 秒一拍绝不能每拍都扫。
        if not slots and roots and self._may_full_scan():
            for table in (_ROOTS_MEMO, _ROOTS_AT, _REGIONS_MEMO):
                try:
                    table.pop(self._roots_key(), None)
                except Exception:
                    pass
            for stream in self.scan_streams():
                if stream in seen:
                    continue
                slot = self._slot_from_stream(stream)
                if slot is None:
                    continue
                seen.add(stream)
                slots.append(slot)
            if slots:
                self.diag["roots_src"] = "self-heal"
        # ★★ 最后兜底（2026-11-07）：needle 锚定依赖 root_vt 正确，而 root_vt 是
        #   本机适配/校准给的，25.x 上完全可能是错的 —— 那时 roots 非空但一个流都
        #   校验不过，或者干脆一个根都没有。这时**必须**退回「按名字锚定」：
        #   这条路只依赖 display_off（校准就是用它投票的），是唯一能在 root_vt
        #   猜错时仍然改到「效果控件」标题的路径。上一版把这个兜底放在 roots 判断
        #   之前，roots 非空就永远不走，于是面板一直英文而日志说「已经是中文」。
        if not slots and names and self._may_name_scan():
            # 按名字锚定：全量扫一次私有堆找这批英文名的 UTF-16 副本，
            # 再按 display_off 反推是哪个流（内联短名直接认、长名反查指针）。
            # ★ 命中后把流地址记进根流缓存：下一拍走便宜的 _slot_from_stream，
            #   不必每拍再全量扫 —— 否则 0.4 秒一拍就是每秒两次全堆扫描。
            got = self.find(names)
            for slot in got:
                if slot.stream in seen:
                    continue
                seen.add(slot.stream)
                slots.append(slot)
            if slots:
                self.diag["roots_src"] = "names"
                self._remember_roots([s.stream for s in slots])
        self.diag["roots"] = len(roots)
        self.diag["slots"] = len(slots)
        self.diag["off"] = self.display_off
        self.diag["vt"] = self.root_vt
        self.diag["note"] = self.layout_note
        # 堆体积/名字条数：真机上「能扫的私有堆只有 34MB」和「名字一个都没在
        # 这 34MB 里」是完全不同的两个故障，日志里必须能分辨。
        self.diag["heap_mb"] = round(_LAST_HEAP_MB, 1)
        self.diag["names"] = len(names or ())
        _LAST_DIAG.clear()
        _LAST_DIAG.update(self.diag)
        if not expand:
            return slots
        for slot in list(slots):
            for group in self._groups(slot.stream):
                ents, _off = self._param_table(group)
                for e in ents:
                    if e in seen:
                        continue
                    sub = self._slot_from_stream(e)
                    if sub is None:
                        continue
                    seen.add(e)
                    slots.append(sub)
        return slots

    # ---- 主流程 ---------------------------------------------------
    def translate(self, name: str) -> str:
        # ★ 已经是中文的不要再译：词典里混着把中文当英文键的条目
        #   （engine_dict 里实测 13 条，如「旋转」→「旋转」）。中文名直接返回空，
        #   调用方按「无需改写」处理，否则每一拍都会得到不同结果。
        if _looks_cjk(name):
            return ""
        for key in (name, name.strip(), name.lstrip(" ")):
            zh = self.translations.get(key)
            if zh and zh != name:
                return zh
        # AE 重复实例："CC Light Rays 2" 词典里没有这个键 —— 去掉编号后缀
        # 再查一次，并把编号原样加回去（"CC 光线 2"）。
        base, suffix = split_instance_suffix(name.strip(), self.known)
        if suffix:
            zh = self.translations.get(base)
            if zh and zh != base:
                return zh + suffix
        return ""

    def apply(self, names: Optional[Iterable[str]] = None, limit: int = 0,
              expand: bool = True) -> Dict:
        """把运行中 AE 里已有的效果实例显示名换成中文(不用重启)。

        ★ 只写 TDB 流的显示名缓存（「效果控件」面板标题 + 界面绘制路径），
          绝不写 FLT_FCSpec 注册表 —— 后者会让顶部「效果」菜单一起变中文。
        expand=False 只改效果类根流（常驻换名每拍调用时用，省掉参数树遍历）。
        """
        slots = self.walk(names, expand=expand)
        written = 0
        skipped = 0
        already = 0
        records = []
        done = set()
        for slot in slots:
            english = slot.english or slot.pf_name
            if not english:
                skipped += 1
                continue
            zh = self.translate(english)
            if not zh:
                # 区分「词典没命中」和「本来就是中文」：调用方靠这个判断
                # 这个会话是不是已经做完了。
                if _looks_cjk(english):
                    already += 1
                skipped += 1
                continue
            if slot.stream in done:
                skipped += 1
                continue
            if self.write_display(slot, zh):
                written += 1
                done.add(slot.stream)
                records.append({"stream": slot.stream, "english": english, "zh": zh})
            else:
                skipped += 1
            if limit and written >= limit:
                break
        return {"candidates": len(slots), "written": written, "skipped": skipped,
                "already": already, "records": records}

    def restore(self, records: Iterable[Dict]) -> Dict:
        """按会话记录写回英文原名（内存里此刻是中文）。"""
        records = list(records or ())
        seeds = []
        has_cjk = False
        for rec in records[:64]:
            for field in ("zh", "english"):
                val = rec.get(field)
                if isinstance(val, str) and val:
                    seeds.append(val)
                    has_cjk = has_cjk or _looks_cjk(val)
        try:
            self._ensure_layout(seeds, allow_cjk=has_cjk)
        except Exception:
            pass
        restored = 0
        skipped = 0
        for rec in records:
            stream = rec.get("stream")
            english = rec.get("english")
            if not stream or not english:
                skipped += 1
                continue
            try:
                head = self.p.read(stream + self.display_off, 0x20)
            except Exception:
                skipped += 1
                continue
            _buf, _res, _size, cap = struct.unpack("<QQQQ", head)
            slot = Slot(stream, "param", "", english, cap < 8,
                        INLINE_CAP if cap < 8 else min(cap - 1, MAX_NAME_BYTES))
            if self.write_display(slot, english):
                restored += 1
            else:
                skipped += 1
        return {"restored": restored, "skipped": skipped}


# ---- 名字槽（参数名实例表） -------------------------------------------
#
# 实测(2026-10-30, AE 2022 22.6.0.64): ExtendScript 的 effect.property(q).name
# 不是读 TDB 流的显示名缓存, 而是读每个效果实例自己的一张「名字槽表」:
#
#     每槽 32 字节, 存一个 NUL 结尾的 UTF-16 名字, 余下补 0
#     例 0x19f5766e0: 42 00 61 00 6c 00 6c 00 20 00 27 59 0f 5c 00 00  "Ball 大小"
#
# 原地写中文(比英文短, 放得下)后脚本立刻读到中文; 写回原字节即还原英文。
SLOT = 32
MEM_PRIVATE = 0x20000


def iter_private_regions(p: Process):
    """只走私有堆(MEM_PRIVATE): 名字槽都在堆里, 扫模块镜像既慢又没必要。"""
    for base, size in private_region_map(p).items():
        yield base, size


class NameTable:
    """按工程里的英文名找实例名字槽, 原地写中文 / 还原。"""

    def __init__(self, p: Process, translations: Dict[str, str]):
        self.p = p
        self.translations = dict(translations or {})
        #: 已知英文原名（词典的键）—— 重复实例后缀只在 base 属于它时才拆
        self.known = {k.strip() for k in self.translations if k.strip()}

    def translate(self, name: str) -> str:
        # 已是中文的名字不再查表（否则每拍得到不同结果）。
        if _looks_cjk(name):
            return ""
        for key in (name, name.strip(), name.lstrip(" ")):
            zh = self.translations.get(key)
            if zh and zh != name and len(zh) <= len(name):
                return zh
        # 重复实例："S_WarpPolar 2" → 去后缀查表 → "S_极坐标扭曲 2"。
        # 长度按「加回后缀的最终文案」判，否则多出来的 " 2" 会白占槽位。
        base, suffix = split_instance_suffix(name.strip(), self.known)
        if suffix:
            zh = self.translations.get(base)
            if zh and zh != base and len(zh + suffix) <= len(name):
                return zh + suffix
        return ""

    def _printable(self, addr: int) -> bool:
        try:
            raw = self.p.read(addr, SLOT)
        except Exception:
            return False
        return _slot_text_ok(raw)

    def scan(self, names: Iterable[str], hits: Optional[Dict[int, bytes]] = None) -> List[Dict]:
        """扫私有堆找这些名字的 UTF-16 副本, 只留「名字槽」形态的命中。

        hits 可由调用方传入 —— LiveParams 做 needle 扫描时已经顺手把同一次
        读到的堆数据里的名字命中收集下来了（见模块级 scan_heap），直接复用
        就把「两条通道各读一遍 1.5GB 私有堆」减成一遍。
        """
        keys = sorted({n for n in (names or ()) if n and len(n) >= 3},
                      key=len, reverse=True)
        pairs = [(n, self.translate(n)) for n in keys]
        pairs = [(n, zh) for n, zh in pairs if zh]
        if not pairs:
            return []
        # 缓存里的命中是按另一批名字的正则收集的，只保留本次真正要用的，
        # 否则会把不相干的名字当成候选（长度/后缀判断都会跑偏）。
        want = {n.encode("utf-16-le") for n, _zh in pairs}
        if hits is not None:
            hits = {a: raw for a, raw in hits.items() if raw in want}
        else:
            rx = re.compile(b"|".join(re.escape(n.encode("utf-16-le"))
                                      for n, _zh in pairs))
            hits = {}
            for base, size in iter_scan_regions(self.p):
                off = 0
                while off < size:
                    n = min(CHUNK, size - off)
                    try:
                        data = self.p.read(base + off, n)
                    except Exception:
                        off += n
                        continue
                    for m in rx.finditer(data):
                        hits[base + off + m.start()] = m.group(0)
                    off += n
        # ★ 名字槽判据(2026-11-05 修正): 原来的「32 字节对齐 + 邻槽也是名字 /
        #   整页 >=5」只是结构启发式, 不能当唯一闸门。保存过的工程里, UI 真正读的
        #   那些名字副本常常既不 32 字节对齐、所在页也不够 5 个命中, 于是整条效果
        #   被漏掉 —— 表现就是「新加的效果能汉化, 打开以前保存的工程却是英文」。
        #   实测(CC Jaws, 124 处): 只写启发式收下的那批 → 界面不变; 全部写 → 立刻
        #   变中文。现在改为「后面紧跟 UTF-16 NUL」即收, 由下面的尾部 NUL 校验兜底:
        #   ("CC Jaws" 嵌在 "CC Jaws 2" 里时后面跟的是空格, 会被校验挡掉)
        keep = {a: raw for a, raw in hits.items() if a % 2 == 0}
        out = []
        for a in sorted(keep):
            raw = keep[a]
            # ★ 槽长按名字自己算, 不能固定读 SLOT(32) 字节:
            #   "Mocha Mask and Track" 这类 >16 字符的名字 UTF-16 就超 32 字节,
            #   固定 32 字节读出来前缀必然对不上 → 整条被丢掉 → 界面永远英文。
            span = max(SLOT, (len(raw) // SLOT + 1) * SLOT)
            try:
                cur = self.p.read(a, span)
            except Exception:
                continue
            if cur[:len(raw)] != raw:
                continue
            if cur[len(raw):len(raw) + 2] != b"\x00\x00":
                continue
            out.append({"addr": a, "orig": cur, "en": raw.decode("utf-16-le"),
                        "zh": self.translate(raw.decode("utf-16-le"))})
        return out

    def _write_slot(self, addr: int, text: str, span: int) -> bool:
        data = text.encode("utf-16-le")
        pad = span - len(data)
        if pad < 2:
            return False
        payload = data + b"\x00" * pad
        return self._write(addr, payload)

    def _write(self, addr: int, payload: bytes) -> bool:
        """WriteProcessMemory + 回读校验（Process.write_buffer 返回值不可靠）。"""
        n = C.c_size_t()
        buf = C.create_string_buffer(payload, len(payload))
        ok = self.p.k.WriteProcessMemory(
            self.p.handle, C.c_void_p(addr), buf, len(payload), C.byref(n))
        if not ok or n.value != len(payload):
            return False
        try:
            return self.p.read(addr, len(payload)) == payload
        except Exception:
            return False

    def apply(self, names: Iterable[str], limit: int = 0,
              hits: Optional[Dict[int, bytes]] = None) -> Dict:
        found = self.scan(names, hits=hits)
        written = 0
        skipped = 0
        records = []
        for item in found:
            if not item["zh"]:
                skipped += 1
                continue
            # ★ span = 原英文占的字节 + 它的 NUL, 多一个字节都不写。
            #   原来 min(SLOT, ...) 对 >16 字符的名字只覆盖前 32 字节,
            #   尾巴残留英文; 但也不能按「整槽」写到 64 字节 —— 那会踩坏
            #   紧邻的其他数据(实测会让 AE 直接崩溃)。
            span = len(item["en"].encode("utf-16-le")) + 2
            if self._write_slot(item["addr"], item["zh"], span):
                written += 1
                records.append({"addr": item["addr"], "orig": item["orig"].hex(),
                                "en": item["en"], "zh": item["zh"]})
            else:
                skipped += 1
            if limit and written >= limit:
                break
        return {"candidates": len(found), "written": written, "skipped": skipped,
                "records": records}

    def restore(self, records: Iterable[Dict]) -> Dict:
        restored = 0
        skipped = 0
        for rec in records or ():
            addr = rec.get("addr")
            orig = rec.get("orig")
            if not addr or not orig:
                skipped += 1
                continue
            try:
                data = bytes.fromhex(orig)
            except ValueError:
                skipped += 1
                continue
            try:
                if self._write(int(addr), data):
                    restored += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        return {"restored": restored, "skipped": skipped}


# ---- 效果实例自身的效果名字段（+0x10）---------------------------------
#
# ★★ 2026-11-07 IG5 实证（这是「效果控件标题一直英文」的真正根因）
#
#   FLT_FCSpec::GetEffectName_MB   @0x180066b90 = `lea rax,[rcx+0x10]; ret`
#   FLT_FCSpec::SetEffectName(wstr)@0x18006BA20 = strcpy_s(this+0x10, 0x30, src)
#   FLT_FCSpec::SetEffectName(char*)@0x18006BAE0 = 同上（两个重载同一个写点）
#   FLT_FCSeqSpec::Read            @0x1800399D0 读工程文件 'fnma' chunk 后：
#       0x180039C2E  SetEffectName(this, &wstring)     → 写 this+0x10（48 字节 MBCS）
#       0x180039C3A  SetDisplayName(this, &wstring)    → 写 this+0xF0（UTF-16 wstring）
#       0x180039C47  SetDisplayName(this, &wstring)    → 再写一次
#
# 也就是说：实例名同时存在两份副本，+0x10 是 MBCS 单字节名，+0xF0 是宽字符
# 显示名缓存。「效果控件」面板标题读的是 GetEffectName 那一族（+0x10），
# 所以**只改 +0xF0 时面板标题纹丝不动** —— 这正是 2026-11-06 那一版真机上
# 日志写「效果标题已经是中文」而用户看到 S_TextureSpots 的原因。
#
# 与 FLT_FCSpec 注册表条目的唯一区别就是虚表（两者布局相同）：
#   注册表条目 vtable = flt_base + profile.fc_vtable  ← 必须只读，写它顶部
#                                                       「效果」菜单就跟着中文
#   效果实例   vtable = FLT_FCSeqSpec 的虚表          ← 这才是要写的对象
# 名字字段在对象内偏移 +0x10、长 48 字节、NUL 结尾、紧跟 +0x40 的 match 名，
# 这是 IG5 + 真机 RTTI 双重确认过的结构常量，不随 AE 版本变化。
EFFECT_NAME_OFF = 0x10
EFFECT_NAME_SIZE = 48
#: 名字字段的最大可写字节数（要留一个 NUL）
EFFECT_NAME_MAX = EFFECT_NAME_SIZE - 1


def name_encoding(profile=None) -> str:
    """效果名字段的编码：26.x 起 utf-8，更早按系统代码页（mbcs）。"""
    enc = (getattr(profile, "encoding", "") or "mbcs").lower()
    if enc in ("utf8", "utf-8"):
        return "utf-8"
    if enc in ("mbcs", "cp936", "gbk", "ansi"):
        return "mbcs"
    return enc


def _encode_name(text: str, encoding: str) -> Optional[bytes]:
    try:
        return text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return None


def name_index(names, encoding: str = "mbcs") -> Dict[bytes, str]:
    """{编码后的字节: 原名} —— 名字集合的 O(1) 过滤器。

    ★ 为什么不用「把上千个译名拼成一条正则」：词典有 2.6 万条、选区常用上千条，
      拼成交替式正则在 30MB+ 可写内存上要跑几秒；而一条通用「ASCII 串」正则
      一次就能捞到所有像名字的串，再在这里按集合过滤 —— 与词典大小无关。
      另一个好处：**能认带实例后缀的名字**（"S_TextureSpots 2" 这种），
      拼死的交替式正则会把它们整条漏掉。
    """
    idx: Dict[bytes, str] = {}
    for name in names or ():
        if not isinstance(name, str) or len(name) < 2:
            continue
        raw = _encode_name(name, encoding)
        if raw:
            idx[raw] = name
    return idx


def names_regex_mb(names, encoding: str = "mbcs"):
    """（保留给调用方自定义锚点用）把一批名字编成 MBCS 正则。"""
    pats = []
    for name in sorted({n for n in (names or ())
                        if isinstance(n, str) and len(n) >= 2},
                       key=len, reverse=True):
        raw = _encode_name(name, encoding)
        if raw:
            pats.append(re.escape(raw))
    if not pats:
        return None
    return re.compile(b"(?<![\x20-\x7e])(?:" + b"|".join(pats) + b")(?![\x20-\x7e])")


class EffectName(object):
    """一个「效果名字段」命中：对象地址 + 当前英文名 + 角色（实例/注册表）。"""

    __slots__ = ("obj", "english", "role", "vtable")

    def __init__(self, obj: int, english: str, role: str, vtable: int = 0):
        self.obj = obj
        self.english = english
        self.role = role
        self.vtable = vtable

    def __repr__(self):
        return "EffectName(0x%X, %r, %s)" % (self.obj, self.english, self.role)


#: 已确认的「效果名字段」对象 {(pid,创建时间,镜像): [(obj, role)]} —— 同一
#: AE 会话里对象地址不会搬家，所以每拍只回读这几十上百个 48 字节字段就够，
#: 全量重扫按 _EFFECT_SCAN_MIN 节流（新加的效果最多等这么久）。
_EFFECT_OBJ_MEMO: Dict[tuple, List[tuple]] = {}
_EFFECT_SCAN_AT: Dict[tuple, float] = {}
#: 全量重扫「效果名字段」的最小间隔（秒）。0.4 秒一拍 + 这个上限 = 新加的
#: 效果最迟 0.9 秒变中文，远低于用户要求的 2 秒；同时避免每拍都读一遍全堆。
_EFFECT_SCAN_MIN = 0.5
#: 上一拍「效果名字段」的诊断（app/backend.py 写进 live_watch.log）
_EFFECT_DIAG: Dict[str, object] = {}
#: 扫描源（可写区列表）的短缓存 —— VirtualQueryEx 要把整个地址空间走一遍，
#: 每拍重走一次纯属浪费；区域只增不减，TTL 内复用不影响发现新效果。
_EFFECT_REGIONS_MEMO: Dict[tuple, tuple] = {}
_EFFECT_REGIONS_TTL = 1.0
#: 最近一次全量扫描的耗时与读取量（诊断/节流用）
_LAST_EFFECT_SCAN: Dict[str, float] = {}
#: 宽档扫描的续扫游标 {session: (区间序号, 区间内偏移)} —— 分片扫完整个
#: 地址空间要好几拍，必须记住扫到哪，否则每次都从 0 开始 = 永远扫不完。
_WIDE_CURSOR: Dict[tuple, tuple] = {}
#: 宽档单拍读取预算（字节）。8MB 大约 40~80ms，0.4 秒一拍绰绰有余。
_WIDE_BUDGET = 8 << 20
#: 窄档（虚表 needle）单拍读取预算。★ 2026-11-07 真机事故：这一档原来没有
#: 预算，把「可写 MEM_PRIVATE」整档读完；用户开着大工程时那里面是 GB 级的
#: 页文件后盾（AE 的磁盘缓存/大缓冲区），一拍拍到 4330MB / 8162 秒 ——
#: watcher 被自己拖死，「新加的效果 0.4 秒内变中文」自然全部失效。
#: 12MB/拍 + 游标续扫，约 60~100ms，几拍就能把真正的对象堆（几十 MB）走完。
_NEEDLE_BUDGET = 12 << 20
#: 窄档续扫游标，同 _WIDE_CURSOR。
_NEEDLE_CURSOR: Dict[tuple, tuple] = {}
#: 单个区间超过这个大小就不当「对象堆」扫：GB 级区间是缓存/映射文件，
#: 对象实例永远在小的堆块里，读大区间等于在读磁盘。
_EFFECT_REGION_MAX = 128 << 20
_DISCOVER_CURSOR: Dict[Any, tuple] = {}   # parade 分片发现游标(按会话 key)
#: 上一拍因「区间过大」被跳过的总字节（诊断用，写进 live_watch.log）。
_EFFECT_SKIP: Dict[str, float] = {}

def _walk_log(msg: str) -> None:
    """效果名发现链诊断日志（<数据根>/walk_debug.log），仅在异常时需要看。"""
    try:
        import os as _os
        root = _os.path.join(
            _os.environ.get("LOCALAPPDATA") or ".", "AeLocalizer")
        with open(_os.path.join(root, "walk_debug.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s %s%s" % (time.strftime("%m-%d %H:%M:%S"), msg, chr(10)))
    except Exception:
        pass


# 效果实例地址表缓存（pid -> {streams, t, tried}），后台 walk 填充
_WALK_CACHE: Dict[int, Dict] = {}
_WALK_BUSY: set = set()
#: 根向量为空时，冷 discover 的全局节流（pid -> monotonic）
_EMPTY_ROOTS_AT: Dict[int, float] = {}


def effect_diag() -> Dict[str, object]:
    """上一拍 EffectNameTable 的诊断快照（只读）。"""
    return dict(_EFFECT_DIAG)


class EffectNameTable(object):
    """写效果**实例**的显示名缓存（「效果控件」标题的正源 = +display_off）。

    为什么不复用 LiveParams：LiveParams 的槽位语义是「TDB 显示名 wstring 缓存」
    （+0xE8/+0xF0、有 buf/size/cap 三段），而这里是定长 48 字节单字节串，
    两者的判据、写入方式、回滚方式都不同，混在一个类里只会互相污染。
    """

    def __init__(self, p: Process, translations: Optional[Dict[str, str]] = None,
                 profile=None, data_root=None, encoding: Optional[str] = None):
        self.p = p
        self.translations = dict(translations or {})
        self.known = {k.strip() for k in self.translations if k.strip()}

        #: 中文显示名 -> 英文原名（回读时认出「已经被写成中文」的实例）
        self._zh2en: Dict[str, str] = {}
        ambiguous = set()
        for _en, _zh in self.translations.items():
            if not (_en and _zh and _zh.strip() and _zh != _en):
                continue
            zh, en = _zh.strip(), _en.strip()
            if zh in ambiguous:
                continue
            if zh in self._zh2en and self._zh2en[zh] != en:
                self._zh2en.pop(zh)
                ambiguous.add(zh)
            else:
                self._zh2en[zh] = en
        self._unknown_seen = set()        # 本拍发现但认不出的效果名（诊断）
        self.data_root = data_root
        self._lp = None            # 复用只读 LiveParams 读 parade 最新根向量
        prof = profile if profile is not None else profile_of(p, data_root)
        self.profile = prof
        self.encoding = encoding or name_encoding(prof)
        self.display_off = display_offset(prof)
        self.fc_vt = 0
        self.root_vt = 0
        self._flt_code = []
        modules = {m["name"].lower(): m for m in _modules_of(p)}
        flt = modules.get("flt.dll")
        if not flt:
            raise RuntimeError("AE 还没加载 FLT.DLL，请等启动完成")
        try:
            size = os.path.getsize(flt["path"])
        except OSError:
            size = 0x200000
        base = int(flt["base"])
        self._flt_code = [(base, base + size)]
        fc = int(getattr(prof, "fc_vtable", 0) or 0)
        self.fc_vt = base + fc if fc else 0
        #: 效果实例（FLT_FCSeqSpec）的虚表 —— 「可以写」的白名单。
        #: 不认识的虚表一律不写，宁可漏改也不要写进别人的对象里。
        rv = int(getattr(prof, "root_vtable", 0) or 0)
        self.root_vt = base + rv if rv else 0
        self._key = (p.pid, getattr(p, "creation_time", 0),
                     getattr(p, "image", ""))
        #: 本拍诊断（写进 live_watch.log）
        self.diag: Dict[str, object] = {
            "inst": 0, "reg": 0, "scan": "cache", "written": 0,
        }

    # ---- 基础 -----------------------------------------------------
    def _in_flt(self, value: int) -> bool:
        return any(lo <= value < hi for lo, hi in self._flt_code)

    def _write(self, address: int, data: bytes) -> bool:
        if not data:
            return True
        n = C.c_size_t()
        buf = C.create_string_buffer(data, len(data))
        ok = self.p.k.WriteProcessMemory(
            self.p.handle, C.c_void_p(address), buf, len(data), C.byref(n))
        if not ok or n.value != len(data):
            return False
        try:
            return self.p.read(address, len(data)) == data
        except Exception:
            return False

    def read_name(self, obj: int) -> str:
        """读对象 +0x10 的 48 字节 MBCS 名字（读不到返回空串）。"""
        try:
            raw = self.p.read(obj + EFFECT_NAME_OFF, EFFECT_NAME_SIZE)
        except Exception:
            return ""
        head = raw.split(b"\x00", 1)[0]
        if not head:
            return ""
        for enc in (self.encoding, "utf-8", "latin-1"):
            try:
                return head.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return ""

    def _printable_name(self, obj: int) -> str:
        """+0x10 若是「NUL 结尾的纯 ASCII 名字」就返回它，否则空串。

        结构判据（IG5 实证）：效果名字段 = 定长 48 字节、NUL 结尾、MBCS 单字节。
        """
        try:
            raw = self.p.read(obj + EFFECT_NAME_OFF, EFFECT_NAME_SIZE)
        except Exception:
            return ""
        head = raw.split(b"\x00", 1)[0]
        if len(head) < 2 or len(head) > EFFECT_NAME_MAX:
            return ""
        if any(b < 0x20 or b > 0x7e for b in head):
            return ""
        try:
            return head.decode(self.encoding)
        except (UnicodeDecodeError, LookupError):
            return head.decode("latin-1")

    def _display_of(self, obj: int) -> str:
        """对象当前的名字：优先 +display_off 的显示名缓存（真实例的正源），
        缓存为空时才退回 +0x10 的定长 MBCS 字段（注册表条目走这条）。"""
        name = self.read_display(obj)
        if name:
            return name
        return self._printable_name(obj)

    def known_english(self, name: str) -> str:
        """内存里读到的显示名 -> 对应英文原名；认不出返回空串。

        同时认：英文原名、英文名+实例后缀（" 2"）、以及已译中文（含后缀）。
        ★ 认不出就绝不写 —— 这是「不往别人的对象里塞字节」的第一道闸。
        """
        n = (name or "").strip()
        if not n:
            return ""
        if n in self.translations:
            return n
        base, _suffix = split_instance_suffix(n, self.known)
        if base and base in self.translations:
            return n
        if n in self._zh2en:
            return self._zh2en[n]
        zbase, zsuffix = split_instance_suffix(n, set(self._zh2en))
        if zbase and zbase in self._zh2en:
            return self._zh2en[zbase] + zsuffix
        return ""

    def _classify(self, obj: int, english: str) -> Optional[EffectName]:
        """校验「这个地址确实是一个 FLT 对象、且 +0x10 正好是 english」。"""
        try:
            vtable = self.p.u64(obj)
        except Exception:
            return None
        if not self._in_flt(vtable):
            return None                 # 虚表不在 FLT.dll → 不是 FLT 对象
        raw = _encode_name(english, self.encoding)
        if raw is None:
            return None
        try:
            field = self.p.read(obj + EFFECT_NAME_OFF, EFFECT_NAME_SIZE)
        except Exception:
            return None
        # 字段里必须是「这个名字 + NUL」，不能是更长串的中间片段
        if field.split(b"\x00", 1)[0] == raw:
            return EffectName(obj, english, self._role_of(vtable), vtable)
        # 真实例的 +0x10 不是名字（2026-11-07 普查：FCSeqSpec 实例那里是别的
        # 数据，全是乱码），名字在 +display_off 的显示名缓存里 —— 认它。
        if self.read_display(obj) == english:
            return EffectName(obj, english, self._role_of(vtable), vtable)
        return None

    def _role_of(self, vtable: int) -> str:
        """按虚表判定这对象是「注册表条目」还是「效果实例」。

        · registry：FLT_FCSpec 注册表条目 = 顶部「效果」菜单的数据源 → 只读。
          这条判据必须硬：任何一个字节写进去，菜单就跟着变中文。
        · instance：其余带效果名字段的对象 → 写。
          不要求虚表恰好等于 root_vtable —— 各类效果（内置/第三方/CC）的
          实例可能是 FCSeqSpec 的派生类，虚表各不相同，卡死 root_vtable 会让
          整条通道在真机上完全空转（2026-11-06 那版 slots=0 就是这种走法）。
        · other：连 fc_vtable 都不知道（本机适配没有这个版本）→ 不敢写，
          因为那时无法把注册表条目剔出去，宁可不汉化也不能让菜单变中文。
        """
        if not self.fc_vt:
            return "other"
        return "registry" if vtable == self.fc_vt else "instance"

    # ---- 查找 -----------------------------------------------------
    def _match_name(self, run: bytes, idx: Dict[bytes, str]) -> Optional[str]:
        """内存里的一段 ASCII 串 → 已知英文效果名（认得出实例后缀）。"""
        eng = idx.get(run)
        if eng is not None:
            return eng
        try:
            text = run.decode(self.encoding, "strict")
        except (UnicodeDecodeError, LookupError):
            return None
        text = text.strip()
        base, _suffix = split_instance_suffix(text, self.known)
        if base != text and idx.get(_encode_name(base, self.encoding) or b""):
            # 返回带后缀的完整名：_classify 要拿它跟内存里的字段逐字节对齐
            return text
        return None

    def _sweep(self, source, idx, out, seen, budget: int = 0,
               cursor=None) -> tuple:
        """在给定区间里按名字锚定；budget>0 时只干这么多字节就交还游标。

        返回 (读了多少字节, 新游标)。游标为 None 表示这一轮扫完了。
        """
        ran = 0
        i, off = 0, 0
        if cursor:
            cbase, off = cursor
            # 游标存的是「区间基址 + 区间内偏移」而不是序号：区间列表每次
            # 重建后顺序可能变（新提交的区会插进来），按基址找才稳。
            for k, (b, _s) in enumerate(source):
                if b == cbase:
                    i = k
                    break
            else:
                off = 0
        while i < len(source):
            base, size = source[i]
            if off >= size:
                i += 1
                off = 0
                continue
            n = min(CHUNK, size - off)
            if budget:
                left = budget - ran
                if left <= 0:
                    return ran, (base, off)
                n = min(n, left)
            try:
                data = self.p.read(base + off, n)
            except Exception:
                off += n
                ran += n
                continue
            for m in _RUN_MB_RX.finditer(data):
                hit = base + off + m.start()
                obj = hit - EFFECT_NAME_OFF
                if obj <= 0x10000 or obj in seen:
                    continue
                eng = self._match_name(m.group(0), idx)
                if eng is None:
                    continue
                seen.add(obj)
                ent = self._classify(obj, eng)
                if ent is not None:
                    out.append(ent)
            off += n
            ran += n
        return ran, None

    def _needles(self) -> List[bytes]:
        """按虚表锚定的 needle：效果实例 / 注册表条目两类对象的首 8 字节。

        ★ 为什么还要虚表锚点（2026-11-07 真机性能教训）：按名字正则在可写
          内存上跑一遍要几百毫秒，0.4 秒一拍根本扛不住；而 bytes.find 是
          C 层匹配，35MB 只要十几毫秒 —— 用虚表地址找对象、再只读对象的
          +0x10/+0xF0，把每拍成本从「几百毫秒」压到「十几毫秒」。
        """
        out = []
        for vt in (self.root_vt, self.fc_vt):
            if vt:
                out.append(struct.pack("<Q", vt))
        return out

    def _needle_pass(self, source, out, seen, budget: int = 0,
                     cursor=None) -> tuple:
        """在给定区间里找「首 8 字节 = FLT 虚表」的对象。返回 (读了字节数, 游标)。

        ★ budget/cursor 与 _sweep 同款：单拍只读这么多字节，下一拍接着扫。
          没有预算是 11-07 真机「一拍 4330MB / 8162 秒」的根因。
        """
        needles = self._needles()
        if not needles:
            return 0, None
        ran = 0
        i, off = 0, 0
        if cursor:
            cbase, off = cursor
            for k, (b, _s) in enumerate(source):
                if b == cbase:
                    i = k
                    break
            else:
                off = 0
        while i < len(source):
            base, size = source[i]
            if off >= size:
                i += 1
                off = 0
                continue
            n = min(CHUNK, size - off)
            if budget:
                left = budget - ran
                if left <= 0:
                    return ran, (base, off)
                n = min(n, left)
            try:
                data = self.p.read(base + off, n)
            except Exception:
                off += n
                ran += n
                continue
            for needle in needles:
                vt = int.from_bytes(needle, "little")
                at = data.find(needle)
                while at >= 0:
                    # 对象首地址必须 8 字节对齐，否则只是别处存的副本
                    if (base + off + at) % 8 == 0:
                        obj = base + off + at
                        if obj > 0x10000 and obj not in seen:
                            seen.add(obj)
                            # ★ 必须再验一道「这个对象的显示名/名字字段
                            #   确实是已知效果名」：命中的可能是对象内部
                            #   存的副虚表指针，那个位置 +0xF0/+0x10 是
                            #   任意字节，写进去就是破坏内存。
                            eng = self.known_english(self._display_of(obj))
                            if eng:
                                out.append(EffectName(
                                    obj, eng, self._role_of(vt), vt))
                    at = data.find(needle, at + 1)
            off += n
            ran += n
        return ran, None

    def _regions(self, narrow: bool):
        """取扫描区间：窄档（私有堆）用短缓存，宽档（所有可写区）用长缓存。

        ★ 一律丢掉「GB 级大区间」：那是 AE 的磁盘缓存/映射文件，读它等于读
          磁盘（2026-11-07 真机 0.5MB/s 的元凶）。效果实例永远在小的堆块里。
        """
        key = (self._key, bool(narrow))
        memo = _EFFECT_REGIONS_MEMO.get(key)
        now = time.monotonic()
        ttl = _EFFECT_REGIONS_TTL if narrow else _EFFECT_REGIONS_TTL * 5
        if memo and now - memo[0] < ttl:
            return memo[1]
        skipped = 0
        src = []
        for b, s in (iter_heap_regions if narrow else iter_scan_regions)(self.p):
            if s <= _EFFECT_REGION_MAX:
                src.append((b, s))
            else:
                skipped += s
        _EFFECT_SKIP["mb"] = skipped / 1048576.0
        _EFFECT_REGIONS_MEMO[key] = (now, src)
        _trim(_EFFECT_REGIONS_MEMO)
        return src

    def scan(self, names, regions=None) -> List[EffectName]:
        """按名字锚定所有「效果名字段」。

        不依赖 parade 向量、不依赖 root_vtable、不依赖 display_cache ——
        只依赖 +0x10/+0x30 这个 IG5 实证的结构常量，所以连同版本号都不用猜。

        ★ 三层（2026-11-07 真机教训，按成本从低到高）：
          1. 虚表 needle（C 层 bytes.find，35MB 十几毫秒）——**每拍都跑**，
             新加/新打开的实例当拍就能抓到，这是「0.4 秒内变中文」的主力；
          2. 名字正则 窄档（可写 MEM_PRIVATE 堆）——按 _EFFECT_SCAN_MIN 节流，
             兜住虚表不在已知两个值上的派生类实例；
          3. 名字正则 宽档（全部可写区，含 MEM_MAPPED / DLL 可写节）——分片
             带游标，每拍只读 _WIDE_BUDGET 字节，十几拍覆盖一遍。
        """
        idx = name_index(names, self.encoding)
        if not idx:
            return []
        # 已经中文的实例留在内存里：中文按 mbcs/utf-8 都不是「可打印 ASCII」，
        # 上面那条通用正则本来就抓不到它们，正好跳过（不用每拍重写一遍）。
        out: List[EffectName] = []
        seen = set()
        t0 = time.monotonic()
        if regions is not None:
            ran, _ = self._sweep(regions, idx, out, seen)
            wide = False
        else:
            # ★ 每拍都做的窄扫 = 虚表 needle（C 层 find，35MB 约十几毫秒）。
            #   新加的效果对象一出现就带虚表，所以「加完 0.4 秒内变中文」
            #   靠的是这一步，不靠名字正则。
            # ★ 每拍都做的窄扫 = 虚表 needle（C 层 find）。**带预算和游标**：
            #   11-07 那版没有预算，开着大工程时一拍读 4330MB / 8162 秒，
            #   watcher 被拖死；现在 12MB/拍，几拍就能把真正的对象堆走完。
            narrow = self._regions(True)
            ran, ncur = self._needle_pass(narrow, out, seen,
                                          budget=_NEEDLE_BUDGET,
                                          cursor=_NEEDLE_CURSOR.get(self._key))
            if ncur is None:
                _NEEDLE_CURSOR.pop(self._key, None)
            else:
                _NEEDLE_CURSOR[self._key] = ncur
                _trim(_NEEDLE_CURSOR)
            wide = True
            # 名字正则只做**宽档分片**，不再对私有堆整档扫一遍：整档 35MB 要
            # 0.4 秒上下，正好吃掉一拍；而分片扫描的区间集合是私有堆的超集
            # （iter_writable_regions），捞派生类实例的效果完全一样。
            if wide:
                # 宽档分片：每拍只读 _WIDE_BUDGET 字节，游标记住扫到哪，
                # 下一拍接着扫。这样单拍耗时封顶（约 0.2 秒），又能在十几拍
                # 内把整个可写地址空间覆盖一遍（捞虚表不在两个已知值上的
                # 派生类对象）。
                src = self._regions(False)
                got, cur = self._sweep(src, idx, out, seen,
                                       budget=_WIDE_BUDGET,
                                       cursor=_WIDE_CURSOR.get(self._key))
                ran += got
                if cur is None:
                    _WIDE_CURSOR.pop(self._key, None)
                else:
                    _WIDE_CURSOR[self._key] = cur
                    _trim(_WIDE_CURSOR)
        secs = time.monotonic() - t0
        _LAST_EFFECT_SCAN.clear()
        _LAST_EFFECT_SCAN.update({"mb": ran / (1024.0 * 1024.0), "secs": secs,
                                  "wide": 1.0 if wide else 0.0,
                                  "skip_mb": float(_EFFECT_SKIP.get("mb") or 0.0)})
        return out

    # ---- 写入 -----------------------------------------------------
    def translate(self, name: str) -> str:
        """英文效果名 → 中文（已经是中文/查不到返回空串）。"""
        if _looks_cjk(name):
            return ""
        for key in (name, name.strip(), name.lstrip(" ")):
            zh = self.translations.get(key)
            if zh and zh != name:
                return zh
        base, suffix = split_instance_suffix(name.strip(), self.known)
        if suffix:
            zh = self.translations.get(base)
            if zh and zh != base:
                return zh + suffix
        return ""

    def read_display(self, obj: int) -> str:
        """读实例 +display_off 的 UTF-16 显示名缓存（std::wstring 布局）。"""
        off = self.display_off
        if not off:
            return ""
        try:
            head = self.p.read(obj + off, 0x20)
        except Exception:
            return ""
        buf, _res, size, cap = struct.unpack("<QQQQ", head)
        if size <= 0 or size > MAX_NAME_BYTES:
            return ""
        if cap < 8:
            return _decode(head[: size * 2])
        try:
            return _decode(self.p.read(buf, size * 2))
        except Exception:
            return ""

    def write_display(self, obj: int, text: str,
                     expect: Optional[str] = None) -> bool:
        """写 +display_off 的显示名缓存并同步 _Mysize。

        ★ 只写这里就够了（2026-11-07 真机定案）：AfterFXLib.dll（「效果控件」
          面板 UI）导入的是 TDB_StreamBase::GetDisplayName（返回 this+display_off
          的 wstring），实测面板标题跟着它变（截屏 + 内存普查双证）；实例的
          +0x10 在真实例上是别的数据，而且写它会被 AE 存进工程，所以绝不再写。
        ★ expect：只在「这段缓存当前读出来正好是 expect」时才写 —— 这是
          offset 猜错时的安全阀：off 不对读到的必然不是那个名字，直接跳过，
          绝不往别人的结构里塞字节。
        """
        off = self.display_off
        if not off:
            return False
        if expect is not None and self.read_display(obj) != expect:
            self.diag["dispmiss"] = int(self.diag.get("dispmiss") or 0) + 1
            return False
        try:
            head = self.p.read(obj + off, 0x20)
        except Exception:
            return False
        buf, _res, size, cap = struct.unpack("<QQQQ", head)
        data = text.encode("utf-16-le")
        units = len(data) // 2
        if size > MAX_NAME_BYTES:
            return False
        # 只认「结构自洽的 std::wstring」：内联态 cap 必须在 0..7 且 size<=cap，
        # 堆态 cap>=8 且 size<=cap。全零（cap=0/size=0）说明这一段根本不是
        # 初始化过的显示名，写进去就是往未知结构塞字节 —— 不动它，
        # 说明这段不是我们要写的显示名（宁可不改，也不破坏内存）。
        if cap > MAX_NAME_BYTES or size > int(cap) or int(cap) <= 0:
            return False
        # MSVC wstring 的容量以 UTF-16 单元计，结束符另有空间。
        if units > cap:
            return False
        target = obj + off if cap < 8 else buf
        if not self._write(target, data + b"\x00\x00"):
            return False
        return self._write(obj + off + 0x10, struct.pack("<Q", units))

    def write(self, ent: EffectName, text: str,
              expect: Optional[str] = None) -> bool:
        """只写 +display_off 的显示名缓存（**不再写 +0x10**）。

        ★ 2026-11-07 真机定案：
          · 「效果控件」面板标题读的就是 +display_off 的显示名缓存（截屏 +
            census 双向印证），写它就够；
          · +0x10 在真实例上是别的数据（普查里全是乱码），写它既改不到标题，
            又会被 AE 序列化进工程（fnma）—— 11-07 09:01 起三个自动保存里的
            中文效果名就是这么写进去的，于是关掉通道重进 AE 仍是中文。
        """
        disp = self.write_display(ent.obj, text, expect=expect)
        if disp:
            self.diag["disp"] = int(self.diag.get("disp") or 0) + 1
        return disp

    def _fast_pass(self) -> List[EffectName]:
        """回读上一拍记住的对象（每个只读 48 字节，几十个对象 <1ms）。

        ★ 这是「新加效果 0.4 秒内变中文」的主力：parade/根向量发现新实例要
          扫一遍内存，而新加的效果几乎都是**已记忆对象里的英文名还没换**，
          回读就能命中。
        """
        memo = _EFFECT_OBJ_MEMO.get(self._key) or []
        out: List[EffectName] = []
        dead = []
        for obj, role in memo:
            try:
                vtable = self.p.u64(obj)
            except Exception:
                dead.append((obj, role))
                continue
            if not self._in_flt(vtable):
                # 对象被释放/内存被复用（关工程、撤销新建效果）——忘掉它
                dead.append((obj, role))
                continue
            real_role = self._role_of(vtable)
            eng = self.known_english(self._display_of(obj))
            if not eng:
                continue
            out.append(EffectName(obj, eng, real_role, vtable))
        if dead:
            drop = set(dead)
            _EFFECT_OBJ_MEMO[self._key] = [x for x in memo if x not in drop]
        return out

    def _roots_for_walk(self):
        """效果实例地址表（后台建缓存，apply/revert 每拍毫秒级取用）。

        ★ 2026-11-07 晚真机数据：LiveParams.walk(expand=False) 能稳定给出
          12 个效果实例，但冷启动要 15.6 秒（parade 发现慢）。放进 apply
          的同步路径会卡界面，所以改成：第一次开通道时后台线程跑一次
          walk，结果按 pid 缓存；之后的每拍（含关通道还原）直接读缓存。
        """
        pid = int(getattr(self.p, "pid", 0) or 0)
        st = _WALK_CACHE.get(pid) or {}
        streams = st.get("streams") or []
        if streams:
            return streams
        if not pid or pid in _WALK_BUSY:
            return []
        if time.monotonic() - float(st.get("tried") or 0.0) < 5.0:
            return []
        _WALK_BUSY.add(pid)
        st["tried"] = time.monotonic()
        _WALK_CACHE[pid] = st

        def _bg():
            try:
                from core.proc import Process
                with Process(pid, False) as proc:
                    lp = LiveParams(proc, self.translations,
                                    data_root=self.data_root)
                    slots = lp.walk(expand=False) or []
                    out = [int(getattr(sl, "stream", 0) or 0) for sl in slots]
                    streams = [x for x in out if x]
                    _WALK_CACHE[pid] = {
                        "streams": streams,
                        "t": time.monotonic(),
                        "tried": time.monotonic(),
                    }
                    _walk_log("walk ok pid=%d slots=%d streams=%d"
                              % (pid, len(slots), len(streams)))
            except Exception as exc:
                _walk_log("walk failed pid=%d %s: %s"
                          % (pid, type(exc).__name__, exc))
            finally:
                _WALK_BUSY.discard(pid)

        threading.Thread(target=_bg, daemon=True).start()
        return []

    def _current_roots(self) -> List[int]:
        """当前 parade 根向量（每次刷新，不再只信 _WALK_CACHE 的旧地址）。

        ★ 2026-11-09 原始「第二遍不汉化」根因：_WALK_CACHE[pid].streams 一旦
          存在，_roots_for_walk 就永远返回第一轮的对象集；之后同 parade 新增
          的实例即使 SetDisplayName 即时汉化过，OFF 后第二次 ON 的 _live_apply
          也遍历不到它，于是永久英文。
          这里直接读 LiveParams.roots_from_parades()（已知 parade 的根向量，
          毫秒级、不扫堆），并把结果回写 _WALK_CACHE，OFF 的地址还原也同步。
          根向量为空时只触发后台 warm（expand=False），当前拍 no-op，绝不在
          apply/revert 线程里跑 54 秒冷 discover，也不回退陈旧缓存。
        """
        try:
            if self._lp is None:
                self._lp = LiveParams(self.p, self.translations,
                                      profile=self.profile,
                                      data_root=self.data_root)
            roots = [int(x) for x in (self._lp.roots_from_parades() or ()) if x]
        except Exception:
            roots = []
        pid = int(getattr(self.p, "pid", 0) or 0)
        if roots:
            _WALK_CACHE[pid] = {"streams": list(roots), "t": time.monotonic(),
                                "tried": time.monotonic()}
            self.diag["roots_src"] = "parade-live"
            return roots
        # 根向量为空：可能是全删、工程重置，或 parade 还没热身。这里绝不
        # 在 apply/revert 线程里同步跑 LiveParams.walk（实测写 54.9s）；只
        # 触发一次后台 warm，当前拍返回空，下一拍根向量就绪后再补。这样
        # 不会把 54 秒全堆发现挂在 OFF/ON 关键路径上。
        try:
            st = _WALK_CACHE.get(pid)
            if st and st.get("streams"):
                # 旧缓存含已删除/已展开参数树；丢掉 streams 让后台以
                # LiveParams.walk(expand=False) 重建，而不是信 294 个旧地址。
                st.pop("streams", None)
                st["tried"] = 0.0
            self._roots_for_walk()
        except Exception:
            pass
        self.diag["roots_src"] = "warm-pending"
        return []

    def _walk_pass(self, cancel=None) -> List[EffectName]:
        """用 parade 根向量发现效果实例（毫秒级，兜住「漏汉化/漏还原」）。

        ★ 2026-11-07 现场：needle 扫描每拍只覆盖 12MB，可写区却有 3200MB，
          扫完一遍要 100 多秒 —— 新加的效果要等、关通道时也只还原扫到的
          那几个。LiveParams 的 parade 根向量是现成的快路径（census 实测
          28 个流毫秒级拿全），拿到的就是同一批对象（vptr = root_vt，
          名字在 +display_off），所以这里用它做**主**发现链，扫描只当兜底。
        """
        roots = self._current_roots()
        self.diag["walk_n"] = len(roots)
        out: List[EffectName] = []
        seen = set()
        for stream in roots or ():
            if cancel is not None and cancel():
                break
            if not stream or stream in seen:
                continue
            seen.add(stream)
            try:
                vt = self.p.u64(stream)
            except Exception:
                continue
            # ★ 2026-11-07 晚：LiveParams.walk 给出的流对象是 TDB 流
            #   （vptr 在 TDB.dll，不在 FLT.dll），原来要求 _in_flt 会把
            #   全部实例毙掉 —— 真机日志 inst 恒为 0 就死在这里。
            #   这里只要「显示名命中词典」就认，role 再按虚表归属判。
            _disp = self._display_of(stream)
            eng = self.known_english(_disp)
            if not eng:
                # 诊断: 名字不在词典里(或已是中文)的实例。用户新加的插件效果
                # 若认不出会被静默跳过, 面板永远英文 —— 这里留证据。
                if _disp and not _looks_cjk(_disp):
                    self._unknown_seen.add(_disp[:60])
                continue
            role = self._role_of(vt) if self._in_flt(vt) else "instance"
            out.append(EffectName(stream, eng, role, vt))
        return out

    def _remember(self, ents) -> None:
        # 用当前 parade 结果替换/裁剪账本：已删除对象不能继续留在
        # _EFFECT_OBJ_MEMO 里被后续 apply/revert 当成有效实例。
        if not ents:
            return
        memo = _EFFECT_OBJ_MEMO.setdefault(self._key, [])
        cur = {e.obj for e in ents}
        kept = [(obj, role) for obj, role in memo if obj in cur]
        have = {obj for obj, _role in kept}
        changed = len(kept) != len(memo)
        for ent in ents:
            if ent.obj not in have:
                kept.append((ent.obj, ent.role))
                have.add(ent.obj)
                changed = True
        _EFFECT_OBJ_MEMO[self._key] = kept
        if changed:
            _trim(_EFFECT_OBJ_MEMO)

    def _may_scan(self) -> bool:
        now = time.monotonic()
        last = _EFFECT_SCAN_AT.get(self._key)
        if last is not None and now - last < _EFFECT_SCAN_MIN:
            return False
        _EFFECT_SCAN_AT[self._key] = now
        _trim(_EFFECT_SCAN_AT)
        return True

    def apply(self, names=None, limit: int = 0, cancel=None) -> Dict:
        """把内存里所有**效果实例**的 +0x10 英文名换成中文。

        注册表条目（vtable == flt+fc_vtable）只读不写 —— 那是顶部「效果」
        菜单的数据源，写它菜单就会跟着变中文（2026-11-06 的现场）。

        两条路径：
          · 快路径：回读已记住的实例对象（毫秒级），换掉还没换的名字；
          · 全量扫描：按名字锚定（_EFFECT_SCAN_MIN 节流），发现新对象。
        cancel: 可选回调；返回 True 时尽快停止，用于「用户已点 OFF，旧 ON
        线程不许再写中文」的开关竞态。
        """
        def _stop() -> bool:
            return bool(cancel is not None and cancel())

        if _stop():
            return {"cancelled": True, "written": 0, "records": []}
        # ★ 只用当前 parade 根向量。旧 _fast_pass/_EFFECT_OBJ_MEMO 会保留
        #   已删除/已释放实例（真机 18 条里含删除的 Light Rays 3..12），
        #   且根向量为空时的 scan 会跑 54.9s；改用 warm 后的当前根向量，
        #   未就绪就本拍 no-op，下一拍再补。
        walked = self._walk_pass(cancel=cancel)
        if not walked:
            _EFFECT_OBJ_MEMO.pop(self._key, None)
            self.diag = {"inst": 0, "reg": 0, "other": 0, "disp": 0,
                         "scan": "warm-pending", "written": 0}
            return {"candidates": 0, "written": 0, "skipped": 0,
                    "already": 0, "registry": 0, "other": 0,
                    "records": [], "untranslated": [], "unknown": [],
                    "names": [], "noopReason": "parade roots pending/empty"}
        ents = list(walked)
        self._remember(walked)
        written = skipped = already = registry = other = 0
        records = []
        self._unknown_seen = set()
        untranslated = []          # 发现了实例但词典里没有译文（诊断用）
        found_names = []           # 本拍发现的实例英文名（诊断用）
        done = set()
        for ent in ents:
            if _stop():
                self.diag = {
                    "inst": len(ents) - registry - other, "reg": registry,
                    "other": other, "disp": int(self.diag.get("disp") or 0),
                    "dispmiss": int(self.diag.get("dispmiss") or 0),
                    "scan": "cancelled", "written": written,
                }
                return {"cancelled": True, "written": written,
                        "records": records, "already": already,
                        "candidates": len(ents)}
            if ent.role != "instance":
                if ent.role == "registry":
                    registry += 1
                else:
                    other += 1
                continue
            if ent.obj in done:
                skipped += 1
                continue
            found_names.append(ent.english)
            zh = self.translate(ent.english)
            if not zh:
                if _looks_cjk(ent.english):
                    already += 1
                else:
                    untranslated.append(ent.english)
                skipped += 1
                continue
            # expect 用「当前实际显示名」：可能是英文原名，也可能是上一拍
            # 已经写成中文的译名（回读会把中文反查成英文原名）。
            cur = self.read_display(ent.obj) or ent.english
            if cur and cur == zh:
                already += 1          # 已经是中文了，别每拍重写同一段内存
                done.add(ent.obj)
                continue
            if self.write(ent, zh, expect=cur or None):
                written += 1
                done.add(ent.obj)
                records.append({"obj": ent.obj, "english": ent.english, "zh": zh})
            else:
                skipped += 1
            if limit and written >= limit:
                break
        self.diag = {
            "inst": len(ents) - registry - other, "reg": registry,
            "other": other, "disp": int(self.diag.get("disp") or 0),
            "dispmiss": int(self.diag.get("dispmiss") or 0),
            "scan": "parade",
            "written": written,
        }
        _EFFECT_DIAG.clear()
        _EFFECT_DIAG.update(self.diag)
        return {"candidates": len(ents), "written": written, "skipped": skipped,
                "already": already, "registry": registry, "other": other,
                "records": records,
                "untranslated": untranslated,
                "unknown": sorted(self._unknown_seen),
                "names": found_names}

    def revert(self, names=None, cancel=None) -> Dict:
        """把内存里已经是中文的显示名写回英文原名（关通道后的自愈）。

        与 restore 的区别：**不依赖会话账本**。工程文件里带着中文效果名时
        （2026-11-07 写 +0x10 那版留下的污染），AE 从工程里读出来的实例
        显示名本身就是中文，只有按「中文 -> 英文」反查再写回去才能清干净。
        只写显示名缓存，不写 +0x10 —— 所以不会再次污染工程。
        """
        if not self._zh2en:
            return {"restored": 0, "skipped": 0}
        def _stop() -> bool:
            return bool(cancel is not None and cancel())
        if _stop():
            return {"cancelled": True, "restored": 0, "skipped": 0}
        walked = self._walk_pass(cancel=cancel)  # 只信当前 parade 根向量
        if not walked:
            self.diag = {"mode": "revert", "restored": 0, "walked": 0,
                         "ents": 0, "roots": "warm-pending"}
            return {"restored": 0, "skipped": 0, "walked": 0, "ents": 0,
                    "cancelled": False}
        ents = list(walked)
        self._remember(walked)
        restored = skipped = 0
        for ent in ents:
            if _stop():
                return {"cancelled": True, "restored": restored,
                        "skipped": skipped, "walked": len(walked or []),
                        "ents": len(ents)}
            if ent.role != "instance":
                continue
            cur = self.read_display(ent.obj)
            if not cur or not self.known_english(cur):
                skipped += 1
                continue
            if cur == ent.english:
                skipped += 1
                continue
            if self.write_display(ent.obj, ent.english, expect=cur):
                restored += 1
            else:
                skipped += 1
        self.diag = {"mode": "revert", "restored": restored,
                     "walked": len(walked or []), "ents": len(ents)}
        _EFFECT_DIAG.clear()
        _EFFECT_DIAG.update(self.diag)
        return {"restored": restored, "skipped": skipped,
                "walked": len(walked or []), "ents": len(ents)}

    def restore(self, records, cancel=None) -> Dict:
        """按会话账本把显示名写回英文原名。"""
        restored = skipped = 0
        for rec in (records or ()):
            if cancel is not None and cancel():
                return {"cancelled": True, "restored": restored, "skipped": skipped}
            obj = rec.get("obj")
            english = rec.get("english")
            if not obj or not english:
                skipped += 1
                continue
            # expect 用当初写进去的中文：显示名缓存里还是那份中文才写回，
            # 已被 AE 自己刷新成别的（换工程/重建实例）就不动。
            if self.write(EffectName(int(obj), english, "instance"), english,
                          expect=rec.get("zh") or None):
                restored += 1
            else:
                skipped += 1
        return {"restored": restored, "skipped": skipped}


# ---- 会话记录（跨调用/跨重启保留，用于停止引擎时还原） -----------------
def session_path(data_root: str) -> str:
    return os.path.join(data_root, SESSION_FILE)


def _session_key(rec: Dict):
    """一条记录的「改写目标」标识（效果名流 / 效果实例名 / 参数名槽）。"""
    if rec.get("stream"):
        return ("stream", int(rec["stream"]))
    if rec.get("obj"):
        return ("obj", int(rec["obj"]))
    if rec.get("addr"):
        return ("addr", int(rec["addr"]))
    return ("other", id(rec))


def save_session(data_root: str, pid: int, records: List[Dict]) -> str:
    """把本批改写记录并入会话记录（累积，不是覆盖）。

    ★ 2026-11-04 修「关掉效果名汉化后仍有部分效果保持中文」：
      旧实现每次都整体覆盖 records。效果名会分多批写入（开关、补写、
      引擎启停各一批），后一批直接把前一批的原始字节记录冲掉 ——
      关开关时只能还原最后一批，之前那些效果的英文原名已经无从得知。
      现在按 pid 合并：同一实例的就追加，换了 AE 进程（pid 变了）才整体重置。
    """
    path = session_path(data_root)
    records = [r for r in (records or []) if isinstance(r, dict)]
    try:
        old = load_session(data_root)
    except Exception:
        old = {}
    merged: List[Dict] = []
    if int(old.get("pid") or 0) == int(pid or 0):
        merged = [r for r in (old.get("records") or []) if isinstance(r, dict)]
    # 同一目标只保留最早那条（最早那条里的原始字节才是真英文原名）
    seen = set()
    for rec in merged:
        seen.add(_session_key(rec))
    for rec in records:
        key = _session_key(rec)
        if key in seen:
            continue
        seen.add(key)
        merged.append(rec)
    payload = {"pid": pid, "time": time.time(), "records": merged}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        return ""
    return path


def load_session(data_root: str) -> Dict:
    path = session_path(data_root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def clear_session(data_root: str) -> None:
    try:
        os.remove(session_path(data_root))
    except OSError:
        pass
