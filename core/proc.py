"""AE 进程只读/受控写入的 ctypes 封装。

写入被限制在已验证的名称数据字段上(≤48 字节),不做任何代码补丁。
读写封装与身份校验移植自 After-Effects-Plugin-Localizer
(github.com/versereyh-commits,MIT License,Copyright (c) 2026 verse),
按本项目约定改写。
"""
from __future__ import annotations

import ctypes as C
import hashlib
import os
import struct
import sys
import threading
from ctypes import wintypes as W
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# write: QUERY_INFORMATION | VM_OPERATION | VM_READ | VM_WRITE
_RIGHTS_WRITE = 0x0400 | 0x0008 | 0x0010 | 0x0020
_RIGHTS_READ = 0x0400 | 0x0010

_lock = threading.Lock()
_digests: dict = {}


def elevated(pid: int) -> bool | None:
    """进程是否以管理员身份运行;查不到(已退出/无权限)返回 None。
    只要 QUERY_LIMITED 权限,跨提权层级都能问。"""
    k = C.WinDLL("kernel32", use_last_error=True)
    a = C.WinDLL("advapi32", use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = C.c_void_p
    k.CloseHandle.argtypes = [C.c_void_p]
    a.OpenProcessToken.argtypes = [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]
    a.OpenProcessToken.restype = W.BOOL
    a.GetTokenInformation.argtypes = [C.c_void_p, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    a.GetTokenInformation.restype = W.BOOL
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    token = C.c_void_p()
    try:
        if not a.OpenProcessToken(handle, 0x8, C.byref(token)):  # TOKEN_QUERY
            return None
        try:
            value = W.DWORD()
            size = W.DWORD()
            if not a.GetTokenInformation(token, 20, C.byref(value), C.sizeof(value), C.byref(size)):
                return None  # TokenElevation
            return bool(value.value)
        finally:
            k.CloseHandle(token)
    finally:
        k.CloseHandle(handle)


def elevation_mismatch(pid: int) -> str:
    """AE 与本程序提权层级不同时的说明;相同或查不到返回 ''。"""
    host, own = elevated(pid), elevated(os.getpid())
    if host is None or own is None or host == own:
        return ""
    side = lambda value: "以管理员身份运行" if value else "普通权限"  # noqa: E731
    return (
        f"AE 与本程序的管理员权限不同(AE:{side(host)},本程序:{side(own)}),"
        "Windows 可能拦截操作。请让两者用相同权限运行(建议都不要「以管理员身份运行」)后重试"
    )


class MBI(C.Structure):
    _fields_ = [
        ("BaseAddress", C.c_void_p),
        ("AllocationBase", C.c_void_p),
        ("AllocationProtect", W.DWORD),
        ("PartitionId", W.WORD),
        ("RegionSize", C.c_size_t),
        ("State", W.DWORD),
        ("Protect", W.DWORD),
        ("Type", W.DWORD),
    ]


def file_version(path) -> str:
    """读取 exe/dll 的 VS_VERSIONINFO 文件版本,形如 '25.5.0.66'。"""
    v = C.WinDLL("version", use_last_error=True)
    path = str(path)
    size = v.GetFileVersionInfoSizeW(path, None)
    if not size:
        raise RuntimeError("无法读取文件版本:" + path)
    data = C.create_string_buffer(size)
    if not v.GetFileVersionInfoW(path, 0, size, data):
        raise RuntimeError("无法读取文件版本:" + path)
    value = C.c_void_p()
    length = C.c_uint()
    if not v.VerQueryValueW(data, "\\", C.byref(value), C.byref(length)) or not value.value:
        raise RuntimeError("文件版本信息为空:" + path)
    fixed = C.cast(value, C.POINTER(C.c_uint32))
    ms, ls = fixed[2], fixed[3]
    return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}"


_RPM_LIB = None
_RPM_TRIED = False


def _rpm_lib():
    """aeloc_rpm.dll(批量跨进程读,2026-11-01);缺失/加载失败返回 None。

    查找顺序:打包资源(_MEIPASS)→ 仓库 native/ → exe 同目录。只试一次,
    结果缓存(进程内 DLL 状态不会变)。"""
    global _RPM_LIB, _RPM_TRIED
    if _RPM_TRIED:
        return _RPM_LIB
    _RPM_TRIED = True
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "aeloc_rpm.dll"))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands.append(os.path.join(root, "native", "aeloc_rpm.dll"))
    try:
        cands.append(os.path.join(os.path.dirname(sys.executable), "aeloc_rpm.dll"))
    except Exception:
        pass
    for p in cands:
        if os.path.exists(p):
            try:
                lib = C.CDLL(p)
                lib.aeloc_rpm_many.argtypes = [
                    C.c_void_p, C.POINTER(C.c_uint64), C.POINTER(C.c_ulong),
                    C.c_int, C.c_void_p, C.POINTER(C.c_ubyte)]
                lib.aeloc_rpm_many.restype = C.c_int
                _RPM_LIB = lib
                break
            except OSError:
                continue
    return _RPM_LIB


