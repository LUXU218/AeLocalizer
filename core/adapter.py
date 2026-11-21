"""版本适配层:每个 AE 构建一份签名 profile,先自检后写入。

profile 按 FLT.dll/TDB.dll 的 SHA-256 定版,内置条目来自参考项目
After-Effects-Plugin-Localizer(MIT,Copyright (c) 2026 verse)的已验证布局;
本机适配条目存 <data_root>/adapter_profiles.json,只有通过完整目录交叉
校验(registry_layout)才允许保存为 enabled。发现新构建时绝不借用其他
构建的地址。
"""
from __future__ import annotations

import json
import struct
import threading
from dataclasses import asdict, dataclass, fields as dc_fields, replace
from pathlib import Path

SCHEMA_VERSION = 1
_SAVE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Profile:
    # ---- 效果注册表(FLT.dll,方案 C 实时写效果名所需) ----
    version: str                # 精确构建号,如 "25.3.0.71"
    flt_sha: str
    manager: int                # FLT.dll 内指向效果注册表管理对象的指针槽 RVA
    registry_vector: int        # 管理对象上 begin/end 向量的偏移(运行时校验得出)
    fc_vtable: int              # 效果条目对象的虚表 RVA
    name_get: int               # ?GetEffectName_MB getter RVA(偏移 +16)
    match_get: int              # ?GetMatchName getter RVA(偏移 +64)
    category_get: int           # ?GetCategoryName getter RVA(偏移 +96)
    # ★ 2026-10-28 实测补:方案 D 的脚本桥用它跟 app.version 对账(如 "26.5x89");
    #   原来字段根本不存在,run_native/complete_static 一跑就 AttributeError/TypeError
    host_version: str = ""      # app.version 口径("26.5x89"),空 = 按 version 推导
    # ---- 参数流(TDB.dll,方案 B 枚举 / 方案 D 原生写入所需) ----
    tdb_sha: str = ""
    root_vtable: int = 0        # 效果 parade 根对象虚表 RVA
    parent_get: int = 0         # ?GetParent getter RVA(偏移 +0x20)
    stream_match_get: int = 0   # TDB_StreamBase::GetMatchName RVA(偏移 +8)
    count_get: int = 0          # 命名组数量 getter RVA
    count_bytes: bytes = b""    # count getter 的期望指令字节
    display_get: int = 0        # ?GetDisplayName RVA
    match_pool: int = 0         # TDB 内部标识池 RVA
    display_cache: int = 0      # 流对象上显示名 wstring 缓存偏移
    group_vector: int = 0       # 流容器向量偏移
    named_vector: bool = False  # 命名组是否为连续向量(否则 std::map/unordered_map)
    named_map: int = 0x228      # 命名容器头偏移
    codec_sha: str = ""         # U.dll(编码转换)SHA-256
    native_dynamic: bool = False  # 参数写入需要原生辅助 DLL 按哈希自查
    encoding: str = "utf8"      # 名称在注册表里的编码:utf8 | mbcs
    match_encoding: str = ""    # 内部名编码与名称不同时单列(AE 2021);'' 同 encoding
    named_tree: bool = False    # 命名组是 std::map 红黑树(AE 26.5)
    enabled: bool = False       # 只有通过校验的条目才允许写入
    parameter_draw_sha: str = ""  # 本机结构探测使用的 AfterFXLib.dll
    popup_layout_verified: bool = False
    popup_media_sha: str = ""


def match_encoding(profile: Profile) -> str:
    return profile.match_encoding or profile.encoding


# AE 大版本号 → 产品年份(Adobe 跳过了 19–21)。
MAJOR_YEAR = {"17": "2020", "18": "2021", "22": "2022", "23": "2023", "24": "2024", "25": "2025", "26": "2026"}


def year_of(version: str) -> str:
    major = str(version).split(".", 1)[0]
    return MAJOR_YEAR.get(major, "")


