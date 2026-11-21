"""刷新 AE 已打开的「效果和预设」面板:纯窗口消息,不模拟硬件输入。

右键消息投给面板标签/搜索头打开面板自带菜单,MN_GETHMENU 取菜单句柄,
核对「刷新列表」项后发 VK_RETURN 确认。真光标不动、不抢前台。
面板没开时经主窗口「窗口 → 效果和预设」的 WM_COMMAND 打开再刷新。
移植自 After-Effects-Plugin-Localizer 的 palette_messages(MIT,
Copyright (c) 2026 verse),v1 走原生窗口锚点(不依赖 comtypes/UIA)。
"""
from __future__ import annotations

import ctypes as C
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes as W
from pathlib import Path

_lock = threading.Lock()

REFRESH_ITEMS = ("刷新列表", "Refresh List")


def _user32():
    u = C.WinDLL("user32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    u.IsWindowVisible.argtypes = [W.HWND]
    u.IsWindowVisible.restype = W.BOOL
    u.IsWindow.argtypes = [W.HWND]
    u.IsWindow.restype = W.BOOL
    u.GetParent.argtypes = [W.HWND]
    u.GetParent.restype = W.HWND
    u.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, C.POINTER(C.c_size_t)]
    u.SendMessageTimeoutW.restype = C.c_ssize_t
    u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.GetMenuItemCount.argtypes = [W.HMENU]
    u.GetMenuItemCount.restype = C.c_int
    u.GetMenuItemID.argtypes = [W.HMENU, C.c_int]
    u.GetMenuItemID.restype = W.UINT
    u.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, C.c_int, W.UINT]
    u.GetMenuState.argtypes = [W.HMENU, W.UINT, W.UINT]
    u.GetMenuState.restype = W.UINT
    u.GetMenu.argtypes = [W.HWND]
    u.GetMenu.restype = W.HMENU
    u.GetSubMenu.argtypes = [W.HMENU, C.c_int]
    u.GetSubMenu.restype = W.HMENU
    u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    u.ScreenToClient.argtypes = [W.HWND, C.POINTER(W.POINT)]
    u.ChildWindowFromPointEx.argtypes = [W.HWND, W.POINT, W.UINT]
    u.ChildWindowFromPointEx.restype = W.HWND
    return u


@contextmanager
def physical(u):
    """统一到 Per-Monitor Aware v2 的 DPI 上下文,保证坐标换算一致。"""
    fn = getattr(u, "SetThreadDpiAwarenessContext", None)
    if fn is None:
        yield
        return
    fn.argtypes = [C.c_void_p]
    fn.restype = C.c_void_p
    old = fn(C.c_void_p(-4))
    if not old:
        raise RuntimeError("无法统一 AE 面板的 DPI 坐标,未发送消息")
    try:
        yield
    finally:
        fn(old)


def dpi_for(u, hwnd) -> int:
    fn = getattr(u, "GetDpiForWindow", None)
    if fn is None:
        return 96
    fn.argtypes = [W.HWND]
    fn.restype = W.UINT
    return fn(hwnd) or 96


def _owner(u, hwnd) -> int:
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    return pid.value


def _native_windows(u, pid: int):
    """AE 的全部原生窗口行(rows)与顶层列表(tops)。"""
    rows = []
    tops = []
    seen = set()
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def record(hwnd):
        if hwnd in seen or len(rows) >= 4096 or _owner(u, hwnd) != pid:
            return
        seen.add(hwnd)
        name = C.create_unicode_buffer(256)
        title = C.create_unicode_buffer(256)
        rect = W.RECT()
        u.GetClassNameW(hwnd, name, len(name))
        u.GetWindowTextW(hwnd, title, len(title))
        u.GetWindowRect(hwnd, C.byref(rect))
        rows.append(
            {
                "hwnd": hwnd,
                "class": name.value,
                "title": title.value,
                "visible": bool(u.IsWindowVisible(hwnd)),
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
                "parent": u.GetParent(hwnd),
            }
        )

    @callback
    def top(hwnd, arg):
        if _owner(u, hwnd) == pid:
            tops.append(hwnd)
            record(hwnd)
        return True

    @callback
    def child(hwnd, arg):
        record(hwnd)
        return True

    u.EnumWindows(top, 0)
    for hwnd in list(tops):
        u.EnumChildWindows(hwnd, child, 0)
    return rows, tops


def _label(value: str) -> str:
    import re

    value = value.replace("&", "").split("\t")[0].strip().rstrip(".…").strip()
    return re.sub(r"\s*\([A-Za-z]\)$", "", value).casefold()


def _is_palette_title(value: str) -> bool:
    return _label(value).replace(" ", "").replace("_", "") in ("效果和预设", "effectspresets", "aeeffectspalette")