class Process:
    """运行中的 AfterFX.exe。构造时校验进程身份并记录创建时间,
    会话日志据此防止把旧地址写进新进程。"""

    def __init__(self, pid: int, write: bool = False):
        self.pid = pid
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = C.c_void_p
        self.k.CloseHandle.argtypes = [C.c_void_p]
        self.k.ReadProcessMemory.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.k.WriteProcessMemory.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.WriteProcessMemory.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [C.c_void_p, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.GetProcessTimes.argtypes = [C.c_void_p] + [C.POINTER(W.FILETIME)] * 4
        self.k.VirtualQueryEx.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(MBI), C.c_size_t]
        self.k.VirtualQueryEx.restype = C.c_size_t
        self.handle = self.k.OpenProcess(_RIGHTS_WRITE if write else _RIGHTS_READ, False, pid)
        if not self.handle:
            code = C.get_last_error()
            hint = ""
            if code == 5:  # ERROR_ACCESS_DENIED
                hint = ("(AE 可能以管理员身份运行;本工具与 AE 必须同级权限 —— "
                        "请用相同权限重新启动本工具)")
            raise RuntimeError(f"无法打开 AE 进程(pid {pid}):{C.WinError(code)}{hint}")
        try:
            name = C.create_unicode_buffer(32768)
            size = W.DWORD(len(name))
            if not self.k.QueryFullProcessImageNameW(self.handle, 0, name, C.byref(size)):
                raise C.WinError(C.get_last_error())
            self.image = name.value
            if Path(self.image).name.lower() != "afterfx.exe":
                raise ValueError("目标不是 AE 进程")
            created, ended, kernel, user = (W.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(self.handle, C.byref(created), C.byref(ended), C.byref(kernel), C.byref(user)):
                raise C.WinError(C.get_last_error())
            self.creation_time = (created.dwHighDateTime << 32) | created.dwLowDateTime
        except BaseException:
            self.close()
            raise

    def close(self):
        if getattr(self, "handle", None):
            self.k.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ensure_alive(self):
        if not self.handle:
            raise RuntimeError("AE 进程句柄已关闭")
        if not hasattr(self.k.GetExitCodeProcess, "argtypes"):
            self.k.GetExitCodeProcess.argtypes = [C.c_void_p, C.POINTER(W.DWORD)]
            self.k.GetExitCodeProcess.restype = W.BOOL
        code = W.DWORD()
        if not self.k.GetExitCodeProcess(self.handle, C.byref(code)) or code.value != 259:
            raise RuntimeError("AE 进程已退出或无法确认状态,停止操作")

    def read(self, address: int, size: int) -> bytes:
        # ★ 2026-10-28 实测:布局不符时树遍历会算出垃圾地址,ctypes 直接抛
        #   "cannot be converted to pointer"(信息量为零)。这里先拦,
        #   给出可诊断的错误 —— 上层据此判定「布局不符」而不是崩溃。
        if not (0 < address < 0x7FFFFFFEFFFF):
            raise RuntimeError("读到了非法地址 0x%X(内存布局不符,已停止)" % address)
        data = C.create_string_buffer(size)
        got = C.c_size_t()
        ok = self.k.ReadProcessMemory(self.handle, C.c_void_p(address), data, size, C.byref(got))
        if not ok or got.value != size:
            raise RuntimeError("AE 内存读取失败;进程可能已退出或正在切换状态")
        return data.raw

    def read_many(self, items):
        """批量跨进程读(2026-11-01 性能):一次 DLL 调用读 N 段。

        items: [(address, size), ...] → 返回 [bytes, ...] 与输入一一对应。
        任一段失败抛 RuntimeError(与单条 read 语义一致)。
        aeloc_rpm.dll 缺失/加载失败时自动回退逐条 read —— 行为完全一致,
        只是没有批量的速度收益。"""
        if not items:
            return []
        lib = _rpm_lib()
        if lib is None:
            return [self.read(a, s) for a, s in items]
        n = len(items)
        addrs = (C.c_uint64 * n)(*(int(a) for a, _ in items))
        lens = (C.c_ulong * n)(*(int(s) for _, s in items))
        total = sum(int(s) for _, s in items)
        out = C.create_string_buffer(total) if total else None
        ok = (C.c_ubyte * n)()
        good = lib.aeloc_rpm_many(C.c_void_p(self.handle), addrs, lens,
                                  C.c_int(n), out, ok)
        if good < 0:
            raise RuntimeError("批量读取调用失败(参数非法)")
        if good != n:
            for i, okv in enumerate(ok):
                if not okv:
                    a, s = items[i]
                    raise RuntimeError(
                        "AE 内存读取失败(addr=0x%X, %d 字节);进程可能已退出或正在切换状态"
                        % (a, s))
        res, off = [], 0
        for _, s in items:
            s = int(s)
            res.append(out.raw[off:off + s] if s else b"")
            off += s
        return res

    def read_into(self, address: int, buffer, size: int | None = None) -> int:
        """读进调用方持有的可写缓冲,不复制内容(全内存扫描用)。"""
        view = memoryview(buffer)
        if view.readonly or not view.c_contiguous:
            raise ValueError("读取缓冲区必须连续且可写")
        count = view.nbytes if size is None else size
        if type(count) is not int or not 0 < count <= view.nbytes:
            raise ValueError("读取长度超出缓冲区")
        if not self.handle:
            raise RuntimeError("AE 进程句柄已关闭")
        data = (C.c_ubyte * view.nbytes).from_buffer(buffer)
        got = C.c_size_t()
        ok = self.k.ReadProcessMemory(self.handle, C.c_void_p(address), data, count, C.byref(got))
        if not ok or got.value != count:
            raise RuntimeError("AE 内存读取失败;进程可能已退出或正在切换状态")
        return got.value

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def write_buffer(self, address: int, data: bytes):
        """受控写入:仅允许名称字段长度(≤48 字节),写后立即回读校验。"""
        if not 1 <= len(data) <= 48:
            raise ValueError("拒绝超出名称字段范围的写入")
        got = C.c_size_t()
        buffer = C.create_string_buffer(data, len(data))
        ok = self.k.WriteProcessMemory(self.handle, C.c_void_p(address), buffer, len(data), C.byref(got))
        if not ok or got.value != len(data):
            raise RuntimeError("名称写入失败:" + str(C.WinError(C.get_last_error())))
        if self.read(address, len(data)) != data:
            raise RuntimeError("名称写入后回读不一致")

    def write_exact(self, address: int, data: bytes):
        """受控原地写：只允许落在同一 committed 可写页内，写后回读。"""
        if not data or len(data) > (1 << 20):
            raise ValueError("empty/oversized write")
        m = virtual_query(self, int(address))
        if m is None or m.State != 0x1000 or m.Type != 0x20000:
            raise RuntimeError("目标不是 MEM_PRIVATE committed 页")
        if not (m.Protect & 0xCC) or (m.Protect & 0x100):
            raise RuntimeError("目标不可写或是 PAGE_GUARD")
        if int(address) + len(data) > int(m.BaseAddress) + int(m.RegionSize):
            raise RuntimeError("写入跨页；拒绝分配/越界")
        got = C.c_size_t()
        buf = C.create_string_buffer(data, len(data))
        ok = self.k.WriteProcessMemory(self.handle, C.c_void_p(address),
                                       buf, len(data), C.byref(got))
        if not ok or got.value != len(data):
            raise RuntimeError("写入失败:" + str(C.WinError(C.get_last_error())))
        if self.read(address, len(data)) != data:
            raise RuntimeError("写入后回读不一致")

    def modules(self) -> list:
        """已加载模块列表 [{base, path, name}],一次枚举全进程共享。"""
        self.k.K32EnumProcessModulesEx.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), W.DWORD]
        self.k.K32GetModuleFileNameExW.argtypes = [C.c_void_p, C.c_void_p, W.LPWSTR, W.DWORD]
        array = (C.c_void_p * 4096)()
        needed = W.DWORD()
        if not self.k.K32EnumProcessModulesEx(self.handle, array, C.sizeof(array), C.byref(needed), 2):
            raise C.WinError(C.get_last_error())  # LIST_MODULES_32BIT? 2 = 16|32 all
        result = []
        for base in array[: min(needed.value // 8, 4096)]:
            name = C.create_unicode_buffer(32768)
            if self.k.K32GetModuleFileNameExW(self.handle, base, name, len(name)):
                result.append({"base": base, "path": name.value, "name": Path(name.value).name})
        return result


def virtual_query(p: "Process", address: int):
    """地址所在内存区域信息(MBI);越界/失败返回 None。"""
    m = MBI()
    if not p.k.VirtualQueryEx(p.handle, C.c_void_p(address), C.byref(m), C.sizeof(m)):
        return None
    return m


def module_digest(p: Process, path) -> str:
    """模块文件的 SHA-256,按 (进程, 文件身份) 缓存——路径/大小/时间戳/文件 ID
    任一变化或 AE 重启都会重新计算。"""
    st = os.stat(path)
    key = (
        getattr(p, "pid", None),
        getattr(p, "creation_time", None),
        os.path.normcase(os.path.abspath(str(path))),
        st.st_size,
        st.st_mtime_ns,
        st.st_ino,
    )
    with _lock:
        digest = _digests.get(key)
    if digest is None:
        with Path(path).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with _lock:
            _digests[key] = digest
    return digest


STILL_ACTIVE = 259


def process_alive(pid: int) -> bool:
    """进程是否真的还在跑(僵尸进程返回 False)。

    ★ 2026-10-30 实测(守护被死进程卡死的根因):
      已经退出的 AE 会留下"僵尸"进程对象 —— EnumProcesses 仍然列得出、
      OpenProcess 仍然成功、QueryFullProcessImageNameW 仍然拿得到路径,
      只有 GetExitCodeProcess != STILL_ACTIVE(259) 能把它们和活着的 AE
      区分开。守护/启动器把它当成"正在运行的 AE", 就会反复 attach 已经
      退出的 pid, 用户点「启动 AE」也会被误判成"已经在跑"而毫无反应。
    """
    k = C.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = C.c_void_p
    k.GetExitCodeProcess.argtypes = [C.c_void_p, C.POINTER(W.DWORD)]
    k.CloseHandle.argtypes = [C.c_void_p]
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = W.DWORD(0)
        if not k.GetExitCodeProcess(handle, C.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


def list_ae_processes() -> list:
    """正在运行的 AE 进程 [{pid, image, creation_time, fileVersion}],按 pid 升序。"""
    k = C.WinDLL("kernel32", use_last_error=True)
    # ★ EnumProcesses 在 psapi.dll;kernel32 只导出 K32EnumProcesses(Win7+ 别名),
    #   直接 k.EnumProcesses 会 AttributeError(2026-10-28 启动日志实锤)。
    _psapi = C.WinDLL("psapi", use_last_error=True)
    try:
        enum_processes = _psapi.EnumProcesses
    except AttributeError:
        enum_processes = k.K32EnumProcesses
    enum_processes.argtypes = [C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    enum_processes.restype = W.BOOL
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = C.c_void_p
    k.QueryFullProcessImageNameW.argtypes = [C.c_void_p, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    k.QueryFullProcessImageNameW.restype = W.BOOL
    k.GetProcessTimes.argtypes = [C.c_void_p] + [C.POINTER(W.FILETIME)] * 4
    k.CloseHandle.argtypes = [C.c_void_p]
    size = 4096
    while True:
        array = (W.DWORD * size)()
        needed = W.DWORD()
        if not enum_processes(array, C.sizeof(array), C.byref(needed)):
            raise C.WinError(C.get_last_error())
        if needed.value < C.sizeof(array):
            break
        size *= 2
    result = []
    for pid in array[: needed.value // C.sizeof(W.DWORD)]:
        if not pid:
            continue
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            name = C.create_unicode_buffer(32768)
            n = W.DWORD(len(name))
            if not k.QueryFullProcessImageNameW(handle, 0, name, C.byref(n)):
                continue
            if Path(name.value).name.lower() != "afterfx.exe":
                continue
            if not process_alive(pid):
                continue      # 僵尸(已退出)绝不当成"正在运行的 AE"
            created, ended, kernel, user = (W.FILETIME() for _ in range(4))
            creation = 0
            if k.GetProcessTimes(handle, C.byref(created), C.byref(ended), C.byref(kernel), C.byref(user)):
                creation = (created.dwHighDateTime << 32) | created.dwLowDateTime
            try:
                version = file_version(name.value)
            except RuntimeError:
                version = ""
            result.append({"pid": pid, "image": name.value, "creation_time": creation, "fileVersion": version})
        finally:
            k.CloseHandle(handle)
    result.sort(key=lambda item: item["pid"])
    return result


def _ae_windows(pids) -> list:
    u = C.WinDLL("user32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    result = []
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback
    def visit(hwnd, arg):
        owner = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, C.byref(owner))
        if owner.value in pids:
            name = C.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, name, len(name))
            if name.value.startswith("AE_CApplication_"):
                result.append((owner.value, hwnd))
        return True

    u.EnumWindows(visit, 0)
    return result


def list_ae_hosts() -> list:
    """Prefer AE editing windows over headless AfterFX processes."""
    processes = list_ae_processes()
    if len(processes) <= 1:
        return processes
    owners = {pid for pid, _ in _ae_windows({p["pid"] for p in processes})}
    return [p for p in processes if p["pid"] in owners] if owners else processes


def main_window(pid: int) -> int:
    """AE 主窗口句柄(类名 AE_CApplication_ 前缀)。必须是唯一且可用的,
    模态对话框开着时拒绝操作。"""
    result = _ae_windows({pid})
    if len(result) != 1:
        raise RuntimeError("未找到唯一的 AE 主窗口")
    hwnd = result[0][1]
    u = C.WinDLL("user32", use_last_error=True)
    u.IsWindowEnabled.argtypes = [W.HWND]
    u.IsWindowEnabled.restype = W.BOOL
    if not u.IsWindowEnabled(hwnd):
        raise RuntimeError("请先关闭 AE 中的对话框,再执行名称操作")
    return hwnd


def write_json_atomic(path, value, *, ensure_ascii: bool = False, indent=2):
    """同目录临时文件 + os.replace 的原子 JSON 写;崩溃/断电不会留下半个文件。"""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=ensure_ascii, indent=indent), encoding="utf8")
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