def default_encoding(version: str) -> str:
    """新适配构建的名称编码推断:26.x 起注册表名称存 UTF-8,更早的按系统代码页。
    与 4 个内置 profile 一致(25.3/18.2=mbcs,26.x=utf8)。registry_layout 的
    唯一匹配若与此矛盾会拒绝保存。"""
    return "utf8" if int(str(version).split(".", 1)[0] or 0) >= 26 else "mbcs"


PROFILES: dict[str, Profile] = {
    "26.5.0.89": Profile(
        "26.5.0.89",
        "9a1afe7700229cd0bc39dd37a146b1d3871fc84a8e6f991a7ef56390f21378d3",
        0x12D998, 0x20, 0xBF0B0, 0x66750, 0x67790, 0x65AB0,
        tdb_sha="ef851c3ccbc43da4e0d4899a52baa05a7a71a3cef3a4fdb09abbb0263960ccb5",
        root_vtable=0xBF110, parent_get=0x38D30, stream_match_get=0x389F0,
        count_get=0x2AB70, count_bytes=bytes.fromhex("8b8128020000c3"),
        display_get=0x36FB0, match_pool=0x8DCD0, display_cache=0xF0, group_vector=0x220,
        named_map=0x220, codec_sha="1a465b53deac7a3d14187ed175ba85498dbf5b5463259dec1f202a3393050fd0",
        native_dynamic=True, named_tree=True, enabled=True,
    ),
    "26.0.0.67": Profile(
        "26.0.0.67",
        "68b229950e95dedf630f5d09653715268e8beba4830590433abe7cb58e78a357",
        0x14DEE0, 0x20, 0xC8E20, 0x68850, 0x69920, 0x67BB0,
        tdb_sha="8e1ea3d5e08d91dd25e899d43e0ad69a525b161716bb4c29250e3711b2591c80",
        root_vtable=0xC8E80, parent_get=0x3AB80, stream_match_get=0x3A840,
        count_get=0x2C390, count_bytes=bytes.fromhex("8b8130020000c3"),
        display_get=0x38D30, match_pool=0xA8B60, display_cache=0xF0, group_vector=0x220,
        enabled=True,
    ),
    "25.3.0.71": Profile(
        "25.3.0.71",
        "a4896d34e20f1fb08ba9afd0fc6e428081bd51dba6abb39e87aa6342c973a183",
        0x153938, 0x20, 0xCE290, 0x6B090, 0x6C160, 0x6A3F0,
        tdb_sha="6d8f80729ef02538415bb6578399cee667101b6c218ea73d85d74357558b660e",
        root_vtable=0xCE2F0, parent_get=0x39CF0, stream_match_get=0x399B0,
        count_get=0x2B3B0, count_bytes=bytes.fromhex("8b8130020000c3"),
        display_get=0x37E60, match_pool=0xA6A00, display_cache=0xF0, group_vector=0x220,
        encoding="mbcs", enabled=True,
    ),
    "18.2.1.8": Profile(
        "18.2.1.8",
        "abf6673b380450cc98549876c3738135bc025345b619cc4cba9d17bf98d05bc9",
        0xDDCC8, 0x10, 0x8E9E8, 0x34E90, 0x522E0, 0x51D00,
        tdb_sha="2214da04c37c197f8cefb3cc825eaab6f45efa3c1bdd300e2157842e4b38113d",
        root_vtable=0x8F0F0, parent_get=0x30DA0, stream_match_get=0x275C0,
        count_get=0x253A0, count_bytes=bytes.fromhex("488b8198010000482b8190010000"),
        display_get=0x2F590, match_pool=0x718A0, display_cache=0xE8, group_vector=0x190,
        named_vector=True, encoding="mbcs", enabled=True,
    ),
    # ---- 2026-10-30 实测补: AE 2021 (18.2.0.37, 官方早期构建) ----
    #   FLT/TDB 与 18.2.1.8 完全不同, 不能借用它的地址; 这一条是 2026-10-30 在真机
    #   上走 adopt_candidate 流程(静态候选 -> AE 自报目录 1346 项交叉校验 -> 读回改名)
    #   验过才写进来的。
    "18.2.0.37": Profile(
        "18.2.0.37",
        "dc92cf02585508fb579916643adc607d0f963ecffd1aad04ba629ee96198ffda",
        0xDDCC8, 0x10, 0x8E9D8, 0x34DF0, 0x52240, 0x51C60,
        host_version="18.2x37",
        tdb_sha="efe0987287909c409e860eaa67042c0f91bda4e8627406cf35329707dde14bb3",
        root_vtable=0x0, parent_get=0x30A10, stream_match_get=0x271C0,
        count_get=0x24EC0, count_bytes=bytes.fromhex("488b8198010000482b8190010000"),
        display_get=0x2F200, match_pool=0x0, display_cache=0x0,
        group_vector=0x190, named_vector=True, named_map=0x198,
        encoding="mbcs", enabled=True,
    ),
    # ---- 2026-10-30 实测补: AE 2022 / AE 2023 两个构建(本机真机跑通参数通道) ----
    "22.6.0.64": Profile(
        "22.6.0.64",
        "4ef3515ced17639c582e2d304967c8df129508f9b1b9f78f337beef2033d904a",
        0xEB778, 0x10, 0x97898, 0x380B0, 0x57190, 0x56A10,
        tdb_sha="70eed84d8ddae45d550fd54358628d4f2bdff837a653d3fe6456d25abc076bf8",
        root_vtable=0x0, parent_get=0x334E0, stream_match_get=0xA690,
        count_get=0x26D20, count_bytes=bytes.fromhex("8b8190020000c3"),
        display_get=0x31A20, match_pool=0x0, display_cache=0x0, group_vector=0x280,
        named_map=0x288, encoding="mbcs", enabled=True,
    ),
    "23.0.0.59": Profile(
        "23.0.0.59",
        "2a2370db244d70b223ced4bdb3e07ff43d2fdb57c02dc80aefa89baf08dd80eb",
        0xEB778, 0x10, 0x97898, 0x380B0, 0x57190, 0x56A10,
        tdb_sha="dcd906778ebf8177a87b140a03e714b9dfe39efc72506cf3a9f4b6352e25f670",
        root_vtable=0x0, parent_get=0x33BA0, stream_match_get=0xA690,
        count_get=0x270A0, count_bytes=bytes.fromhex("8b8190020000c3"),
        display_get=0x320E0, match_pool=0x0, display_cache=0x0, group_vector=0x280,
        named_map=0x288, encoding="mbcs", enabled=True,
    ),
}


