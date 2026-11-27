"""Per-user Windows login entry and per-session desktop window ownership."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AeLocalizer"
HELPER_NAME = "AeLocalizerStartup.exe"
ERROR_ALREADY_EXISTS = 183


def scope_id(data_root=None):
    root = data_root or os.environ.get("AELOC_DATA_ROOT", "").strip()
    if not root:
        root = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AeLocalizer")
    normalized = os.path.normcase(os.path.realpath(root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _helper():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("请从完整的 Windows 便携包开启登录待机。")
    return Path(sys.executable).resolve().with_name(HELPER_NAME)


def _command(data_root):
    return subprocess.list2cmdline([str(_helper()), "--wait-for-ae", scope_id(data_root)])


def _is_previous_install(command, data_root):
    trailing = " --wait-for-ae " + scope_id(data_root)
    if not isinstance(command, str) or not command.endswith(trailing):
        return False
    executable = command[:-len(trailing)]
    if executable.startswith('"') and executable.endswith('"'):
        executable = executable[1:-1]
    path = Path(executable)
    return (path.is_absolute() and path.name.casefold() == HELPER_NAME.casefold()
            and command == subprocess.list2cmdline([executable, "--wait-for-ae", scope_id(data_root)]))


def _entry(registry, key):
    try:
        return registry.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return None


def is_enabled(data_root=None):
    try:
        import winreg
        desired = _command(data_root)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return _entry(winreg, key) == (desired, winreg.REG_SZ)
    except (ImportError, ValueError, OSError):
        return False


def set_enabled(enabled, data_root=None):
    desired = _command(data_root)
    import winreg

    if enabled:
        if not _helper().is_file():
            raise ValueError("缺少登录待机程序，请完整解压 Windows 便携包。")
        if len(desired.encode("utf-16-le")) > 520:
            raise ValueError("登录启动命令超过 Windows 的长度限制，请将软件移至较短路径。")
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                 winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    else:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                 winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        except FileNotFoundError:
            cancel_standby(data_root)
            return
    with key:
        entry = _entry(winreg, key)
        if entry is not None and entry != (desired, winreg.REG_SZ):
            if not (enabled and entry[1] == winreg.REG_SZ and _is_previous_install(entry[0], data_root)):
                raise ValueError("当前用户的同名登录项不属于这份配置，已保留原设置。")
        if enabled and entry != (desired, winreg.REG_SZ):
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, desired)
        elif not enabled and entry is not None:
            winreg.DeleteValue(key, RUN_VALUE)
    if not enabled:
        cancel_standby(data_root)


def _native(library, name, result, *args):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


def _kernel():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close(kernel, handle):
    return _native(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)(handle)


def cancel_standby(data_root=None):
    if sys.platform != "win32":
        return
    kernel = _kernel()
    open_event = _native(kernel, "OpenEventW", wintypes.HANDLE, wintypes.DWORD,
                         wintypes.BOOL, wintypes.LPCWSTR)
    handle = open_event(2, False, "Local\\AeLocalizer.Standby." + scope_id(data_root))
    if handle:
        try:
            _native(kernel, "SetEvent", wintypes.BOOL, wintypes.HANDLE)(handle)
        finally:
            _close(kernel, handle)


class _UiLease:
    def __init__(self, kernel=None, handle=None, marker=None):
        self.kernel = kernel
        self.handle = handle
        self.marker = marker
        self.hwnd = None

    def publish_window(self, hwnd):
        if self.handle:
            user = ctypes.WinDLL("user32", use_last_error=True)
            add = _native(user, "SetPropW", wintypes.BOOL, wintypes.HWND,
                          wintypes.LPCWSTR, wintypes.HANDLE)
            if not add(hwnd, self.marker, 1):
                return False
            if self.hwnd and self.hwnd != hwnd:
                _native(user, "RemovePropW", wintypes.HANDLE, wintypes.HWND,
                        wintypes.LPCWSTR)(self.hwnd, self.marker)
            self.hwnd = hwnd
            return True
        return sys.platform != "win32"

    def close(self):
        if self.hwnd:
            user = ctypes.WinDLL("user32", use_last_error=True)
            _native(user, "RemovePropW", wintypes.HANDLE, wintypes.HWND,
                    wintypes.LPCWSTR)(self.hwnd, self.marker)
            self.hwnd = None
        if self.handle:
            _close(self.kernel, self.handle)
            self.handle = None


def acquire_ui(data_root=None, *, automatic=False):
    """Keep the returned lease for the entire GUI lifetime; None means skip launch."""
    if sys.platform != "win32":
        return _UiLease()
    kernel = _kernel()
    create = _native(kernel, "CreateMutexW", wintypes.HANDLE, wintypes.LPVOID,
                     wintypes.BOOL, wintypes.LPCWSTR)
    marker = "AeLocalizer.UI." + scope_id(data_root)
    ctypes.set_last_error(0)
    handle = create(None, False, "Local\\" + marker)
    if not handle:
        return None if automatic else _UiLease()
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _close(kernel, handle)
        return None
    return _UiLease(kernel, handle, marker)


def activate_ui(data_root=None):
    if sys.platform != "win32":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    prop = _native(user, "GetPropW", wintypes.HANDLE, wintypes.HWND, wintypes.LPCWSTR)
    marker = "AeLocalizer.UI." + scope_id(data_root)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = None

    @callback_type
    def find(hwnd, param):
        nonlocal found
        if prop(hwnd, marker):
            found = hwnd
            return False
        return True

    _native(user, "EnumWindows", wintypes.BOOL, callback_type, wintypes.LPARAM)(find, 0)
    if not found:
        return False
    if _native(user, "IsIconic", wintypes.BOOL, wintypes.HWND)(found):
        _native(user, "ShowWindowAsync", wintypes.BOOL, wintypes.HWND, ctypes.c_int)(found, 9)
    return bool(_native(user, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)(found))
