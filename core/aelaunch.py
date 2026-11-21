"""AE 启动与崩溃对话框自动处理（供软件调用）。

★ 2026-10-21 新增原因：
  AE 崩溃后重启会弹「崩溃修复选项」对话框，卡在那里看起来像"AE 没启动"。
  之前脚本一律等 140 秒，全是白等。实测正常启动只要 6-10 秒，
  卡住就是被对话框挡了。

  本模块提供：
    launch_ae()      启动 AE 并自动点掉挡路对话框，直到主窗口可用
    dismiss_dialogs() 处理当前所有 AE 对话框
    wait_ready()      等主窗口就绪（期间自动点对话框）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from typing import List, Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

#: 崩溃修复对话框可能用的窗口类名
CRASH_DIALOG_CLASSES = (
    "Qt5QWindowIcon", "Qt6QWindowIcon",
    "Qt5152QWindowIcon", "Qt652QWindowIcon",
    "AE_CDialog", "AE_CModalDialog",
)

#: 点「继续」用的按钮文字（中英双覆盖）
CONTINUE_WORDS = (
    "继续", "Continue", "确定", "OK", "是", "Yes",
    "忽略", "Ignore", "关闭", "Close",
    "以安全模式启动", "正常启动", "Start in Safe Mode",
)

DONT_SAVE_WORDS = ("不保存", "Don't Save", "否", "No", "放弃", "Discard")


def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def _class(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, b, 256)
    return b.value


def enum_windows() -> List[Tuple[int, str, str, bool]]:
    out = []

    def cb(h, l):
        vis = bool(user32.IsWindowVisible(h))
        out.append((h, _class(h), _text(h), vis))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def enum_children(hwnd: int) -> List[Tuple[int, str, str, bool]]:
    out = []

    def cb(h, l):
        out.append((h, _class(h), _text(h), bool(user32.IsWindowEnabled(h))))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


#: 这些标题的窗口绝对不是 AE 对话框（本软件自己的窗口）
_NEVER_TOUCH = ("AeLocalizer",)


def _is_dialog(cls: str, title: str) -> bool:
    # ★★ 2026-10-22：本软件的 QML 窗口类名是 Qt5QWindowIcon，
    #   之前被当成 AE 自绘对话框，launch 时连点 12 次。
    if any(t in title for t in _NEVER_TOUCH):
        return False
    if cls == "#32770":
        return bool(title.strip())
    if cls in CRASH_DIALOG_CLASSES:
        return True
    if cls.startswith("Qt") and "QWindowIcon" in cls:
        return True
    if cls.startswith("AE_"):
        if cls.startswith("AE_CApplication"):
            return not ("After Effects" in title or "AfterFX" in title)
        return True
    return False


def find_dialogs() -> List[dict]:
    """找所有 AE 对话框（排除主窗口内部控件）。"""
    # 主窗口矩形
    main_rect = None
    for h, cls, title, vis in enum_windows():
        if vis and cls.startswith("AE_CApplication"):
            r = wt.RECT()
            try:
                user32.GetWindowRect(h, ctypes.byref(r))
                main_rect = (r.left, r.top, r.right, r.bottom)
            except Exception:
                pass
            break

    # ★ 只处理 AE 进程的窗口
    _ae_pids = set(ae_pids())

    # ★★ 2026-10-22：Mocha / Imagineer 的许可证框标题是【空的】，
    #   而 _is_dialog 对 #32770 只认"有标题"，于是它被漏掉，
    #   结果 AE 卡在它上面 45~60 秒（用户以为死了）。
    #   这里对空标题的 #32770 额外看子控件文字。
    _MOCHA_MARKS = ("OS_ViewContainer", "DroverLord", "Imagineer")
    _WAIT_WORDS = ("等待程序响应", "等待程序回應", "Wait for the program to respond")
    _KILL_WORDS = ("关闭程序", "關閉程式", "Close the program")

    out = []
    for h, cls, title, vis in enum_windows():
        if not vis:
            continue
        # ★ 先判是不是目标对话框；空标题的 #32770 额外看子控件
        #   （Mocha 许可证框 cls=#32770 标题为空，子控件类名 "DroverLord - Win"、
        #    文字 "OS_ViewContainer"。_is_dialog 只认"有标题"，会把它漏掉，
        #    所以必须在这里补一次判断 —— 之前把补判写在 _is_dialog 之后，
        #    永远走不到那一步。）
        target = _is_dialog(cls, title)
        if not target and cls == "#32770" and not title.strip():
            kids = " | ".join(t for _hh, _cc, t, _en in enum_children(h))
            target = any(m in kids for m in _MOCHA_MARKS)
        if not target:
            continue
        if _ae_pids:
            _pid = wt.DWORD()
            try:
                user32.GetWindowThreadProcessId(h, ctypes.byref(_pid))
            except Exception:
                continue
            if _pid.value not in _ae_pids:
                continue
        if main_rect:
            r = wt.RECT()
            try:
                user32.GetWindowRect(h, ctypes.byref(r))
                cx = (r.left + r.right) // 2
                cy = (r.top + r.bottom) // 2
                ml, mt, mr, mb = main_rect
                if ml <= cx <= mr and mt <= cy <= mb:
                    continue
            except Exception:
                pass
        btns = [{"hwnd": ch, "text": t, "enabled": en}
                for ch, c, t, en in enum_children(h) if c == "Button"]
        out.append({"hwnd": h, "class": cls, "title": title, "buttons": btns})
    return out


def _click(hwnd: int) -> bool:
    try:
        user32.PostMessageW(hwnd, 0x00F5, 0, 0)      # BM_CLICK
        return True
    except Exception:
        return False


def _click_modal(hwnd: int) -> bool:
    """点「自绘对话框」右下角的按钮。

    ★ 2026-10-30 用户报「多添加几个效果就跳报错」时实测：
      AE 的提示框（#32770 + Adobe DirectUI 子窗口 DroverLord）**对合成按键
      完全无反应** —— PostMessage(WM_KEYDOWN)、keybd_event(VK_RETURN)、
      PostMessage(BM_CLICK) 全都无效，而且 EnumChildWindows 读不到按钮文字
      （AE 自己画的），只有真的发鼠标点击才关掉。

      点的位置 = 窗口右下角按钮区（实测 510x167 / 458x137 / 397x136 三种
      AE 提示框都命中）：
        · 单按钮信息框 -> 点在「确定」上
        · 三按钮保存框 -> 点在【最右边】的「取消」上 —— 取消只是不关闭，
          不会丢未保存的改动，这是最安全的落点（绝不会点到"保存"/"不保存"）。
    """
    try:
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        x, y = r.right - 60, r.bottom - 28
        cur = wt.POINT()
        had_cur = bool(user32.GetCursorPos(ctypes.byref(cur)))
        user32.ShowWindow(hwnd, 5)              # SW_SHOW
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        user32.SetCursorPos(x, y)
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.05)
        if had_cur:                              # 别把用户鼠标留在原处
            user32.SetCursorPos(cur.x, cur.y)
        return True
    except Exception:
        return False


def _send_key(hwnd: int, vk: int) -> bool:
    KEYUP = 0x0002
    try:
        user32.ShowWindow(hwnd, 5)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk, 0, KEYUP, 0)
        return True
    except Exception:
        return False


def dismiss_dialogs(verbose: bool = False) -> int:
    """点掉当前所有 AE 对话框，返回处理数。

    ★★ 2026-10-22 安全规则：
      Windows 的「程序未响应」框有 [关闭程序] [等待程序响应] 两个按钮，
      默认按钮常是「关闭程序」—— 直接发回车会把 AE 杀掉
      （用户看到的就是"AE 图标闪一下就没了"）。
      所以这类框必须精确点「等待」，找不到就完全不动。
    """
    _WAIT = ("等待程序响应", "等待程序回應", "Wait for the program to respond")
    _KILL = ("关闭程序", "關閉程式", "Close the program")

    n = 0
    for d in find_dialogs():
        labels = [(b.get("text") or "") for b in d["buttons"]]
        joined = " ".join(labels)
        # ---- ① 未响应框 ----
        if any(w in joined for w in _WAIT) or any(w in joined for w in _KILL):
            hw = None
            for b in d["buttons"]:
                t = (b.get("text") or "").strip()
                if t and any(w in t for w in _WAIT) and b.get("enabled", True):
                    hw = b["hwnd"]
                    break
            if hw:
                _click(hw)
                n += 1
                if verbose:
                    print("  [等待] 未响应框 -> 点「等待程序响应」")
            elif verbose:
                print("  [等待] 未响应框里找不到「等待」按钮，保持不动")
            continue

        picked = None
        for b in d["buttons"]:
            t = (b.get("text") or "").strip()
            if not t or not b.get("enabled", True):
                continue
            if any(w in t for w in CONTINUE_WORDS):
                picked = b["hwnd"]
                break
        if picked is None:
            for b in d["buttons"]:
                t = (b.get("text") or "").strip()
                if t and any(w in t for w in DONT_SAVE_WORDS):
                    picked = b["hwnd"]
                    break
        if picked is not None:
            _click(picked)
            n += 1
            if verbose:
                print("  点掉对话框: %r" % d["title"][:40])
            continue
        # ---- ③ 自绘对话框（AE 的 DirectUI 提示框）----
        #   这类框读不到按钮文字，也不吃合成按键，必须发真鼠标事件。
        _send_key(d["hwnd"], 0x0D)
        if _click_modal(d["hwnd"]):
            n += 1
            if verbose:
                print("  [自绘] 鼠标点掉: %r" % d["title"][:40])
        elif verbose:
            print("  [自绘] 点不掉: %r" % d["title"][:40])
    return n


def _alive(pid: int) -> bool:
    """GetExitCodeProcess == STILL_ACTIVE(259) 才算活着。

    ★ 2026-10-30: AE 崩溃/退出后会留下僵尸进程 —— tasklist 照样列得出来,
      旧代码用它判"已经在运行", 于是点「启动 AE」直接跳过 Popen, 用户
      看到的就是"点了没反应"。
    """
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        try:
            code = wt.DWORD(0)
            if not k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return False


def ae_pids() -> List[int]:
    """活着的 AfterFX.exe 进程 PID(僵尸不算)。"""
    from .proc import list_ae_processes

    try:
        return [process["pid"] for process in list_ae_processes()]
    except (OSError, RuntimeError):
        return []


def ae_running() -> bool:
    """是否有真正活着的 AE 在跑。"""
    return bool(ae_pids())


def install_root(version: str) -> str:
    """按年份("2022")找 AE 安装根目录; 找不到返回空串。"""
    v = str(version or "").strip()
    if not v:
        return ""
    try:
        from .inventory import find_ae_installs

        for _ver, _root in find_ae_installs():
            if str(_ver).strip() == v:
                return _root
    except Exception:
        pass
    guess = "C:/Program Files/Adobe/Adobe After Effects " + v
    return guess if os.path.isdir(guess) else ""


def nudge_redraw(pid: int = 0, hwnd: int = 0, shots_dir: str = "",
                 force: bool = True) -> dict:
    """让 AE 立刻重画一次界面（写完效果名后用）。

    ★ 2026-11-07 真机结论（挨个试过，别回退）：
      - RedrawWindow / PostMessage(WM_MOUSEMOVE、WM_KEYDOWN) 一律无效：
        AE 面板是 D3D 自绘，只认**真实输入队列**事件。
      - SetCursorPos 扫视 + keybd_event 切图层确实能刷新，但光标会动、
        界面会闪，用户明确不要（「不想要用鼠标去…闪一下很奇怪」）。
      - 改成**零位移** mouse_event：事件真实进输入队列，位移为 0
        （光标不动），且只在光标本来就在 AE 窗口里时发 —— 视觉上无感。
    """
    out = {"hwnd": 0, "zero_move": 0, "ok": False}
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        h = int(hwnd or 0)
        if not h:
            for hh, cls, title, vis in enum_windows():
                if not vis or not cls.startswith("AE_CApplication"):
                    continue
                if pid:
                    owner = wt.DWORD()
                    u.GetWindowThreadProcessId(wt.HWND(hh), ctypes.byref(owner))
                    if int(owner.value) != int(pid):
                        continue
                h = int(hh)
                break
        if not h:
            return out
        out["hwnd"] = h
        # 光标不在 AE 窗口里时，输入事件到不了 AE，发了也没用（不闪即可）
        inside = False
        try:
            pt = wt.POINT()
            rc = wt.RECT()
            u.GetCursorPos(ctypes.byref(pt))
            u.GetWindowRect(wt.HWND(h), ctypes.byref(rc))
            inside = (rc.left <= int(getattr(pt, "x", 0)) < rc.right
                      and rc.top <= int(getattr(pt, "y", 0)) < rc.bottom)
        except Exception:
            inside = False
        if not inside and force:
            # ★ 2026-11-07 晚：用户点完 AeLocalizer 的开关时，光标在 AeLocalizer
            #   上，零位移事件到不了 AE，于是「写入成功但面板不刷新」。
            #   这里把光标瞬移到 AE 窗口左上内侧、发两次零位移、再移回原位
            #   （全程约 40ms，不点击、不抢焦点、不切图层）。
            import time as _t
            try:
                pt2 = wt.POINT()
                rc2 = wt.RECT()
                u.GetCursorPos(ctypes.byref(pt2))
                u.GetWindowRect(wt.HWND(h), ctypes.byref(rc2))
                ox, oy = int(getattr(pt2, "x", 0)), int(getattr(pt2, "y", 0))
                tx = rc2.left + max(60, (rc2.right - rc2.left) // 6)
                ty = rc2.top + max(60, (rc2.bottom - rc2.top) // 4)
                u.SetCursorPos(int(tx), int(ty))
                _t.sleep(0.02)
                for _dx in (2, -2):
                    try:
                        u.mouse_event(0x0001, _dx, 0, 0, 0)
                        out["zero_move"] += 1
                    except Exception:
                        pass
                _t.sleep(0.02)
                u.SetCursorPos(ox, oy)
                out["moved"] = True
            except Exception:
                pass
        elif inside:
            # ★ 零位移(0,0) 在真机上被 AE 忽略；改成「右移 2px 再回原位」——
            #   真实 WM_MOUSEMOVE、视觉上几乎无感，但足以让自绘面板重算 hover。
            for _dx in (2, -2):
                try:
                    u.mouse_event(0x0001, _dx, 0, 0, 0)
                    out["zero_move"] += 1
                except Exception:
                    pass
        out["ok"] = out["zero_move"] > 0
        # 兜底：标脏（对自绘窗口多半无效，代价极小）
        try:
            u.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.HRGN, wt.UINT]
            u.RedrawWindow.restype = wt.BOOL
            u.RedrawWindow(wt.HWND(h), None, None, 0x0001 | 0x0004 | 0x0080)
        except Exception:
            pass
    except Exception:
        pass
    return out

def main_window() -> Optional[Tuple[int, str]]:
    for h, cls, title, vis in enum_windows():
        if vis and cls.startswith("AE_CApplication"):
            return h, title
    return None



def ae_main_windows() -> List[Tuple[int, str]]:
    """有主窗口的 AE(标题形如「Adobe After Effects 2022 - xxx.aep」)。"""
    out: List[Tuple[int, str]] = []
    for h, cls, title, vis in enum_windows():
        if not vis or not cls.startswith("AE_CApplication"):
            continue
        t = (title or "").strip()
        if not t.startswith("Adobe After Effects"):
            continue
        out.append((h, t))
    return out


def live_ae_target() -> Tuple[int, str]:
    """唯一一个有主窗口的 AE → (pid, exe); 没有或有多个返回 (0, "")。

    ★ 必须按主窗口挑: 本机常驻大量 `AfterFX.exe -r` 留下的无窗口桩进程,
      按进程表数 AE 永远不唯一。
    """
    wins = ae_main_windows()
    if len(wins) != 1:
        return 0, ""
    h, _title = wins[0]
    try:
        pid = int(_pid_of_window(h) or 0)
    except Exception:
        pid = 0
    if pid <= 0 or not _alive(pid):
        return 0, ""
    exe = ""
    try:
        from .aejsx import exe_of_pid

        exe = exe_of_pid(pid)
    except Exception:
        exe = ""
    return pid, exe

def wait_ready(timeout: float = 25.0, verbose: bool = False) -> bool:
    """等 AE 主窗口就绪，期间自动点掉挡路对话框。"""
    end = time.time() + max(3.0, timeout)
    clicked = 0
    while time.time() < end:
        clicked += dismiss_dialogs(verbose=verbose)
        mw = main_window()
        if mw:
            if verbose:
                print("  AE 就绪: %r（点掉 %d 个对话框）" % (mw[1][:40], clicked))
            return True
        time.sleep(0.4)
    return False


def launch_ae(exe: str = "", timeout: float = 25.0,
              verbose: bool = False, version: str = "") -> Tuple[bool, str]:
    """启动 AE（若未运行）并等到主窗口可用。

    返回 (成功, 说明)
    """
    if not exe and version:
        _root = install_root(version)
        if _root:
            exe = os.path.join(_root, "Support Files", "AfterFX.exe")
    if not exe:
        # ★ 2026-10-22：不再写死 2026。按已安装版本从高到低探测，
        #   这样在 AE 2025 上点「启动 AE」也能起对的那个。
        try:
            from .inventory import find_ae_installs

            for _ver, _root in find_ae_installs():
                _p = os.path.join(_root, "Support Files", "AfterFX.exe")
                if os.path.exists(_p):
                    exe = _p
                    break
        except Exception:
            pass
        if not exe:
            exe = (r"C:\Program Files\Adobe\Adobe After Effects 2026"
                   r"\Support Files\AfterFX.exe")
    if not os.path.exists(exe):
        return False, "找不到 AE：%s" % exe

    # 已在运行 -> 只等。★ 2026-10-30: 只认活着的实例(僵尸不算)
    live = ae_pids()
    running = bool(live)
    if verbose:
        print("  （%s）" % ("AE 已在运行，只等待就绪" if running else "没有活着的 AE，开始启动"))

    if not running:
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except OSError as exc:
            return False, "启动失败：%s" % exc

    # ★★ 2026-10-22 实测：AE 启动时会被两个"看起来像卡死"的框挡住
    #   ① Mocha / Imagineer 的许可证框（OS_ViewContainer，标题为空）
    #      实测能挡 45~60 秒，用户以为 AE 死了
    #   ② Windows 的「程序未响应」框
    #      ★ 默认按钮是「关闭程序」，点错就把 AE 杀了
    #      用户看到的就是"AE 图标闪一下就没了"
    #   这里在等待期间持续自动处理它们（只点「等待」，绝不点「关闭」）。
    handled = 0
    try:
        import sys as _sys

        _tools = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
        if _tools not in _sys.path:
            _sys.path.insert(0, _tools)
        import ae_dialog as _dlg

        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            try:
                handled += _dlg.handle_once(verbose=False)
            except Exception:
                pass
            if _dlg.main_window():
                break
            time.sleep(0.8)
    except Exception:
        pass

    ok = wait_ready(max(5.0, timeout - min(handled, 20)), verbose=verbose)
    if verbose and handled:
        print("  （自动处理了 %d 个启动期对话框）" % handled)
    return (True, "AE 已就绪") if ok else (False, "%.0f 秒内未就绪" % timeout)


def _pid_of_window(hwnd):
    """窗口属于哪个进程。"""
    try:
        pid = wt.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def restart_running_ae(timeout=90.0, verbose=False):
    """把正在运行的 AE 关掉再启动, 让新的汉化立刻生效（工程自动重开）。

    ★ 为什么必须重启: 实测(2026-10-30) 效果/参数名是 AE 在「加载工程」时
      按当时插件状态解析并缓存在进程里的, 不存进 .aep; 同会话里重开工程、
      重绘、选中图层都不会重新解析。所以切换中英只有重启进程这一条路。

    安全闸: 窗口标题带 *（有未保存改动）时直接拒绝, 绝不替用户丢工作。
    返回 (成功, 说明)。
    """
    from .aejsx import exe_of_pid, project_path, title_has_unsaved

    mw = main_window()
    if not mw:
        return False, "没有找到 AE 主窗口（可能在启动中）"
    hwnd, title = mw
    if title_has_unsaved(title):
        return False, "AE 里有未保存的改动，先保存再切换（这次没有动你的工程）"
    pid = _pid_of_window(hwnd)
    exe = exe_of_pid(pid)
    if not exe or not os.path.exists(exe):
        return False, "找不到 AE 的可执行文件路径"
    aep = ""
    try:
        aep = project_path(exe, timeout=20.0)
    except Exception:
        aep = ""
    if verbose:
        print("  重启 AE: pid=%d exe=%s 工程=%r" % (pid, exe, aep))

    before = set(ae_pids())
    try:
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(int(hwnd)), 0x0010, 0, 0)
    except Exception as exc:
        return False, "关闭 AE 失败: %s" % exc

    end = time.time() + 60.0
    while time.time() < end:
        try:
            dismiss_dialogs(verbose=False)
        except Exception:
            pass
        if not (before & set(ae_pids())):
            break
        time.sleep(0.5)
    else:
        return False, "AE 没有在 60 秒内退出（可能弹着对话框），请手动关闭后再切换"

    args = [exe] + ([aep] if aep else [])
    try:
        subprocess.Popen(args, cwd=os.path.dirname(exe))
    except OSError as exc:
        return False, "AE 已关闭但重启失败：%s" % exc

    ok = wait_ready(timeout, verbose=verbose)
    name = os.path.basename(aep) if aep else "新工程"
    if ok:
        return True, "AE 已重启（%s），译名已按当前设置生效" % name
    return False, "AE 已重启但 %.0f 秒内没就绪，请稍等一下" % timeout

# ------------------------------------------------------------- 命令行入口

if __name__ == "__main__":
    import argparse
    import sys as _sys

    _ap = argparse.ArgumentParser(
        description="AE 启动 / 对话框自动处理（点「继续」「等待」等挡路按钮）")
    _ap.add_argument("cmd", choices=["launch", "dismiss", "wait", "list"],
                     help="launch=启动AE并点掉对话框 | dismiss=点掉当前对话框 | "
                          "wait=等主窗口就绪 | list=列出当前AE窗口/对话框")
    _ap.add_argument("--timeout", type=float, default=30.0, help="超时秒数")
    _ap.add_argument("--exe", default="", help="指定 AfterFX.exe 路径（launch 用）")
    _ap.add_argument("--version", default="", help="指定 AE 年份，如 2022（launch 用）")
    _ap.add_argument("-v", "--verbose", action="store_true")
    _args = _ap.parse_args()

    if _args.cmd == "list":
        _pids = ae_pids()
        if _pids:
            print("运行中的 AE: pid=%s" % ", ".join(str(p) for p in _pids))
        else:
            print("运行中的 AE: 无（已退出的僵尸进程不算）")
        for d in find_dialogs():
            btns = " | ".join((b.get("text") or "?") for b in d["buttons"]) or "（无按钮）"
            print("对话框: class=%s title=%r\n  按钮: %s" % (d["class"], d["title"], btns))
        mw = main_window()
        print("AE 主窗口:", "%r" % mw[1] if mw else "未运行/未就绪")
        if not find_dialogs() and not mw:
            print("（没有发现 AE —— AE 未启动）")
    elif _args.cmd == "dismiss":
        _n = dismiss_dialogs(verbose=True)
        print("处理了 %d 个对话框" % _n)
    elif _args.cmd == "wait":
        _ok = wait_ready(_args.timeout, verbose=True)
        print("AE 就绪" if _ok else "%.0f 秒内未就绪" % _args.timeout)
        _sys.exit(0 if _ok else 1)
    elif _args.cmd == "launch":
        _ok, _msg = launch_ae(exe=_args.exe, timeout=_args.timeout,
                              verbose=True, version=_args.version)
        print(_msg)
        _sys.exit(0 if _ok else 1)