# ---------- 本机适配存储 ----------

def _profile_to_json(profile: Profile) -> dict:
    data = asdict(profile)
    data["count_bytes"] = data["count_bytes"].hex()
    return data


def _profile_from_json(data: dict) -> Profile:
    names = {f.name for f in dc_fields(Profile)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"适配条目含有未知字段:{'、'.join(sorted(unknown))}")
    values = dict(data)
    raw = values.get("count_bytes") or ""
    values["count_bytes"] = bytes.fromhex(raw) if raw else b""
    return Profile(**values)


def local_profiles_path(data_root) -> Path:
    return Path(data_root) / "adapter_profiles.json"


def load_local(data_root) -> dict:
    """本机适配条目 {version: Profile}。文件损坏时报错由调用方决定兜底。"""
    path = local_profiles_path(data_root)
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION or value.get("kind") != "aeloc-adapter-profiles":
        raise ValueError("本机适配记录格式不正确")
    entries = value.get("profiles")
    if not isinstance(entries, dict):
        raise ValueError("本机适配记录缺少 profiles")
    profiles = {str(key): _profile_from_json(item) for key, item in entries.items()}
    evidence = value.get("languageEvidence") or {}
    if isinstance(evidence, dict):
        for key, item in evidence.items():
            profile = profiles.get(key)
            if (profile and isinstance(item, dict) and item.get("fltSha") == profile.flt_sha
                    and isinstance(item.get("drawSha"), str)):
                media_sha = item.get("popupMediaSha", "")
                if isinstance(media_sha, str):
                    profiles[key] = replace(
                        profile, parameter_draw_sha=item["drawSha"],
                        popup_layout_verified=item.get("popupVerified") is True,
                        popup_media_sha=media_sha)
    return profiles


