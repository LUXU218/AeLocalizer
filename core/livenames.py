"""效果名实时写入:直接改运行中 AE 里 FLT.dll 效果注册表的显示名字段。

不注入、不挂钩、不改插件文件、不需要管理员。每个构建的内存布局由
core/adapter 的 profile 定版(FLT.dll SHA-256 + 虚表 + getter 指令形态
三重校验),写入仅限 48 字节显示名字段,写前快照、写后回读、失败逆序回滚;
原始字节记录在会话日志(data_root/live-session.json),日志绑定进程
pid+创建时间,AE 重启后旧记录自动作废。
移植自 After-Effects-Plugin-Localizer(MIT,Copyright (c) 2026 verse),
按本项目约定改写。
"""
from __future__ import annotations

import ctypes as C
import json
import struct
import threading
from contextlib import contextmanager
from ctypes import wintypes as W
from pathlib import Path

from .adapter import get_profile, match_encoding, mismatch_text, nameless, runtime_allowed, supported_text
from .proc import Process, elevation_mismatch, file_version, list_ae_processes, main_window, module_digest, write_json_atomic

FIELD_SIZE = 48
_lock = threading.RLock()
_mutex_name = "Local\\AeLocalizer.NameSession"


@contextmanager
def _process_lock():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, W.DWORD]
    kernel.WaitForSingleObject.restype = W.DWORD
    kernel.ReleaseMutex.argtypes = [C.c_void_p]
    kernel.ReleaseMutex.restype = W.BOOL
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.CreateMutexW(None, False, _mutex_name)
    if not handle:
        raise RuntimeError("无法创建名称操作互斥量:" + str(C.WinError(C.get_last_error())))
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 258:
            raise RuntimeError("另一个 AeLocalizer 实例正在操作名称,请稍后重试")
        if result not in (0, 128):
            raise RuntimeError("无法获取名称操作互斥量:" + str(C.WinError(C.get_last_error())))
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def decode_name(raw: bytes, encoding: str) -> str:
    """名称字段(到 NUL)转文字。本构建编码读不动的字节(插件自带 GBK 名、
    中文内部名)按 AE 的显示方式逐字节 latin-1 读出,读取永不失败;
    写入与恢复永远用原始字节。"""
    text = raw.split(b"\0", 1)[0]
    try:
        return text.decode(encoding)
    except UnicodeDecodeError:
        return text.decode("latin-1")


def _name_bytes(value, encoding="utf8", strict=True) -> bytes:
    """会话日志里的名称字段(96 个十六进制字符)转字节。strict=True 要求
    按 encoding 也可读(本程序写入的名字);原始名字允许任何字节。"""
    try:
        if not isinstance(value, str) or len(value) != FIELD_SIZE * 2 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("名称字节必须是 96 个十六进制字符")
        raw = bytes.fromhex(value)
        if b"\0" not in raw:
            raise ValueError("名称字节缺少 NUL 终止符")
        if strict:
            raw.split(b"\0", 1)[0].decode(encoding)
        return raw
    except ValueError as error:
        raise RuntimeError("恢复记录损坏,已停止写入,原名称已保留:" + str(error)) from error


def encode_name(text: str, encoding="utf8") -> bytes:
    if not isinstance(text, str):
        raise ValueError("名称必须是文字")
    text = text.strip()
    if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("名称不能为空或包含控制字符")
    try:
        value = text.encode(encoding)
    except UnicodeEncodeError:
        raise ValueError(
            "此 AE 版本按系统编码保存名称,无法表示该名称(如 ™ ® • 等字符),请换用可表示的中文;若系统区域不是中文,"
            "请在 Windows「区域设置 → 管理语言设置 → 更改系统区域设置」选择中文(简体,中国)后重启再试"
        ) from None
    if len(value) > FIELD_SIZE - 1:
        raise ValueError(f"名称过长,请缩短(最多 {FIELD_SIZE - 1} 个英文字符或约 {FIELD_SIZE // 3} 个常见汉字)")
    return (value + b"\0").ljust(FIELD_SIZE, b"\0")


#: 译名收口标点（在标点处断句，读起来比硬截断自然）
_BREAK_CHARS = "，、；。,;"