def clipped(row, by):
    rect = list(row["rect"])
    seen = set()
    current = row
    while current:
        if current["hwnd"] in seen or not current["visible"]:
            return None
        seen.add(current["hwnd"])
        r = current["rect"]
        rect = [max(rect[0], r[0]), max(rect[1], r[1]), min(rect[2], r[2]), min(rect[3], r[3])]
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            return None
        current = by.get(current.get("parent"))
    return tuple(rect)


def search_headers(rows, scale=1):
    """「效果和预设」搜索头的原生锚点:Edit 搜索框 → DroverLord - TabPanel
    面板体 → 同宽的 OS_ViewContainer 头部;点击点取头部右缘内 14px。"""
    by = {r["hwnd"]: r for r in rows}
    found = {}
    for edit in rows:
        e = edit["rect"]
        if edit.get("class") != "Edit" or not clipped(edit, by) or e[2] - e[0] < 80 * scale or not 6 * scale <= e[3] - e[1] <= 32 * scale:
            continue
        current = by.get(edit.get("parent"))
        seen = set()
        body = None
        while current and len(seen) < 12 and current["hwnd"] not in seen:
            seen.add(current["hwnd"])
            if current["title"] == "DroverLord - TabPanel Window":
                body = current
                break
            current = by.get(current.get("parent"))
        if body is None or not 0 <= e[1] - body["rect"][1] <= 40 * scale:
            continue
        current = by.get(body.get("parent"))
        seen = set()
        while current and len(seen) < 8 and current["hwnd"] not in seen:
            seen.add(current["hwnd"])
            r = current["rect"]
            b = body["rect"]
            height = b[1] - r[1]
            if current["title"] == "OS_ViewContainer" and 8 * scale <= height <= 48 * scale and abs(r[0] - b[0]) <= 8 * scale and abs(r[2] - b[2]) <= 8 * scale:
                visible = clipped(current, by)
                if visible:
                    header = (max(r[0], visible[0]), max(r[1], visible[1]), min(r[2], visible[2]), min(b[1], visible[3]))
                    if header[2] - header[0] >= 90 * scale and header[3] > header[1]:
                        point = (round(header[2] - 14 * scale), (header[1] + header[3]) // 2)
                        found[current["hwnd"]] = {"point": point, "target": current["hwnd"], "kind": "native-search-header", "button": True}
                break
            current = by.get(current.get("parent"))
    return sorted(found.values(), key=lambda x: (x["header"][1] if "header" in x else 0, x["point"][0]))[:8]


def _popups(u, pid: int):
    result = []
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback
    def visit(hwnd, arg):
        owner = W.DWORD()
        name = C.create_unicode_buffer(128)
        u.GetWindowThreadProcessId(hwnd, C.byref(owner))
        u.GetClassNameW(hwnd, name, len(name))
        if owner.value == pid and name.value == "#32768":
            result.append(hwnd)
        return True

    u.EnumWindows(visit, 0)
    return result


def _refresh_popup(u, pid: int):
    """当前打开的弹出菜单里「刷新列表」项:(菜单窗口, HMENU, 位置, command)。"""
    result = []
    stalled = []
    disabled = []
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback
    def visit(hwnd, arg):
        owner = W.DWORD()
        name = C.create_unicode_buffer(128)
        u.GetWindowThreadProcessId(hwnd, C.byref(owner))
        u.GetClassNameW(hwnd, name, len(name))
        if owner.value != pid or name.value != "#32768":
            return True
        menu = C.c_size_t()
        # 挂死的 AE 不能拖死本进程:限时查询,查不动就整体放弃
        if not u.SendMessageTimeoutW(hwnd, 0x1E1, 0, 0, 0x0003, 500, C.byref(menu)):
            stalled.append(hwnd)
            return True
        handle = menu.value
        count = u.GetMenuItemCount(handle)
        if not 1 <= count <= 128:
            return True
        for i in range(count):
            text = C.create_unicode_buffer(256)
            u.GetMenuStringW(handle, i, text, len(text), 0x400)
            if text.value.replace("&", "").strip() not in REFRESH_ITEMS:
                continue
            state = u.GetMenuState(handle, i, 0x400)
            command = u.GetMenuItemID(handle, i)
            if state == 0xFFFFFFFF or state & 3 or command in (0, 0xFFFFFFFF):
                disabled.append(hwnd)
                continue
            result.append((hwnd, handle, i, command))
        return True

    u.EnumWindows(visit, 0)
    if stalled:
        raise RuntimeError("AE 菜单无响应,已停止操作")
    if disabled:
        raise RuntimeError("AE 的刷新列表菜单不可用")
    if len(result) > 1:
        raise RuntimeError("检测到多个效果刷新菜单,未执行操作")
    return result[0] if result else None


def _panel_command(u, main: int) -> dict:
    """主窗口菜单「窗口 → 效果和预设」的 command id(面板未开时用它打开)。"""
    menu = u.GetMenu(main)
    matches = []
    for i in range(max(0, min(u.GetMenuItemCount(menu), 128))):
        text = C.create_unicode_buffer(256)
        u.GetMenuStringW(menu, i, text, len(text), 0x400)
        if _label(text.value) not in ("窗口", "window"):
            continue
        sub = u.GetSubMenu(menu, i)
        ignored = C.c_size_t()
        if not u.SendMessageTimeoutW(main, 0x117, sub, i, 0x0003, 250, C.byref(ignored)):
            raise RuntimeError("AE 窗口菜单无响应")
        for j in range(max(0, min(u.GetMenuItemCount(sub), 128))):
            u.GetMenuStringW(sub, j, text, len(text), 0x400)
            if not _is_palette_title(text.value):
                continue
            command = u.GetMenuItemID(sub, j)
            state = u.GetMenuState(sub, j, 0x400)
            if state == 0xFFFFFFFF or state & 3 or command in (0, 0xFFFFFFFF):
                raise RuntimeError(f"AE 效果和预设菜单不可用(command={command}, state={state})")
            matches.append({"command": command, "checked": bool(state & 8)})
    if len(matches) != 1:
        raise RuntimeError("无法唯一识别 AE「窗口」菜单中的「效果和预设」")
    return matches[0]


def _identity(u, pid: int, expected_image=None, creation_time=None):
    from .proc import Process

    with Process(pid, write=False) as p:
        if expected_image and Path(p.image).resolve().casefold() != Path(expected_image).resolve().casefold():
            raise RuntimeError("AE 安装路径已变化,未刷新")
        if creation_time is not None and p.creation_time != creation_time:
            raise RuntimeError("AE 已重新启动,请重新连接后刷新")
        return p.image, p.creation_time


def _hit_target(u, rows, tops, point) -> int:
    by = {r["hwnd"]: r for r in rows}
    roots = [h for h in tops if h in by and by[h]["visible"] and by[h]["rect"][0] <= point[0] < by[h]["rect"][2] and by[h]["rect"][1] <= point[1] < by[h]["rect"][3]]
    if not roots:
        raise RuntimeError("无法核对面板所属的 AE 顶层窗口")
    current = roots[0]
    seen = set()
    for _ in range(64):
        if current in seen:
            break
        seen.add(current)
        p = W.POINT(*point)
        if not u.ScreenToClient(current, C.byref(p)):
            break
        child = u.ChildWindowFromPointEx(current, p, 7)
        if not child or child == current:
            return current
        current = child
    raise RuntimeError("AE 子窗口命中测试失败")


def _write_diagnostic(data_root, payload) -> str:
    if not data_root:
        return ""
    from .proc import write_json_atomic
    from datetime import datetime

    try:
        folder = Path(data_root) / "palette"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"refresh-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        write_json_atomic(path, payload, ensure_ascii=False, indent=1)
        return str(path)
    except OSError:
        return ""


def refresh(pid: int, timeout: float = 10, *, expected_image=None, creation_time=None, progress=None, data_root=None) -> dict:
    """刷新「效果和预设」面板。全程只用 PostMessage/SendMessageTimeout,
    不动真鼠标、不抢前台;返回 {nativePaletteRefreshed, locator, ...}。"""
    if not _lock.acquire(False):
        raise RuntimeError("列表刷新正在进行,请稍候")
    try:
        u = _user32()
        with physical(u):
            return _run(u, pid, timeout, expected_image, creation_time, progress, data_root)
    finally:
        _lock.release()


def _run(u, pid, timeout, image, created, progress, data_root):
    from .proc import main_window

    started = time.monotonic()
    deadline = started + timeout
    trace = []
    owned = set()
    opened = False
    stage = "验证宿主"

    def check(target=None):
        if time.monotonic() >= deadline:
            raise TimeoutError("AE 效果列表刷新超时,未继续操作")
        _identity(u, pid, image, created)
        if target and (not u.IsWindow(target) or _owner(u, target) != pid):
            raise RuntimeError("AE 面板窗口已变化,未继续操作")

    def dismiss() -> bool:
        for h in tuple(owned):
            if u.IsWindow(h) and _owner(u, h) == pid:
                u.PostMessageW(h, 0x100, 0x1B, 0)  # WM_KEYDOWN ESC
                u.PostMessageW(h, 0x101, 0x1B, 0)  # WM_KEYUP
        until = min(deadline, time.monotonic() + 0.75)
        while time.monotonic() < until and any(u.IsWindow(h) for h in owned):
            time.sleep(0.03)
        if any(u.IsWindow(h) for h in owned):
            return False
        owned.clear()
        return True

    def locate():
        rows, tops = _native_windows(u, pid)
        scale = density / 96
        headers = search_headers(rows, scale)
        trace.append({"nativeHeaders": len(headers)})
        return headers

    try:
        image, created = _identity(u, pid, image, created)
        main = main_window(pid)
        density = dpi_for(u, main)
        if _popups(u, pid):
            raise RuntimeError("AE 菜单正在打开,请先关闭菜单后重试")
        stage = "定位效果面板"
        candidates = locate()
        if not candidates:
            command = _panel_command(u, main)
            trace.append({"windowMenu": command})
            if command["checked"]:
                raise RuntimeError("效果和预设面板已打开,但没找到可核对的搜索标题:请展开面板(点开搜索框)后重试")
            check(main)
            if not u.PostMessageW(main, 0x111, command["command"], 0):  # WM_COMMAND 打开面板
                raise RuntimeError("无法打开效果和预设面板")
            opened = True
            while not candidates:
                time.sleep(0.1)
                check()
                candidates = locate()
        for c in candidates:
            target = c["target"]
            check(target)
            p = W.POINT(*c["point"])
            if not u.ScreenToClient(target, C.byref(p)):
                raise RuntimeError("无法换算菜单位置")
            packed = ((p.y & 65535) << 16) | (p.x & 65535)
            down, up, keys = (0x201, 0x202, 1) if c.get("button") else (0x204, 0x205, 2)
            pressed = False
            try:
                for message, mask in ((0x200, 0), (down, keys), (up, 0)):
                    check(target)
                    if not u.PostMessageW(target, message, mask, packed):
                        raise RuntimeError("AE 面板消息发送失败")
                    if message == down:
                        pressed = True
                    if message == up:
                        pressed = False
            finally:
                if pressed and u.IsWindow(target) and _owner(u, target) == pid:
                    u.PostMessageW(target, up, 0, packed)
            stage = "核对刷新菜单"
            popup = None
            until = min(deadline, time.monotonic() + 0.8)
            while time.monotonic() < until:
                check()
                owned.update(_popups(u, pid))
                popup = _refresh_popup(u, pid)
                check()
                if popup:
                    break
                if owned:
                    time.sleep(0.05)
                    check()
                    popup = _refresh_popup(u, pid)
                    check()
                    break
                time.sleep(0.03)
            trace.append({"locator": c["kind"], "menuOpened": bool(owned), "refreshItemFound": bool(popup)})
            if not popup:
                if not dismiss():
                    raise RuntimeError("本次临时菜单未关闭,已停止")
                continue
            handle, menu, index, command_id = popup
            if handle not in owned:
                raise RuntimeError("刷新菜单不属于本次操作")
            stage = "执行刷新菜单"
            check(handle)
            ignored = C.c_size_t()
            remaining = max(1, min(250, int((deadline - time.monotonic()) * 1000)))
            if not u.SendMessageTimeoutW(handle, 0x1E5, index, 0, 0x0003, remaining, C.byref(ignored)):  # MN_GETHMENU
                raise RuntimeError("AE 菜单无响应,未追加操作")
            if not u.GetMenuState(menu, index, 0x400) & 0x80:
                raise RuntimeError("未能选中「刷新列表」菜单项,未执行操作")
            for message in (0x100, 0x101):
                check(handle)
                if not u.PostMessageW(handle, message, 0x0D, 0):  # WM_KEYDOWN/UP VK_RETURN
                    raise RuntimeError("AE 刷新消息发送失败")
            while time.monotonic() < deadline and u.IsWindow(handle):
                time.sleep(0.03)
            check()
            if u.IsWindow(handle):
                raise RuntimeError("AE 菜单未关闭,刷新结果不确定")
            owned.clear()
            stage = "完成"
            return {"nativePaletteRefreshed": True, "commandId": command_id, "panelOpened": opened, "locator": c["kind"], "diagnosticPath": _write_diagnostic(data_root, {"schemaVersion": 1, "pid": pid, "stage": stage, "trace": trace})}
        raise RuntimeError("菜单中未找到可用的「刷新列表」,未执行其他菜单项")
    except Exception as error:
        trace.append({"error": str(error)})
        note = _write_diagnostic(data_root, {"schemaVersion": 1, "pid": pid, "stage": stage, "trace": trace})
        raise RuntimeError(str(error) + ((";诊断记录:" + note) if note else "")) from None
    finally:
        dismiss()