def save_local(data_root, profiles: dict, *, merge: bool = True) -> None:
    from .proc import write_json_atomic

    with _SAVE_LOCK:
        if merge:
            try:
                existing = load_local(data_root)
            except (ValueError, OSError):
                existing = {}
            existing.update(profiles)
            profiles = existing
        rows, evidence = {}, {}
        for key, profile in profiles.items():
            row = _profile_to_json(profile)
            draw_sha = row.pop("parameter_draw_sha")
            popup = row.pop("popup_layout_verified")
            media_sha = row.pop("popup_media_sha")
            rows[key] = row
            if draw_sha:
                evidence[key] = {"fltSha": profile.flt_sha, "drawSha": draw_sha,
                                 "popupVerified": popup}
                if media_sha:
                    evidence[key]["popupMediaSha"] = media_sha
        # Older releases can still read the profile rows after a rollback.
        payload = {"schemaVersion": SCHEMA_VERSION, "kind": "aeloc-adapter-profiles",
                   "profiles": rows, "languageEvidence": evidence}
        write_json_atomic(local_profiles_path(data_root), payload, ensure_ascii=False, indent=1)


def adopt_candidate(p, base: int, profile: Profile, host_catalog: list,
                    data_root=None) -> dict:
    """运行时交叉校验并启用本机候选(§8.6 收尾,2026-10-28 接线)。

    aescan 的静态候选(registry_vector=0、enabled=False)在这里补上运行时
    证据:catalog_probe 送回的 AE 自报目录 ↔ 内存穷举比对。通过即把
    registry_vector/编码写进 profile 并 save_local(enabled=True);失败抛
    CatalogMismatch,候选保持未启用。绝不借用其他构建的地址。
    """
    prefer = default_encoding(profile.version)
    layout = registry_layout(p, base, profile, host_catalog, prefer=prefer)
    adopted = replace(
        profile,
        registry_vector=layout["offset"],
        encoding=layout["encoding"],
        match_encoding=layout.get("matchEncoding") or "",
        enabled=True,
    )
    save_local(data_root, {adopted.version: adopted})
    return layout


def candidates(version: str, data_root=None) -> list:
    """同一版本号可能有内置条目 + 本机验证条目(打了补丁/精简版 AE 文件不同)。"""
    found = []
    if version in PROFILES:
        found.append(PROFILES[version])
    if data_root is not None:
        try:
            local = load_local(data_root)
        except (ValueError, OSError):
            local = {}
        if version in local:
            found.append(local[version])
    return found


def ambiguous(version: str, data_root=None) -> bool:
    return len(candidates(version, data_root)) > 1


def _completeness(profile: Profile) -> tuple:
    """Rank profiles that describe the same binary; more fields wins.

    Older built-in entries can lack layout facts (root_vtable/match_pool/
    display_cache) that a locally verified entry carries for the exact same
    FLT hash.  Picking the richer one is still byte-identity gated by the
    caller, so this never borrows another build's addresses.
    """
    return (
        1 if profile.enabled else 0,
        1 if profile.parameter_draw_sha else 0,
        1 if profile.root_vtable else 0,
        1 if profile.match_pool else 0,
        1 if profile.display_cache else 0,
        1 if profile.display_get else 0,
        1 if profile.registry_vector else 0,
    )