def fit_name(text: str, encoding: str, max_bytes: int) -> str:
    """把放不下的译文收进 max_bytes 字节。

    显示名字段只有 48 字节（含终止 NUL），而词典里确实有描述性长句被当成
    效果名（例：BCC 2D Particles 的译文长达 77 字节）。原实现只能整条放弃，
    用户看到的就是「有的效果名还是英文」。这里按两步收口：

      1. 主干优先：取第一个中文标点前的部分（「BCC 平面粒子系统，可发射…」
         → 「BCC 平面粒子系统」），语义最完整；
      2. 退而求其次：从后往前找能放下的最长前缀，并切掉尾部标点。

    都放不下返回空串（调用方据此走「不可写」分支）。
    """
    text = (text or "").strip()
    if not text:
        return ""

    def blen(value):
        try:
            return len(value.encode(encoding))
        except UnicodeEncodeError:
            return None

    size = blen(text)
    if size is not None and size <= max_bytes:
        return text
    for index, char in enumerate(text):
        if char in _BREAK_CHARS and index >= 2:
            head = text[:index].rstrip()
            head_size = blen(head)
            if head_size is not None and 0 < head_size <= max_bytes:
                return head
            break
    for index in range(len(text) - 1, 1, -1):
        head = text[:index].rstrip()
        if head and head[-1] in _BREAK_CHARS:
            head = head[:-1].rstrip()
        head_size = blen(head)
        if head and head_size is not None and 0 < head_size <= max_bytes:
            return head
    return ""


def unencodable(text: str, encoding: str) -> list:
    found = []
    for char in text:
        try:
            char.encode(encoding)
        except UnicodeEncodeError:
            if char not in found:
                found.append(char)
    return found


def describe(process: dict) -> str:
    folder = Path(process.get("image") or "").parent.name or "AE"
    version = process.get("fileVersion") or "版本未知"
    return f"{folder}({version})"


def current_process(write: bool = False, expected_image=None) -> Process:
    """唯一运行中的 AE;多开、版本未适配、路径不符都拒绝。"""
    from .adapter import year_of

    processes = list_ae_processes()
    if not processes:
        raise RuntimeError("没有找到正在运行的 AE,请先启动所选 AE")
    if len(processes) != 1:
        raise RuntimeError("检测到多个 AE 进程:" + "、".join(describe(p) for p in processes) + "。请只保留一个 AE 进程")
    process = processes[0]
    version = process.get("fileVersion") or ""
    if not runtime_allowed(version, _data_root_of.get("value")):
        raise RuntimeError(
            f"检测到 {describe(process)},此构建尚未适配实时写入;已验证构建:{supported_text(_data_root_of.get('value'))}。"
            "可先运行「自动识别与适配」。扫描、翻译与写盘功能不受影响"
        )
    target = Process(process["pid"], write)
    if expected_image:
        try:
            if Path(target.image).resolve().casefold() != Path(expected_image).resolve().casefold():
                raise RuntimeError("正在运行的 AE 与所选路径不一致。请启动所选 AE:" + str(expected_image))
            if file_version(target.image) != version:
                raise RuntimeError("打开进程后 AE 版本发生变化,已停止操作")
        except BaseException:
            target.close()
            raise
    return target


# current_process 需要数据根做 profile 查询;backend 在使用前设置。
_data_root_of = {"value": None}


def set_data_root(data_root) -> None:
    _data_root_of["value"] = data_root


def _with_derived_root(profile, certain_path, digest):
    """Use the exact FLT effect-group vtable for a verified build.

    Legacy display-name calibration can persist its fallback stream vtable
    into root_vtable. Re-derive the effect-group type for both reference
    builds and exact, adopted local profiles.
    """
    try:
        import build_profile as bp  # type: ignore
    except ImportError:
        try:
            from runtime import build_profile as bp  # type: ignore
        except ImportError:
            return profile
    try:
        if (profile.enabled and profile.parameter_draw_sha
                and profile.flt_sha == digest):
            derived = bp.parade_vtable(certain_path, digest, allow_unknown=True)
        else:
            derived = bp.parade_vtable(certain_path, digest)
    except (bp.BuildMismatch, OSError, ValueError):
        return profile
    from dataclasses import replace
    return replace(profile, root_vtable=int(derived["vt_rva"]))


