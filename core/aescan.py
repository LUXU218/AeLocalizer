"""AE 模块静态识别:从磁盘上的 FLT.dll/TDB.dll 推导适配 profile。

只读,不碰进程、不写任何文件。证据来源是导出符号语义 + MSVC RTTI +
确定性的指令形态,全部要求唯一;识别不出就失败,绝不猜地址。
移植自 After-Effects-Plugin-Localizer 的确定性识别路径(MIT,
Copyright (c) 2026 verse),去掉其 AI 代理层,保留可复现的静态证据。

依赖 capstone + pefile(项目 Python 已装);缺失时报可读的错误。
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

MODULES = ("FLT.dll", "TDB.dll")
MAX_MODULE_BYTES = 128 * 1024 * 1024

_GETTER_PREFIXES = {
    "FLT.dll": (
        ("?GetEffectName_MB@FLT_FCSpec@@", "name_get"),
        ("?GetMatchName@FLT_FCSpec@@", "match_get"),
        ("?GetCategoryName@FLT_FCSpec@@", "category_get"),
    ),
    "TDB.dll": (
        ("?GetParent@TDB_StreamBase@@", "parent_get"),
        ("?GetMatchName@TDB_StreamBase@@", "stream_match_get"),
        ("?GetNumStreams@TDB_IndexedStreamGroup@@", "indexed_count_get"),
        ("?GetNumStreams@TDB_NamedStreamGroup@@", "count_get"),
        ("?GetDisplayName@TDB_StreamBase@@", "display_get"),
    ),
}


def _load_tools():
    try:
        import capstone
        import pefile
    except ImportError as error:
        raise RuntimeError("静态识别需要 capstone 和 pefile 库:" + str(error)) from None
    return capstone, pefile


def _unique(values, label):
    values = set(values)
    if len(values) != 1:
        raise ValueError(label + "未找到唯一证据")
    return values.pop()


def module_paths(exe) -> dict:
    """FLT.dll/TDB.dll 的候选路径:AfterFX.exe 同级的 Support Files 优先。"""
    base = Path(exe).parent
    found = {}
    for name in MODULES:
        for candidate in (base / "Support Files" / name, base / name):
            if candidate.is_file():
                found[name] = candidate
                break
    return found


class ModuleTools:
    """两个模块的 PE 视图 + 反汇编器。所有 span 读取都限定在有效节内。"""

    def __init__(self, exe, version_of=None):
        capstone, pefile = _load_tools()
        self.capstone = capstone
        self.pefile = pefile
        self.exe = Path(exe)
        self.version = (version_of or _default_version_of)(self.exe)
        self.modules = {}
        paths = module_paths(self.exe)
        for name in MODULES:
            path = paths.get(name)
            if path is None:
                continue
            if path.is_symlink() or path.is_junction():
                raise ValueError("分析模块不能是链接")
            if path.stat().st_size > MAX_MODULE_BYTES:
                raise ValueError("AE 模块缺失或过大")
            data = path.read_bytes()
            try:
                pe = pefile.PE(data=data, fast_load=True)
                pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
            except pefile.PEFormatError:
                raise ValueError("AE 模块不是有效 PE 文件:" + name) from None
            if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.SizeOfImage > 256 * 1024 * 1024:
                raise ValueError("静态识别只支持常规 Windows x64 模块")
            self.modules[name] = {"path": path, "data": data, "pe": pe, "sha256": hashlib.sha256(data).hexdigest()}
        if "FLT.dll" not in self.modules:
            raise ValueError("找不到 FLT.dll(应在 AfterFX.exe 同级的 Support Files 里)")
        self.dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.dis.detail = True

    def close(self):
        for m in self.modules.values():
            m["pe"].close()

    def module(self, name):
        if name not in self.modules:
            raise ValueError("只能分析所选 AE 的 FLT.dll / TDB.dll")
        return self.modules[name]

    def span(self, name, rva, size, *, code=False):
        m = self.module(name)
        pe = m["pe"]
        if not 0 <= rva < pe.OPTIONAL_HEADER.SizeOfImage:
            raise ValueError("RVA 超出模块范围")
        if not 1 <= size <= 4096:
            raise ValueError("读取长度超出工具上限")
        for section in pe.sections:
            start = section.VirtualAddress
            end = start + section.SizeOfRawData
            if start <= rva and rva + size <= end:
                if code and not section.Characteristics & 0x20000000:
                    raise ValueError("反汇编范围不在可执行节")
                offset = section.PointerToRawData + rva - start
                data = m["data"][offset : offset + size]
                if len(data) == size:
                    return data
        raise ValueError("读取范围不在模块文件有效节内")

    def _symbols(self, name):
        pe = self.module(name)["pe"]
        exports = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        symbols = getattr(exports, "symbols", []) if exports else []
        for symbol in symbols:
            if symbol.name and not symbol.forwarder:
                yield symbol.address, symbol.name.decode("ascii", "replace")

    def instructions(self, name, rva, size=256):
        data = self.span(name, rva, size, code=True)
        result = []
        for ins in self.dis.disasm(data, rva):
            result.append(ins)
            if ins.mnemonic == "ret":
                break
        return result

    def getters(self):
        """名称/父子/容器 getter 的导出地址与 `lea rax,[rcx+disp]; ret` 字段偏移。"""
        result = []
        for name, items in _GETTER_PREFIXES.items():
            if name not in self.modules:
                continue
            symbols = list(self._symbols(name))
            for prefix, field in items:
                found = [addr for addr, text in symbols if text.startswith(prefix)]
                # const/非 const 的 parent 重载首条指令一致,取第一个;其余符号多解即放弃。
                if not found:
                    continue
                if len(found) > 1 and field != "parent_get":
                    continue
                address = found[0]
                result.append({"field": field, "module": name, "value": address})
                try:
                    raw = self.span(name, address, 32, code=True)
                    ins = list(self.dis.disasm(raw, address, count=3))
                except ValueError:
                    continue
                if len(ins) >= 2 and ins[1].mnemonic == "ret" and ins[0].mnemonic in ("lea", "mov"):
                    ops = ins[0].operands
                    if (
                        len(ops) == 2
                        and ops[0].type == self.capstone.CS_OP_REG
                        and ins[0].reg_name(ops[0].reg) == "rax"
                        and ops[1].type == self.capstone.CS_OP_MEM
                    ):
                        mem = ops[1].mem
                        if ins[0].reg_name(mem.base) == "rcx" and mem.index == 0 and 0 <= mem.disp <= 4096:
                            offset_field = {
                                "name_get": "name_offset",
                                "match_get": "match_offset",
                                "category_get": "category_offset",
                                "parent_get": "parent_offset",
                                "stream_match_get": "guid_offset",
                            }.get(field)
                            if offset_field:
                                result.append({"field": offset_field, "module": name, "value": mem.disp})
                if field == "indexed_count_get" and len(ins) >= 3 and [i.mnemonic for i in ins[:2]] == ["mov", "sub"]:
                    operands = [i.operands for i in ins[:2]]
                    ok = all(
                        len(o) == 2
                        and o[0].type == self.capstone.CS_OP_REG
                        and ins[0].reg_name(o[0].reg) == "rax"
                        and o[1].type == self.capstone.CS_OP_MEM
                        and ins[0].reg_name(o[1].mem.base) == "rcx"
                        and not o[1].mem.index
                        for o in operands
                    )
                    if ok:
                        end, begin = (o[1].mem.disp for o in operands)
                        if 0 <= begin <= 4096 and end - begin == 8:
                            result.append({"field": "group_vector", "module": name, "value": begin})
        return result

    def manager(self):
        """效果注册表管理器:pointer getter `?FLTp_GetFCAddress@@` 调用的
        `mov rax,[rip+disp]; ret`,且目标在可写节。"""
        symbols = [addr for addr, text in self._symbols("FLT.dll") if text.startswith("?FLTp_GetFCAddress@@") and text.endswith("@@F@Z")]
        entry = _unique(symbols, "FLTp_GetFCAddress")
        result = []
        for ins in self.instructions("FLT.dll", entry):
            if ins.mnemonic != "call" or ins.operands[0].type != self.capstone.CS_OP_IMM:
                continue
            target = ins.operands[0].imm
            try:
                body = self.instructions("FLT.dll", target, 32)
            except ValueError:
                continue
            if len(body) != 2 or body[0].mnemonic != "mov" or body[1].mnemonic != "ret":
                continue
            ops = body[0].operands
            if (
                len(ops) != 2
                or ops[0].type != self.capstone.CS_OP_REG
                or body[0].reg_name(ops[0].reg) != "rax"
                or ops[1].type != self.capstone.CS_OP_MEM
            ):
                continue
            mem = ops[1].mem
            if mem.base == self.capstone.x86_const.X86_REG_RIP:
                rva = body[0].address + body[0].size + mem.disp
                if self._writable("FLT.dll", rva):
                    result.append(rva)
        return _unique(result, "效果目录管理器")

    def _writable(self, name, rva) -> bool:
        section = self.module(name)["pe"].get_section_by_rva(rva)
        return bool(section and section.Characteristics & 0x80000000)

    def primary_vtable(self, typename) -> int:
        """RTTI 定位主虚表:类型描述符 → COL → 只读节里首函数在代码节的虚表。"""
        pe = self.module("FLT.dll")["pe"]
        image = pe.get_memory_mapped_image()
        result = []
        at = 0
        needle = typename.encode() + b"\0"
        while True:
            at = image.find(needle, at)
            if at < 0:
                break
            descriptor = at - 16
            at += 1
            start = 0
            while True:
                found = image.find(struct.pack("<I", descriptor), start)
                if found < 0:
                    break
                start = found + 1
                col = found - 12
                if col < 0 or col + 24 > len(image):
                    continue
                signature, offset, _, _, _, self_rva = struct.unpack_from("<IIIIII", image, col)
                if signature != 1 or offset != 0 or self_rva != col:
                    continue
                ref = struct.pack("<Q", pe.OPTIONAL_HEADER.ImageBase + col)
                position = 0
                while True:
                    position = image.find(ref, position)
                    if position < 0:
                        break
                    vt = position + 8
                    position += 1
                    section = pe.get_section_by_rva(vt)
                    if vt % 8 or not section or section.Characteristics & 0x80000000:
                        continue
                    function = struct.unpack_from("<Q", image, vt)[0] - pe.OPTIONAL_HEADER.ImageBase
                    function_section = pe.get_section_by_rva(function)
                    if function_section and function_section.Characteristics & 0x20000000:
                        result.append(vt)
        return _unique(result, typename + "主虚表")

    def verify_files(self):
        """分析结束后复核模块未变化(AE 更新进行中是唯一可重试的失败)。"""
        from .proc import file_version

        if file_version(self.exe) != self.version:
            raise ValueError("分析期间 AE 版本发生变化")
        for m in self.modules.values():
            with m["path"].open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != m["sha256"]:
                raise ValueError("分析期间模块发生变化")

    def complete_static(self, host_version: str):
        """组合全部静态证据成一个候选 Profile(未启用,registry_vector 待运行时校验)。
        效果注册表字段必须齐全;TDB 参数字段尽力而为,缺失时只支持效果名通道。"""
        from .adapter import Profile, default_encoding

        observations = self.getters()
        fields = {}
        for e in observations:
            key = e["field"]
            if key in fields and fields[key] != e["value"]:
                raise ValueError("字段证据冲突:" + key)
            fields[key] = e["value"]
        missing = [k for k in ("name_get", "match_get", "category_get") if k not in fields]
        if missing:
            raise ValueError("未找到唯一的名称接口:" + "、".join(missing))
        for field, value in (("name_offset", 16), ("match_offset", 64), ("category_offset", 96)):
            if fields.get(field) != value:
                raise ValueError("当前名称字段族不匹配:" + field)
        params = dict(
            tdb_sha=self.modules.get("TDB.dll", {}).get("sha256", ""),
            parent_get=fields.get("parent_get", 0),
            stream_match_get=fields.get("stream_match_get", 0),
            count_get=fields.get("count_get", 0),
            count_bytes=b"",
            display_get=fields.get("display_get", 0),
            match_pool=0,
            display_cache=0,
            group_vector=fields.get("group_vector", 0),
            named_vector=False,
            named_map=0x228,
            codec_sha="",
            native_dynamic=False,
        )
        if "TDB.dll" in self.modules and all(k in fields for k in ("parent_get", "stream_match_get", "count_get", "group_vector")):
            try:
                count = fields["count_get"]
                raw = self.span("TDB.dll", count, 19, code=True)
                if raw.startswith(bytes.fromhex("488b81")):
                    if raw[7:10] != bytes.fromhex("482b81") or raw[14:19] != bytes.fromhex("48c1f803c3"):
                        raise ValueError("命名参数向量指令不匹配")
                    params["named_vector"] = True
                    params["count_bytes"] = raw[:14]
                    params["named_map"] = fields["group_vector"] + 8
                elif raw[:2] == bytes.fromhex("8b81") and raw[6] == 0xC3:
                    params["count_bytes"] = raw[:7]
                    params["named_map"] = struct.unpack_from("<I", raw, 2)[0] - 8
                else:
                    raise ValueError("尚未识别命名参数容器")
            except ValueError:
                params = {**params, "count_get": 0, "count_bytes": b"", "named_vector": False}
        return Profile(
            version=self.version,
            host_version=host_version,
            flt_sha=self.modules["FLT.dll"]["sha256"],
            manager=self.manager(),
            registry_vector=0,
            fc_vtable=self.primary_vtable(".?AVFLT_FCSpec@@"),
            name_get=fields["name_get"],
            match_get=fields["match_get"],
            category_get=fields["category_get"],
            encoding=default_encoding(self.version),
            enabled=False,
            **params,
        )


def _default_version_of(exe) -> str:
    from .proc import file_version

    return file_version(exe)


def analyze_install(exe, progress=None, cancel=None) -> dict:
    """离线静态识别。返回报告 dict(candidate 字段 + 模块哈希 + 缺失清单),
    永不启用任何 profile。"""
    tools = None
    report = {
        "schemaVersion": 1,
        "kind": "aeloc-adapter-analysis",
        "version": "",
        "modules": {},
        "state": "blocked",
        "candidate": {},
        "missing": ["完整宿主目录交叉校验(registry_layout)"],
    }
    stage = "读取模块"

    def step(text):
        nonlocal stage
        if cancel is not None and cancel.is_set():
            raise RuntimeError("自动识别已取消")
        stage = text
        if progress:
            progress("自动识别:" + text)

    try:
        step(stage)
        tools = ModuleTools(exe)
        report["version"] = tools.version
        report["modules"] = {name: m["sha256"] for name, m in tools.modules.items()}
        step("识别名称接口与字段")
        # ★ 2026-10-28:host_version 用 app.version 口径(如 "26.5x89"),
        #   脚本桥拿它和 app.version 对账;原来传空串,桥必抛「AE 构建不匹配」
        _vp = (tools.version or "").split(".")
        profile = tools.complete_static(
            "%s.%sx%s" % (_vp[0], _vp[1], _vp[3]) if len(_vp) == 4 else "")
        profile = _language_candidate(tools, profile, report)
        step("复核模块未变化")
        tools.verify_files()
        if cancel is not None and cancel.is_set():
            raise RuntimeError("自动识别已取消")
        report["state"] = "candidate"
        report["candidate"] = _profile_to_candidate(profile)
        report["paramsSupported"] = bool(profile.count_get and profile.root_vtable)
        report["missing"] = ["完整宿主目录交叉校验(registry_layout)"]
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        kind = {"读取模块": "module"}.get(stage, "structure")
        report["failureStage"] = stage
        report["failureKind"] = kind
        report["summary"] = stage + "失败:" + str(error)
    finally:
        if tools:
            tools.close()
    return report


def _language_candidate(tools, profile, report):
    """Collect read-only evidence for the installed parameter channels."""
    from dataclasses import replace
    from .adapter import year_of
    try:
        from runtime import build_profile as bp
    except ImportError as error:
        report["parameterDrawError"] = str(error)
        return profile

    if not year_of(profile.version):
        return profile
    lib = tools.exe.parent / "AfterFXLib.dll"
    try:
        if lib.is_symlink() or lib.is_junction() or lib.stat().st_size > MAX_MODULE_BYTES:
            report["parameterDrawError"] = "AfterFXLib.dll 路径或大小未通过验证"
            return profile
        draw = bp.draw_candidate(lib, allow_unknown=True)
    except (bp.BuildMismatch, ValueError, OSError) as error:
        report["parameterDrawError"] = str(error)
        return profile
    profile = replace(profile, parameter_draw_sha=draw["sha256"])
    flt = tools.module("FLT.dll")["path"]
    stage = "FLT 效果树"
    try:
        root = bp.parade_vtable(flt, profile.flt_sha, allow_unknown=True)
        profile = replace(profile, root_vtable=root["vt_rva"])
        if not profile.count_get:
            report["popupLayoutError"] = "TDB 参数流枚举布局未通过校验"
            return profile
        stage = "FLT 下拉控件类型"
        bp.popup_profile(flt, profile.flt_sha, allow_unknown=True)
        stage = "TDB 选项指针"
        tdb = tools.module("TDB.dll")["path"]
        try:
            bp.popup_field_layout(tdb, profile.tdb_sha)
            return replace(profile, popup_layout_verified=True)
        except bp.BuildMismatch:
            media = tools.exe.parent / "MediaFoundation.dll"
            if (not media.is_file() or media.is_symlink() or media.is_junction()
                    or media.stat().st_size > MAX_MODULE_BYTES):
                raise
            stage = "TDB 与 MediaFoundation 选项指针"
            media_sha = bp.sha256_file(media)
            bp.media_popup_field_layout(tdb, profile.tdb_sha, media, media_sha)
            return replace(profile, popup_layout_verified=True, popup_media_sha=media_sha)
    except (bp.BuildMismatch, ValueError, OSError) as error:
        report["popupLayoutError"] = f"{stage}：{error}"
        return profile


def _profile_to_candidate(profile) -> dict:
    from .adapter import _profile_to_json

    return _profile_to_json(profile)


def candidate_from_report(report: dict):
    from .adapter import _profile_from_json

    if report.get("kind") != "aeloc-adapter-analysis" or report.get("state") != "candidate":
        raise ValueError("识别报告不是有效候选")
    return _profile_from_json(report["candidate"])