def get_profile(version: str, flt_sha: str = None, data_root=None) -> Profile:
    """取该版本的 profile;给了 flt_sha 就按它选,否则取第一个(哈希不符会在
    写入前拦下)。未适配的版本直接拒绝,绝不借用其他构建的地址。"""
    found = candidates(version, data_root)
    if not found:
        raise RuntimeError("此 AE 构建尚未适配实时写入:" + str(version))
    if flt_sha:
        matched = [p for p in found if p.flt_sha == flt_sha]
        if not matched:
            raise RuntimeError(mismatch_text(version))
        # Same binary, multiple descriptions: keep the most complete one.
        ranked = sorted(
            enumerate(matched),
            key=lambda item: (_completeness(item[1]), item[0]),
        )
        return ranked[-1][1]
    return found[0]


def is_supported(version: str, flt_sha: str = None, data_root=None) -> bool:
    return any(p.enabled and (flt_sha is None or p.flt_sha == flt_sha) for p in candidates(version, data_root))


def adapted_runtime_profile(process, modules, data_root):
    """Return adopted language evidence for the actual AE and loaded DLLs."""
    import os
    from .proc import file_version, module_digest

    by_name = {item["name"].casefold(): item["path"] for item in modules}
    flt = by_name.get("flt.dll")
    draw = by_name.get("afterfxlib.dll")
    if not flt or not draw:
        return None
    locations = {os.path.normcase(os.path.abspath(Path(draw).parent)),
                 os.path.normcase(os.path.abspath(Path(flt).parent)),
                 os.path.normcase(os.path.abspath(Path(process.image).parent))}
    if len(locations) != 1:
        return None
    try:
        profile = get_profile(file_version(process.image),
                              module_digest(process, flt), data_root)
    except (RuntimeError, OSError, ValueError):
        return None
    if (profile.enabled and profile.parameter_draw_sha
            and module_digest(process, draw) == profile.parameter_draw_sha):
        return profile
    return None


def runtime_allowed(version: str, data_root=None) -> bool:
    return is_supported(version, data_root=data_root)


def supported_text(data_root=None) -> str:
    parts = [p.version for p in PROFILES.values() if p.enabled]
    if data_root is not None:
        try:
            local = load_local(data_root)
        except (ValueError, OSError):
            local = {}
        for version, p in local.items():
            if p.enabled and (version not in PROFILES or p.flt_sha != PROFILES[version].flt_sha):
                parts.append(version + "(本机验证)")
    return "、".join(parts)


def mismatch_text(version: str) -> str:
    return (
        f"当前 AE 的效果模块(FLT.dll)与已验证的 {version} 不同,未执行写入。"
        "常见原因是 AE 文件被补丁、精简版或重新打包修改过"
        "(可右键 Support Files\\FLT.dll → 属性 → 数字签名,查看是否为有效的 Adobe Inc. 签名)。"
        "请先运行「自动识别与适配」,自检通过后即可在这台电脑上使用"
    )


def nameless(raw: bytes, encoding: str) -> bool:
    """内部名字段按 encoding 读不出内容(AE 2021 实测有 7 个效果脚本侧返回 '')
    时为真——这样的效果无法定位,永不改名、永不恢复。"""
    raw = raw.split(b"\0", 1)[0]
    if not raw:
        return True
    try:
        raw.decode(encoding)
        return False
    except UnicodeDecodeError:
        return True


# ---------- 运行时目录交叉校验(适配流程的第二步) ----------

READINGS = (("utf8", "utf8"), ("mbcs", "mbcs"), ("utf8", "mbcs"))
FIELD_LABELS = {"displayName": "显示名称", "matchName": "内部标识", "category": "分类"}
EXAMPLES = 20


def _readings(raw: bytes, encoding: str) -> set:
    """一个名称字段在 AE 脚本侧可能出现的样子:按 encoding 解码;解不动时
    (插件自带 GBK 名等)按 AE 的显示方式逐字节 latin-1/cp1252。字节永远精确比对。"""
    raw = raw.split(b"\0", 1)[0]
    try:
        return {raw.decode(encoding)}
    except UnicodeDecodeError:
        pass
    found = {raw.decode("latin-1")}
    try:
        found.add(raw.decode("cp1252"))
    except UnicodeDecodeError:
        pass
    return found