class Registry:
    """运行中 AE 的效果注册表视图。构造时完成全部布局校验,任一不符即拒绝。"""

    def __init__(self, p: Process, data_root=None, profile=None):
        self.p = p
        self.data_root = data_root if data_root is not None else _data_root_of.get("value")
        version = file_version(p.image)
        self.modules = p.modules()
        module = next((m for m in self.modules if m["name"].lower() == "flt.dll"), None)
        if not module:
            raise RuntimeError("AE 尚未加载效果模块,请等待启动完成")
        self.base = module["base"]
        self.path = module["path"]
        digest = module_digest(p, self.path)
        self.profile = profile or get_profile(version, digest, self.data_root)
        self.profile = _with_derived_root(self.profile, self.path, digest)
        # 本机适配的 native_dynamic 条目依赖 TDB/U 模块同时一致
        if self.profile.native_dynamic:
            for name, sha in (("tdb.dll", self.profile.tdb_sha), ("u.dll", self.profile.codec_sha)):
                if not sha:
                    continue
                other = next((m for m in self.modules if m["name"].casefold() == name), None)
                if not other or module_digest(p, other["path"]) != sha:
                    raise RuntimeError("本机适配的依赖模块发生变化,请重新验证")
        for rva, expected in (
            (self.profile.name_get, b"\x48\x8d\x41\x10\xc3"),
            (self.profile.match_get, b"\x48\x8d\x41\x40\xc3"),
            (self.profile.category_get, b"\x48\x8d\x41\x60\xc3"),
        ):
            if p.read(self.base + rva, len(expected)) != expected:
                raise RuntimeError("AE 名称字段接口校验失败,未执行写入")
        self.manager = p.u64(self.base + self.profile.manager)

    def records(self) -> list:
        begin, end = struct.unpack("<QQ", self.p.read(self.manager + self.profile.registry_vector, 16))
        if end < begin or (end - begin) % 16 or not 1 <= (end - begin) // 16 < 32768:
            raise RuntimeError("AE 效果目录结构不符,未执行写入")
        vector = self.p.read(begin, end - begin)
        addresses = [a for a in struct.unpack(f"<{len(vector) // 8}Q", vector)[::2] if a]
        rows = []
        # ★ 2026-11-01 性能:1400 条 × 160B 的逐条 RPM 收敛为一次批量读
        #   (aeloc_rpm.dll);旧路径(单条 read)作为回退保留,行为一致。
        #   测试的 fake 进程没有 read_many 属性 → 自动走回退。
        batches = None
        rm = getattr(self.p, "read_many", None)
        if rm is not None:
            try:
                batches = rm([(a, 160) for a in addresses])
            except Exception:
                batches = None      # 回退逐条;失败语义由单条 read 给出
        for i, address in enumerate(addresses):
            data = batches[i] if batches is not None else self.p.read(address, 160)
            rows.append(self._parse_record(address, data))
        key_encoding = match_encoding(self.profile)
        bare = [nameless(r["guard"][16:48], key_encoding) for r in rows]
        self.unnamed = [r["displayName"] for r, b in zip(rows, bare) if b]
        return [r for r, b in zip(rows, bare) if not b]

    def _parse_record(self, address: int, data: bytes) -> dict:
        if struct.unpack_from("<Q", data)[0] != self.base + self.profile.fc_vtable:
            raise RuntimeError("AE 效果目录对象类型不符,未执行写入")
        fields = []
        for start, end_field in ((16, 64), (64, 96), (96, 160)):
            raw = data[start:end_field]
            if b"\0" not in raw:
                raise RuntimeError("AE 效果名称字段未终止,未执行写入")
            fields.append(decode_name(raw, match_encoding(self.profile) if start == 64 else self.profile.encoding))
        return {
            "address": address,
            "displayName": fields[0],
            "matchName": fields[1],
            "category": fields[2],
            "guard": data[:16] + data[64:160],
        }

    def read_record(self, address: int) -> dict:
        return self._parse_record(address, self.p.read(address, 160))

    def unchanged(self, row) -> bool:
        now = self.read_record(row["address"])
        return now["matchName"] == row["matchName"] and now["guard"] == row["guard"]

    def target(self, key: str) -> dict:
        targets = [r for r in self.records() if r["matchName"] == key]
        if len(targets) != 1:
            raise RuntimeError("当前 AE 中未找到唯一的效果:" + key)
        return targets[0]