class CatalogMismatch(ValueError):
    """目录交叉校验失败。details 只含效果名与字段字节,不含地址。"""

    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def _host_entries(host_catalog):
    """AE 自报目录 → {matchName: (displayName, category)};重复注册的条目
    (插件装了两份)按一份对比,名称互相冲突的拒绝。"""
    expected = {}
    variants = {}
    times = {}
    unnamed = []
    for row in host_catalog:
        key = row.get("matchName") or ""
        value = (row.get("displayName") or "", row.get("category") or "")
        if not key:
            unnamed.append(value[0])
            continue
        times[key] = times.get(key, 0) + 1
        variants.setdefault(key, [])
        if value not in variants[key]:
            variants[key].append(value)
        expected.setdefault(key, value)
    duplicates = [
        {"matchName": k, "times": n, "names": [{"displayName": d, "category": c} for d, c in variants[k]]}
        for k, n in times.items()
        if n > 1
    ]
    conflicts = [d for d in duplicates if len(d["names"]) > 1]
    if conflicts:
        first = conflicts[0]
        shown = " / ".join(v["displayName"] for v in first["names"][:3])
        raise CatalogMismatch(
            f"AE 效果列表里有 {len(conflicts)} 个内部标识对应不同的名称(例如 {first['matchName']}:{shown}),"
            "可能同一插件的不同版本装了两份;请删掉多余的一份并重启 AE 后重试",
            {"hostCount": len(expected), "hostRows": len(host_catalog), "duplicates": duplicates[:EXAMPLES], "conflicts": len(conflicts), "unnamed": unnamed[:EXAMPLES]},
        )
    return expected, duplicates[:EXAMPLES], unnamed


def _differences(rows, expected, encoding, key_encoding=None):
    """内存目录与 AE 自报目录的差异数 + 前 EXAMPLES 个例子。"""
    count = 0
    examples = []
    seen = set()
    key_encoding = key_encoding or encoding

    def note(item):
        nonlocal count
        count += 1
        if len(examples) < EXAMPLES:
            examples.append(item)

    for name, key, category in rows:
        keys = [k for k in _readings(key, key_encoding) if k in expected]
        if len(keys) != 1 or keys[0] in seen:
            note({"field": "matchName", "memoryHex": key.split(b"\0", 1)[0].hex(), "memoryText": sorted(_readings(key, key_encoding))})
            continue
        seen.add(keys[0])
        shown, group = expected[keys[0]]
        for field, raw, want in (("displayName", name, shown), ("category", category, group)):
            if want not in _readings(raw, encoding):
                note({"matchName": keys[0], "field": field, "memoryHex": raw.split(b"\0", 1)[0].hex(), "memoryText": sorted(_readings(raw, encoding)), "host": want})
    for key in sorted(set(expected) - seen)[: max(0, EXAMPLES - len(examples))]:
        examples.append({"matchName": key, "field": "missing"})
    return count + len(set(expected) - seen), examples


def registry_layout(p, base: int, profile: Profile, host_catalog: list, prefer: str = "") -> dict:
    """只读的穷举交叉校验:在管理对象头部的小范围内找到唯一一个向量,其内容
    与 AE 自报目录完全一致。返回 {'offset', 'encoding', 'matchEncoding'}。
    找不到唯一证据时抛 CatalogMismatch,并给出最接近候选的差异说明。

    prefer:编码裁定(2026-10-28 接线用)。全英文目录时 utf8/mbcs 两种读法
    都可能零差异(§7 的歧义坑)—— 调用方按构建先验给出裁定(26.x=utf8、
    更早=mbcs,见 default_encoding),命中即采纳,不在证据里仍按歧义拒绝。"""
    manager = p.u64(base + profile.manager)
    expected, duplicates, unnamed = _host_entries(host_catalog)
    unnamed_rows = [(row.get("displayName") or "", row.get("category") or "") for row in host_catalog if not row.get("matchName")]
    if not 1 < len(expected) < 32768:
        raise CatalogMismatch(f"AE 报告的效果数量异常({len(expected)} 个),不能作为对照", {"hostCount": len(expected), "hostRows": len(host_catalog), "unnamed": unnamed[:EXAMPLES]})
    total = len(expected) + len(unnamed)
    matches = []
    vectors = []
    candidate_list = []
    key_encoding_of = {}

    def try_readings(pairs, pool):
        """一轮读法:同编码读法优先,全都没命中才轮到跨编码(AE 2021 族)。"""
        for offset in range(0, 129, 8):
            try:
                begin, end = struct.unpack("<QQ", p.read(manager + offset, 16))
            except (RuntimeError, OSError, struct.error):
                continue
            if begin < end and (end - begin) % 16 == 0 and (end - begin) // 16 < 32768:
                vectors.append({"offset": offset, "count": (end - begin) // 16})
            if end - begin != total * 16:
                continue
            candidate = next((c for c in candidate_list if c["offset"] == offset), None)
            if candidate is None:
                candidate = {"offset": offset}
                candidate_list.append(candidate)
            try:
                values = struct.unpack("<" + "Q" * (total * 2), p.read(begin, end - begin))
                rows = []
                keys = set()
                for index, address in enumerate(values[::2]):
                    raw = p.read(address, 160)
                    if struct.unpack_from("<Q", raw)[0] != base + profile.fc_vtable:
                        raise ValueError(f"第 {index + 1} 项对象类型不符")
                    fields = (raw[16:64], raw[64:96], raw[96:160])
                    for label, field in zip(("displayName", "matchName", "category"), fields):
                        if b"\0" not in field:
                            raise ValueError(f"第 {index + 1} 项{FIELD_LABELS[label]}没有结尾符({field.hex()})")
                    if fields[1].split(b"\0", 1)[0] and fields[1] in keys:
                        raise ValueError(f"第 {index + 1} 项内部标识重复({fields[1].split(bytes(1), 1)[0].hex()})")
                    keys.add(fields[1])
                    rows.append(fields)
            except (ValueError, RuntimeError, OSError, struct.error) as error:
                candidate["problem"] = str(error) or type(error).__name__
                continue
            for encoding, key_encoding in pairs:
                named = [r for r in rows if not nameless(r[1], key_encoding)]
                bare = [r for r in rows if nameless(r[1], key_encoding)]
                diff, examples = _differences(named, expected, encoding, key_encoding)
                left = list(unnamed_rows)
                for name, key, category in bare:
                    pair = next((u for u in left if u[0] in _readings(name, encoding) and u[1] in _readings(category, encoding)), None)
                    if pair:
                        left.remove(pair)
                        continue
                    diff += 1
                    if len(examples) < EXAMPLES:
                        examples.append({"field": "unnamed", "matchNameHex": key.split(b"\0", 1)[0].hex(), "memoryText": sorted(_readings(name, encoding))})
                diff += len(left)
                examples += [{"field": "unnamedMissing", "host": u[0]} for u in left][: max(0, EXAMPLES - len(examples))]
                label = encoding if encoding == key_encoding else f"{encoding}+{key_encoding}"
                candidate[label] = {"differences": diff, "examples": examples, "unnamedInMemory": len(bare)}
                if not diff:
                    matches.append(offset)
                    key_encoding_of.setdefault(offset, []).append((encoding, "" if key_encoding == encoding else key_encoding))
                    pool.append(offset)

    # 第一轮:名称与内部名同编码。全英文名时两种编码都可能命中 —— 记下并判歧义。
    same_offsets = []
    try_readings(READINGS[:2], same_offsets)
    if len(set(same_offsets)) == 1:
        offset = same_offsets[0]
        encodings = key_encoding_of[offset]
        if len(encodings) > 1:
            # ★ 2026-10-28:全英文目录的编码歧义裁定 —— 调用方按构建先验
            #   (default_encoding:26.x=utf8、更早=mbcs)给出 prefer,命中
            #   证据即采纳;没有 prefer 或不命中仍拒绝,绝不猜。
            picked = next((e for e in encodings if e[0] == prefer), None) if prefer else None
            if picked:
                return {"offset": offset, "encoding": picked[0],
                        "matchEncoding": picked[1]}
            raise CatalogMismatch(
                f"完整效果目录向量未找到唯一证据(同一向量在 {len(encodings)} 种编码下都与 AE 目录一致);"
                "目录名称全为英文时无法区分编码。可先安装一个名称非英文的第三方插件(如汉化版 BCC)后重试",
                {"hostCount": len(expected), "hostRows": len(host_catalog), "duplicates": duplicates, "vectors": vectors, "candidates": candidate_list},
            )
        encoding, key_enc = encodings[0]
        return {"offset": offset, "encoding": encoding, "matchEncoding": key_enc}
    if same_offsets:
        raise CatalogMismatch(f"完整效果目录向量未找到唯一证据({len(set(same_offsets))} 处都与 AE 目录一致)", {"hostCount": len(expected), "hostRows": len(host_catalog), "duplicates": duplicates, "vectors": vectors, "candidates": candidate_list})
    # 第二轮:跨编码读法(AE 2021:名称 UTF-8、内部名系统代码页)。
    cross_offsets = []
    try_readings(READINGS[2:], cross_offsets)
    if len(cross_offsets) == 1:
        offset = cross_offsets[0]
        encoding, key_enc = key_encoding_of[offset][0]
        return {"offset": offset, "encoding": encoding, "matchEncoding": key_enc}
    if cross_offsets:
        raise CatalogMismatch(f"完整效果目录向量未找到唯一证据({len(cross_offsets)} 处都与 AE 目录一致)", {"hostCount": len(expected), "hostRows": len(host_catalog), "duplicates": duplicates, "vectors": vectors, "candidates": candidate_list})
    details = {"hostCount": len(expected), "hostRows": len(host_catalog), "duplicates": duplicates, "unnamedCount": len(unnamed), "unnamed": unnamed[:EXAMPLES], "vectors": vectors, "candidates": candidate_list}
    twice = ""
    if duplicates:
        twice = f";AE 列表里有 {len(duplicates)} 个效果重复出现(例如 {duplicates[0]['matchName']}),可能同一插件装了两份,已按一份对比"
    raise CatalogMismatch("完整效果目录向量未找到唯一证据(" + _summary(details) + twice + ")", details)


def _summary(details) -> str:
    total = details["hostCount"] + details.get("unnamedCount", 0)
    candidate_list = details["candidates"]
    if not candidate_list:
        counts = "、".join(str(v["count"]) for v in details["vectors"][:6]) or "无"
        return f"AE 报告 {total} 个效果,内存里没有数量相同的目录;找到的目录数量:{counts}"
    readable = [c for c in candidate_list if "problem" not in c]
    if not readable:
        return f"AE 报告 {total} 个效果;数量相同的目录读取时{candidate_list[0]['problem']}"
    labels = [a if a == b else f"{a}+{b}" for a, b in READINGS]
    best = None
    for c in readable:
        for e in labels:
            if e in c:
                item = (c[e]["differences"], e, c)
                if best is None or item < best:
                    best = item
    if best is None:
        return f"AE 报告 {total} 个效果;目录候选无法按任何编码读取"
    count, encoding, candidate = best
    examples = candidate[encoding]["examples"]
    first = examples[0] if examples else {}
    where = f",例如 {first.get('matchName') or first.get('memoryHex', '?')} 的{FIELD_LABELS.get(first.get('field'), '条目')}" if first else ""
    return f"AE 报告 {total} 个效果,最接近的目录有 {count} 项不一致{where}"