def read_live(data_root=None, expected_image=None) -> dict:
    """只读目录快照(自检/预览用),不写任何东西。"""
    set_data_root(data_root) if data_root is not None else None
    with (current_process(expected_image=expected_image) if expected_image else current_process()) as p:
        registry = Registry(p, data_root)
        records = registry.records()
        profile = registry.profile
        return {
            "pid": p.pid,
            "creationTime": p.creation_time,
            "image": p.image,
            "version": profile.version,
            "profileSha256": profile.flt_sha,
            "effects": [{k: v for k, v in r.items() if k not in ("address", "guard")} for r in records],
            "unnamedEffects": list(getattr(registry, "unnamed", [])),
        }


class NameSession:
    """一次写入会话:日志 + 受控写入 + 回滚 + 恢复。"""

    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self.journal_path = self.data_root / "live-session.json"
        self.expected_image = None

    def process(self) -> Process:
        set_data_root(self.data_root)
        return current_process(write=True, expected_image=self.expected_image) if self.expected_image else current_process(write=True)

    def journal(self) -> dict:
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf8"))
            if not isinstance(journal, dict):
                raise ValueError("顶层必须是对象")
            if type(journal.get("schemaVersion")) is not int or journal["schemaVersion"] != 1:
                raise ValueError("schemaVersion 必须是 1")
            if journal.get("encoding", "utf8") not in ("utf8", "mbcs"):
                raise ValueError("名称编码不受支持")
            if not isinstance(journal.get("entries"), dict):
                raise ValueError("entries 必须是对象")
            if journal["entries"] and (type(journal.get("pid")) is not int or type(journal.get("creationTime")) is not int):
                raise ValueError("非空记录的 pid 和 creationTime 必须是整数")
            for entry in journal["entries"].values():
                if not isinstance(entry, dict):
                    raise ValueError("名称条目必须是对象")
                if not isinstance(entry.get("originalName"), str) or not isinstance(entry.get("newName"), str):
                    raise ValueError("原始名称和新名称必须是文字")
                _name_bytes(entry.get("originalHex"), journal.get("encoding", "utf8"), strict=False)
                _name_bytes(entry.get("appliedHex"), journal.get("encoding", "utf8"))
            return journal
        except FileNotFoundError:
            return {"schemaVersion": 1, "entries": {}}
        except ValueError as error:
            raise RuntimeError("恢复记录损坏,已停止写入,原名称已保留:" + str(error)) from error

    @staticmethod
    def _identity(p: Process, journal: dict) -> dict:
        if journal.get("entries") and (journal.get("pid") != p.pid or journal.get("creationTime") != p.creation_time):
            # 旧记录属于已退出/不同的 AE 实例:绝不把它们的地址写进新进程。
            return {"schemaVersion": 1, "entries": {}}
        return journal

    def apply(self, translations: dict, skip_missing: bool = False) -> dict:
        """translations: {matchName: 中文名}。写入前逐条复核,写后整目录回读校验。"""
        if not translations:
            raise ValueError("没有可写入的效果译名")
        with _lock, _process_lock(), self.process() as p:
            main_window(p.pid)
            registry = Registry(p, self.data_root)
            records = registry.records()
            by_key = {r["matchName"]: r for r in records}
            profile = registry.profile
            encoding = profile.encoding
            encoded = {}
            unwritable = []
            # ★ 2026-11-06 修「打开保存过的工程只有部分效果名汉化」的第二条根因：
            #   48 字节的显示名字段装不下超长译文（词典里确有 325 条 > 47 字节，
            #   例如 BCC 2D Particles 的中文全称 81 字节）。原实现让 encode_name
            #   直接抛 ValueError，异常冒到 apply 外面 → **整批**一条都写不进，
            #   表现就是「效果名只汉化了寥寥几个」甚至全英文。超长和「编码不可
            #   表示」一样属于单条不可写，必须跳过该条、其余照写。
            shortened = {}
            for key, value in translations.items():
                text = value.strip()
                if encoding != "utf8" and unencodable(text, encoding):
                    unwritable.append((key, text, "encoding"))
                    continue
                fitted = fit_name(text, encoding, FIELD_SIZE - 1)
                if not fitted:
                    # 连最短前缀都放不下(极端:单个超长字节序列)才算不可写
                    unwritable.append((key, text, "toolong"))
                    continue
                if fitted != text:
                    # 超长译名按标点/前缀收口后照写,不再整条退回英文 ——
                    # 回退英文正是用户看到的「部分效果名没汉化」。
                    shortened[key] = fitted
                encoded[key] = encode_name(fitted, encoding)
            problem = None
            if unwritable:
                shown = ";".join(f"{key}:{value}" for key, value, _r in unwritable[:3])
                reasons = sorted({r for _k, _v, r in unwritable})
                why = ("名称过长(显示名字段只有 47 字节)"
                       if reasons == ["toolong"] else
                       "含有当前系统编码无法表示的字符或名称过长")
                problem = {"message": f"{len(unwritable)} 个译名{why},已跳过:{shown}",
                           "keys": [k for k, _v, _r in unwritable],
                           "toolong": [k for k, _v, r in unwritable if r == "toolong"]}
                if not encoded:
                    # ★ 全是不可写(超长/编码不可表示)不是「写入失败」:返回空结果让
                    #   调用方 toast 一次并记账,不要抛异常 —— 抛异常会被常驻补写
                    #   当成瞬时故障每 8 秒无限重试,日志被刷屏。
                    return {"applied": {}, "unchanged": [], "skipped": [],
                            "pid": p.pid, "unwritable": problem, "shortened": shortened}
            if len(by_key) != len(records):
                raise RuntimeError("效果内部标识不唯一,未执行写入")
            skipped = sorted(key for key in encoded if key not in by_key)
            if skipped and not skip_missing:
                raise ValueError("当前 AE 未加载此效果:" + skipped[0])
            for key in skipped:
                del encoded[key]
            if not encoded:
                return {"applied": {}, "unchanged": [], "skipped": skipped, "pid": p.pid, "unwritable": problem, "shortened": shortened}
            final = {key: encoded[key].split(b"\0", 1)[0].decode(encoding) for key in encoded}
            journal = self._identity(p, self.journal())
            previous = json.loads(json.dumps(journal))
            staged = []
            unchanged = []
            for key, value in encoded.items():
                row = by_key[key]
                before = p.read(row["address"] + 16, FIELD_SIZE)
                try:
                    _name_bytes(before.hex(), encoding, strict=False)
                except RuntimeError:
                    raise RuntimeError("AE 当前名称字段格式异常,未执行写入:" + key) from None
                if before == value:
                    unchanged.append(key)
                    continue
                entry = journal["entries"].get(key)
                if entry and before.hex() != entry["appliedHex"]:
                    raise RuntimeError("此效果名称已被其他程序修改,未覆盖:" + key)
                journal["entries"][key] = {
                    "originalHex": entry["originalHex"] if entry else before.hex(),
                    "appliedHex": value.hex(),
                    "originalName": entry["originalName"] if entry else row["displayName"],
                    "newName": final[key],
                }
                staged.append((row, before, value))
            applied = {key: final[key] for key in encoded}
            if not staged:
                return {"applied": applied, "unchanged": unchanged, "skipped": skipped, "pid": p.pid, "sharedNames": [], "unwritable": problem, "shortened": shortened}
            journal.update({"pid": p.pid, "creationTime": p.creation_time, "image": p.image, "profileSha256": profile.flt_sha, "encoding": encoding})
            write_json_atomic(self.journal_path, journal)
            attempted = []
            try:
                snapshot = {r["matchName"]: r["address"] for r in registry.records()}
                for row, before, value in staged:
                    if snapshot.get(row["matchName"]) != row["address"] or not registry.unchanged(row) or p.read(row["address"] + 16, FIELD_SIZE) != before:
                        raise RuntimeError("AE 目录在操作期间发生变化,已停止")
                    attempted.append((row, before))
                    p.write_buffer(row["address"] + 16, value)
                verified = registry.records()
                # 重名共享译文(BCC 转场双份等)是允许的;只要求落到目标名。
                got = {r["matchName"]: r["displayName"] for r in verified}
                for key, want in final.items():
                    if got.get(key) != want:
                        raise RuntimeError("更名后目录校验不一致:" + key)
                return {"applied": applied, "unchanged": unchanged, "skipped": skipped, "pid": p.pid, "sharedNames": [], "unwritable": problem, "shortened": shortened}
            except Exception as error:
                failed = []
                for row, before in reversed(attempted):
                    try:
                        actual = registry.target(row["matchName"])
                        if actual["address"] != row["address"] or actual["guard"] != row["guard"]:
                            raise RuntimeError("回滚目标守卫不符")
                        p.write_buffer(row["address"] + 16, _name_bytes(before.hex(), encoding, strict=False))
                    except Exception:
                        failed.append(row["matchName"])
                if failed:
                    retained = json.loads(json.dumps(previous))
                    retained.update({k: v for k, v in journal.items() if k != "entries"})
                    for key in failed:
                        retained["entries"][key] = journal["entries"][key]
                    write_json_atomic(self.journal_path, retained)
                else:
                    write_json_atomic(self.journal_path, previous)
                state = "部分字段未能回滚,记录已保留" if failed else "已完全回滚"
                raise RuntimeError("应用名称失败," + state + ":" + str(error)) from error

    def restore(self, keys: list = None) -> dict:
        with _lock, _process_lock():
            journal = self.journal()
            wanted = list(journal["entries"]) if keys is None else [k for k in keys if k in journal["entries"]]
            if not wanted:
                return {"restored": {}}
            set_data_root(self.data_root)
            if not any(item.get("pid") == journal.get("pid") for item in list_ae_processes()):
                write_json_atomic(self.journal_path, {"schemaVersion": 1, "entries": {}})
                return {"restored": {}, "oldProcessEnded": True}
            with self.process() as p:
                if journal.get("pid") != p.pid or journal.get("creationTime") != p.creation_time:
                    write_json_atomic(self.journal_path, {"schemaVersion": 1, "entries": {}})
                    return {"restored": {}, "oldProcessEnded": True}
                main_window(p.pid)
                registry = Registry(p, self.data_root)
                restored = {}
                records = registry.records()
                rows = {r["matchName"]: r for r in records}
                if len(rows) != len(records):
                    raise RuntimeError("效果内部标识不唯一,未执行恢复")
                encoding = journal.get("encoding", "utf8")
                for key in wanted:
                    if key not in rows:
                        raise RuntimeError("当前 AE 中未找到唯一的效果:" + key)
                    entry = journal["entries"][key]
                    current = p.read(rows[key]["address"] + 16, FIELD_SIZE)
                    if current.hex() not in (entry["appliedHex"], entry["originalHex"]):
                        raise RuntimeError("效果名称已被外部修改,未强制恢复:" + key)
                try:
                    for key in wanted:
                        entry = journal["entries"][key]
                        row = rows[key]
                        if not registry.unchanged(row):
                            raise RuntimeError("AE 目录在操作期间发生变化,已停止:" + key)
                        p.write_buffer(row["address"] + 16, _name_bytes(entry["originalHex"], encoding, strict=False))
                        actual = registry.read_record(row["address"])
                        if (
                            actual["matchName"] != key
                            or actual["guard"] != row["guard"]
                            or actual["displayName"] != entry["originalName"]
                            or p.read(row["address"] + 16, FIELD_SIZE).hex() != entry["originalHex"].lower()
                        ):
                            raise RuntimeError("效果目录恢复回读不一致,记录已保留:" + key)
                        restored[key] = entry["originalName"]
                        del journal["entries"][key]
                finally:
                    if restored:
                        write_json_atomic(self.journal_path, journal)
                return {"restored": restored}


def elevation_hint(pid: int) -> str:
    return elevation_mismatch(pid)


def mismatch_hint(version: str, data_root=None) -> str:
    return mismatch_text(version)
