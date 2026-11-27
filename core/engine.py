"""运行时引擎管理：guard_uni（参数名/下拉值）+ 官方 UTF-8 Fixer（2026 编码兼容）。

设计约束：
  1. 复用经过实机验证的 guard_uni 引擎。
  2. AE 2026 及以上使用原版 MIT UTF-8 Fixer。
  3. 软件层管理选择、词典、应用、回滚和版本门控。

三层职责：
  L1 静态层   .aex PiPL（效果名、分类名）  -> 本工具直接改文件
  L2 运行时层 guard_uni（参数名、下拉值）  -> 注入 FLT.dll hook
  L3 兼容层   AE-UTF8-Fixer（2026+）      -> 修 GBK->UTF-8

★ 为什么引擎的词典目录要单独一份
  引擎读的是【扁平】{英文: 中文}，而用户勾选是【按插件】的。
  所以每次启动引擎前，都要按当前勾选重新生成 <DATA_ROOT>/engine_dict/，
  再把这个目录通过 SAPPHIRE_CN_DICT 交给引擎 —— 这就是"用户自选插件"
  在参数名通道上的落地方式。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
# ★ 2026-10-29: 与 app.backend._resolve_data_root 同一契约 —— 测试/脚本设
#   AELOC_DATA_ROOT 切沙盒。此前本模块无视该变量, 测试套件经 prepare_dict
#   把真实 engine_dict/extra_overrides.dat 覆盖成测试词条(遮蔽完整词典,
#   守护重启后词典 51,649 -> 189, 实事故)。
#   ★ 2026-11-02 二次加固:光靠「跑测试先设环境变量」的文档约定不够 ——
#     02:49 同款事故复发(裸跑 unittest discover 把 25,951 条词库再次覆盖成
#     7 条测试词,且 unittest discover 不导入 tests/__init__.py, 包级护栏
#     根本没执行)。unittest 进程内直接强制沙盒, 不再依赖外部约定;
#     真实数据根只允许 tools/rebuild_engine_dict.py(断言必须无此变量)
#     显式触达。
if os.environ.get("AELOC_DATA_ROOT", "").strip():
    DATA_ROOT = os.environ["AELOC_DATA_ROOT"].strip()
elif "unittest" in sys.modules:
    import tempfile

    DATA_ROOT = os.path.join(tempfile.gettempdir(), "aeloc_test_sandbox_default")
    os.makedirs(DATA_ROOT, exist_ok=True)
else:
    DATA_ROOT = os.path.join(LOCALAPPDATA, "AeLocalizer")

#: 引擎词典目录（每次启动前按勾选重建）
ENGINE_DICT_DIR = os.path.join(DATA_ROOT, "engine_dict")

#: guard_uni 引擎的来源（用户已有资产）
GUARD_SRC_CANDIDATES = [
    os.path.join(DATA_ROOT, "guard_runtime", "guard_uni.py"),
    os.path.join(ROOT, "runtime", "managed_guard.py"),
]
#: 官方 Fixer（MIT）
FIXER_NAME = "00_UTF8_Fixer.aex"

#: 需要 Fixer 的最低版本
MIN_VERSION_NEEDING_FIXER = 2026

#: 引擎的优雅停止信号（guard_uni 会轮询这个文件）
STOP_FLAG = "STOP_NOW.flag"


@dataclass
class EngineStatus:
    """引擎就绪状态。"""

    guard_available: bool = False
    guard_path: str = ""
    guard_reason: str = ""

    fixer_installed: bool = False
    fixer_path: str = ""
    fixer_needed: bool = False
    fixer_reason: str = ""

    dict_available: bool = False
    dict_path: str = ""
    dict_entries: int = 0

    #: 引擎进程是否在跑
    running: bool = False
    profile_confirmed: bool = False
    #: 引擎读到的词典条数（engine_dict）
    engine_dict_entries: int = 0
    #: 下拉值词典条数（enum_fill）
    enum_dict_entries: int = 0
    #: 引擎日志路径
    log_path: str = ""

    @property
    def ready(self) -> bool:
        """能否开始汉化。"""
        return self.guard_available and self.dict_available


def find_guard() -> tuple:
    """找 guard_uni 引擎。返回 (路径, 原因)。"""
    for p in GUARD_SRC_CANDIDATES:
        if os.path.exists(p):
            return p, ""
    return "", "未找到 guard_uni 引擎（参数名通道需要它）"


def collect_dir() -> str:
    """引擎把采集到的参数名写在哪（guard_uni 的 OUT_DIR）。"""
    gp, _ = find_guard()
    if gp:
        return os.path.join(os.path.dirname(gp), "out")
    return ""


def seen_param_names() -> List[str]:
    """读引擎采集到的【全部参数名】。

    ★ 为什么需要（2026-10-22 实测）：
      参数名不在 .aex 里，是插件在运行时注册出来的，静态扫描拿不到。
      所以 AI 翻译无法预先知道要译哪些参数 —— 用户勾选 DLSS 全部汉化后
      效果名汉化了、参数名一个没动（词典里压根没有那些词）。

      引擎挂钩 FLT_MakeStreamFromParamDef 时会把每个见过的参数名落到
      all_param_names.json（实测 1641 条，含 DLSS 的 10 条）。
      这里读出来交给 AI 翻译补词，才能形成完整闭环。
    """
    d = collect_dir()
    if not d:
        return []
    p = os.path.join(d, "all_param_names.json")
    if not os.path.exists(p):
        return []
    try:
        import json

        with open(p, encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("names") or []
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for x in data:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        if len(s) < 2 or len(s) > 80:
            continue
        if s.isdigit():
            continue
        seen.add(s)
        out.append(s)
    return out


def find_fixer(ae_root: str) -> tuple:
    """检查某个 AE 安装是否装了官方 Fixer。"""
    p = os.path.join(ae_root, "Support Files", "Plug-ins", FIXER_NAME)
    if os.path.exists(p):
        return p, os.path.getsize(p)
    return "", 0


def needs_fixer(ae_version: str) -> bool:
    try:
        return int(ae_version) >= MIN_VERSION_NEEDING_FIXER
    except (TypeError, ValueError):
        return False


def check(ae_root: str = "", ae_version: str = "") -> EngineStatus:
    """全面检查引擎状态。"""
    st = EngineStatus()

    gp, reason = find_guard()
    st.guard_available = bool(gp)
    st.guard_path = gp
    st.guard_reason = reason

    if ae_root:
        fp, size = find_fixer(ae_root)
        st.fixer_installed = bool(fp)
        st.fixer_path = fp
        st.fixer_needed = needs_fixer(ae_version)
        if not st.fixer_needed:
            st.fixer_reason = "AE %s 为 GBK 世界，无需修复层" % ae_version
        elif st.fixer_installed:
            st.fixer_reason = "已安装（%d KB）" % (size // 1024)
        else:
            st.fixer_reason = "未安装：2026 上插件自带的中文会显示乱码"

    dp = os.path.join(DATA_ROOT, "dict.bin")
    if os.path.exists(dp):
        st.dict_available = True
        st.dict_path = dp
        # ★ 性能（2026-10-21）：verify() 要读 2 万条，实测 0.5s。
        #   engineReport 每次刷新都调 -> 点按钮卡顿。按 mtime 缓存。
        try:
            import time as _t

            sig = (dp, os.path.getmtime(dp))
            cache = globals().get("_DICT_VERIFY_CACHE")
            if cache and cache[0] == sig:
                st.dict_entries = cache[1]
            else:
                from .dictmmap import verify

                st.dict_entries = verify(dp)
                globals()["_DICT_VERIFY_CACHE"] = (sig, st.dict_entries)
        except Exception:
            st.dict_entries = 0

    st.running = engine_running()
    st.profile_confirmed = st.running and profile_runtime_confirmed()
    st.log_path = engine_log_path()
    try:
        from .enginedict import engine_dict_ready, enum_dict_ready

        st.engine_dict_entries = engine_dict_ready(ENGINE_DICT_DIR)
        st.enum_dict_entries = enum_dict_ready(ENGINE_DICT_DIR)
    except Exception:
        st.engine_dict_entries = 0
        st.enum_dict_entries = 0
    return st


def install_fixer(ae_root: str, source: str = "") -> tuple:
    """把官方 Fixer 装到指定 AE。返回 (成功, 消息)。"""
    dst_dir = os.path.join(ae_root, "Support Files", "Plug-ins")
    if not os.path.isdir(dst_dir):
        return False, "插件目录不存在：%s" % dst_dir

    if not source:
        bak = os.path.join(DATA_ROOT, "fixer_backup", FIXER_NAME)
        if os.path.exists(bak):
            source = bak
    if not source or not os.path.exists(source):
        return False, "找不到 Fixer 源文件（请手动放置 %s）" % FIXER_NAME

    dst = os.path.join(dst_dir, FIXER_NAME)
    try:
        shutil.copy2(source, dst)
        return True, "已安装到 %s" % dst
    except PermissionError:
        return False, "需要管理员权限（目标在 Program Files 下）"


def remove_fixer(ae_root: str, backup: bool = True) -> tuple:
    """卸载 Fixer（先备份）。返回 (成功, 消息)。"""
    src = os.path.join(ae_root, "Support Files", "Plug-ins", FIXER_NAME)
    if not os.path.exists(src):
        return True, "未安装，无需卸载"

    if backup:
        bak_dir = os.path.join(DATA_ROOT, "fixer_backup")
        os.makedirs(bak_dir, exist_ok=True)
        try:
            shutil.copy2(src, os.path.join(bak_dir, FIXER_NAME))
        except OSError:
            pass

    try:
        os.remove(src)
        return True, "已卸载（已备份）" if backup else "已卸载"
    except PermissionError:
        return False, "需要管理员权限"


def ae_running() -> List[int]:
    """返回正在运行的 AfterFX PID 列表。"""
    from .aelaunch import ae_pids

    return ae_pids()


# ★ 权威停止时间戳(2026-10-29):stop 后日志心跳仍"保鲜"90 秒,
#   engine_running 的启发式会假报"运行中",按钮状态与状态栏打架。
#   stop/start 时更新;95 秒内强制 False(除非重新启动清零)。
_STOPPED_AT = 0.0
_STARTED_GUARD = None


def mark_stopped() -> None:
    """强杀/优雅停止后由调用方标记,让 engine_running 立即为 False。"""
    global _STOPPED_AT
    import time as _t

    _STOPPED_AT = _t.time()
    _clear_guard_identity_cache()


def guard_confirmed() -> bool:
    """只认“身份已确认”的 guard：有效/旧锁的存活 guard PID，或命令行签名。

    不再用“任意 python 进程 + 日志新鲜”，那会把别的高权限 python 进程误报
    成 guard；也不再仅凭日志心跳。
    """
    try:
        if read_guard_lock(stale_ok=True):
            return True
    except Exception:
        pass
    try:
        if guard_pids():
            return True
    except Exception:
        pass
    return False


def engine_running() -> bool:
    """引擎进程是否在跑（身份确认，不靠日志/任意 python 猜测）。"""
    import time as _t2

    if _STOPPED_AT and (_t2.time() - _STOPPED_AT) < 95.0 and not guards_alive():
        return False
    return guard_confirmed()


def _engine_log_fresh(seconds: float = 90.0) -> bool:
    """引擎日志在最近 N 秒内有心跳 -> 认为引擎活着。

    ★ 比查进程更可靠：引擎每 30 秒写一次心跳，
      而查进程还得区分"是 python 但不是我那个 python"。
    """
    try:
        p = engine_log_path()
        if not os.path.exists(p):
            return False
        return (time.time() - os.path.getmtime(p)) < seconds
    except Exception:
        return False


def _engine_running_ps() -> bool:
    """退路：用 PowerShell 查（慢但准）。"""
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
            "| Where-Object { $_.CommandLine -like '*guard_uni*' } "
            "| Measure-Object | Select-Object -ExpandProperty Count"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        ).stdout or ""
        return out.strip().isdigit() and int(out.strip()) > 0
    except Exception:
        return False


def engine_log_path() -> str:
    """引擎实际写日志的位置。

    ★ 修复（2026-10-21）：原来是 DATA_ROOT/engine.log，但引擎
      （guard_uni.py）实际写在 <hook>/out/guard_uni.log。
      导致自诊断读到 0 字节的空文件，误报「写入 0 次」。
      这里优先返回引擎真实日志；不存在再退回旧路径。
    """
    gp, _ = find_guard()
    if gp:
        real = os.path.join(os.path.dirname(gp), "out", "guard_uni.log")
        if os.path.exists(real):
            return real
    return os.path.join(DATA_ROOT, "engine.log")


def build_engine_command(
    guard_path: str,
    dict_dir: str,
    *,
    mode: str = "apply",
    hold_seconds: int = 600,
) -> List[str]:
    """构造启动 guard_uni 的命令行（供测试与诊断用）。

    ★ 打包版（PyInstaller）里 `sys.executable` 是 AeLocalizer.exe 自己 ——
      直接当解释器用会再开一个 GUI。所以走 `--run-guard` 自执行入口。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-guard", guard_path, mode, str(hold_seconds)]
    return [sys.executable, guard_path, mode, str(hold_seconds)]


def start_engine(
    guard_path: str,
    dict_dir: str,
    *,
    hold_seconds: int = 600,
    log_path: str = "",
    ae_root: str = "",
    data_root: str = "",
    skip_hash: bool = False,
) -> tuple:
    """后台启动 guard_uni。返回 (成功, 消息/日志路径)。

    ★ 2026-10-21 新增模块哈希钉死：
      AE 若是补丁版/精简版（AfterFX.exe 或 FLT.dll 被替换），
      内存布局和函数签名都会变，写内存有崩溃风险，直接拒绝。
    """
    if not os.path.exists(guard_path):
        return False, "引擎不存在：%s" % guard_path
    if not os.path.isdir(dict_dir):
        return False, "词典目录不存在：%s" % dict_dir

    # ★ 术语修正层（term_fix）：旧词典也要补上，升级用户不重建词典即生效。
    #   已是最新时零写入；失败不影响启动（译名退化为旧值，但引擎照常工作）。
    try:
        from .termfix import ensure_term_fix

        ensure_term_fix(dict_dir)
    except Exception:
        pass

    # ★★ 2026-10-25 真重启（用户实翻车的根因修复）：
    #   守护是常驻的；旧守护还活着就再 spawn 一个 = 多个 frida 代理同时
    #   注入同一个 AE = frida-agent.dll 崩溃 → AE 卡死在「崩溃修复选项」
    #   → 用户重开 AE 又被注入 → 死循环。「重启引擎」必须是真重启。
    try:
        stop_ok = True
        if engine_running():
            _stopped, _smsg = stop_engine(timeout=10)
            stop_ok = bool(_stopped)
            if not stop_ok:
                # 不阻断等待：仍给旧进程一点时间自然退出，但未确认前不 spawn。
                pass
            import time as _t

            _deadline = _t.time() + 25
            while _t.time() < _deadline and (engine_running()
                                             or (not os.environ.get("AELOC_DATA_ROOT")
                                                 and guard_locks_alive(DATA_ROOT))):
                _t.sleep(1.0)
            if (engine_running() or (not os.environ.get("AELOC_DATA_ROOT")
                                     and guard_locks_alive(DATA_ROOT))
                    or not stop_ok):
                # 不确认退出就绝不 spawn 第二个；也不再强杀未知 PID。
                return False, ("旧参数守护仍在运行/未确认退出，"
                               "拒绝启动第二个 guard；请正常停止旧守护后重试")
    except Exception:
        return False, "旧参数守护停止流程异常，拒绝双注入；请正常停止后重试"
    if not os.environ.get("AELOC_DATA_ROOT") and guard_locks_alive(DATA_ROOT):
        return False, "检测到旧参数守护 lock 仍活，拒绝启动第二个 guard"

    # ★ 哈希校验（拒绝在可疑 AE 上写入）
    if not skip_hash and ae_root:
        try:
            from core.modhash import verify
            vr = verify(ae_root, data_root or DATA_ROOT)
            if not vr.ok:
                return False, vr.reason
        except Exception as exc:      # 校验本身出错不该阻断启动
            _ = exc

    env = dict(os.environ)
    env["SAPPHIRE_CN_DICT"] = dict_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["AELOC_DATA_ROOT"] = DATA_ROOT
    env["AELOC_ENGINE_DICT"] = ENGINE_DICT_DIR
    env["AELOC_PROJECT_ROOT"] = ROOT
    # Frida 17.26.3 fixes nearby-allocation overflow on low-address AE DLLs.
    # Use the project package without replacing the system Python dependency.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        os.path.join(ROOT, ".runtime-deps"), env.get("PYTHONPATH", ""))))
    env["AELOC_MANAGED_PROTOCOL"] = "aeloc-managed-guard/1"

    log = log_path or engine_log_path()
    os.makedirs(os.path.dirname(log), exist_ok=True)

    try:
        with open(log, "w", encoding="utf-8") as lf:
            # stdin 必须隔离：guard 只经 control 文件/命名管道通信，不该继承
            # 调用方（GUI/driver）的 stdin。若继承的是带后台 reader 线程的共享
            # 管道，Windows 上子进程 Python 启动早期的 CRT stdio 位置查询会阻塞
            # （实测 992s 级，且与 reader 是否释放同刻恢复）。2026-11-14 定位。
            child = subprocess.Popen(
                build_engine_command(guard_path, dict_dir, mode="apply",
                                     hold_seconds=hold_seconds),
                cwd=os.path.dirname(guard_path),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        global _STOPPED_AT, _STARTED_GUARD
        _STOPPED_AT = 0.0
        _STARTED_GUARD = child
        return True, log
    except Exception as exc:
        return False, "启动失败：%s" % exc


def _proc_creation_time(pid: int) -> int:
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        k.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p]
        k.GetProcessTimes.restype = ctypes.c_int
        h = k.OpenProcess(0x1000, 0, int(pid))
        if not h:
            return 0

        class _FT(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

        c, e, kt, u = _FT(), _FT(), _FT(), _FT()
        try:
            if not k.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(c),
                                     ctypes.byref(e), ctypes.byref(kt),
                                     ctypes.byref(u)):
                return 0
            return (int(c.hi) << 32) | int(c.lo)
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return 0


def _lock_pid_alive(lock: str) -> int:
    if not lock or not os.path.exists(lock):
        return 0
    try:
        raw = open(lock).read().strip()
        pid = int(raw)
        if pid <= 0 or not pid_alive(pid):
            return 0
        side = lock + ".json"
        if os.path.exists(side):
            try:
                import json

                obj = json.load(open(side, encoding="utf-8"))
                want = int(obj.get("ctime") or 0)
                got = _proc_creation_time(pid)
                if want and got and want != got:
                    return 0
            except Exception:
                return 0
        return pid
    except Exception:
        return 0


def read_guard_lock_pid_any() -> int:
    """迁移用：当前 guard 锁里的 PID 是否仍活（不依赖 CIM/命令行）。"""
    return _lock_pid_alive(guard_lock_path())


def read_legacy_guard_lock_pid_any(data_root: str = "") -> int:
    """旧外部 guard 的 out/guard.lock；高权 guard 的 CIM 空不代表死亡。"""
    script = os.environ.get("AELOC_LEGACY_GUARD_PATH", "").strip()
    if not os.path.isfile(script):
        return 0
    return _lock_pid_alive(os.path.join(os.path.dirname(script), "out", "guard.lock"))


def guard_locks_alive(data_root: str = "") -> int:
    return read_guard_lock_pid_any() or read_legacy_guard_lock_pid_any(data_root)


def pid_alive(pid: int) -> bool:
    """PID 是否对应活着的进程(SYNCHRONIZE 打开即可,不需完整权限)。"""
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        k.WaitForSingleObject.restype = ctypes.c_ulong
        h = k.OpenProcess(0x00100000, 0, int(pid))  # SYNCHRONIZE
        if not h:
            return False
        try:
            # OpenProcess 成功不代表进程未结束（句柄可指向已退出对象）。
            rc = int(k.WaitForSingleObject(ctypes.c_void_p(h), 0))
            return rc == 0x102  # WAIT_TIMEOUT -> alive
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return False


def guard_lock_path() -> str:
    """守护单实例锁(<hook>/out/guard.lock,§5.23)。找不到守护脚本返回空。"""
    gp, _why = find_guard()
    if not gp:
        return ""
    return os.path.join(os.path.dirname(gp), "out", "guard.lock")


_GUARD_ID_CACHE = None
_GUARD_ID_LOCK = __import__("threading").Lock()


def _clear_guard_identity_cache():
    global _GUARD_ID_CACHE
    with _GUARD_ID_LOCK:
        _GUARD_ID_CACHE = None


def _guard_side_identity(lock: str, pid: int):
    """Strong managed identity; malformed/legacy sidecar returns None."""
    try:
        import json

        side = lock + ".json"
        with open(side, encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return None
        if str(obj.get("protocol") or "") != "aeloc-managed-guard/1":
            return None
        if int(obj.get("pid") or 0) != int(pid):
            return None
        ctime = int(obj.get("ctime") or 0)
        token = str(obj.get("token") or "")
        image = str(obj.get("image") or "")
        if not ctime or not token or not image:
            return None
        return (os.path.normcase(os.path.abspath(lock)), int(pid), ctime,
                token, os.path.normcase(os.path.abspath(image)))
    except Exception:
        return None


def _guard_identity_fast(lock: str):
    """Revalidate positive identity without spawning PowerShell/CIM."""
    global _GUARD_ID_CACHE
    with _GUARD_ID_LOCK:
        cached = _GUARD_ID_CACHE
    if not cached:
        return 0
    try:
        raw = open(lock).read().strip()
        if not raw.isdigit():
            return 0
        pid = int(raw)
        if pid != cached[1] or os.path.normcase(os.path.abspath(lock)) != cached[0]:
            return 0
        ident = _guard_side_identity(lock, pid)
        if ident != cached:
            _clear_guard_identity_cache()
            return 0
        if not pid_alive(pid):
            _clear_guard_identity_cache()
            return 0
        if _proc_creation_time(pid) != cached[2]:
            _clear_guard_identity_cache()
            return 0
        return pid
    except Exception:
        _clear_guard_identity_cache()
        return 0


def _prime_guard_identity(lock: str, pid: int):
    ident = _guard_side_identity(lock, pid)
    if ident is None:
        return
    global _GUARD_ID_CACHE
    with _GUARD_ID_LOCK:
        _GUARD_ID_CACHE = ident


def read_guard_lock(stale_ok: bool = False) -> int:
    """单实例锁里的守护 PID;锁失效(过期/属主死/PID 复用)返回 0。

    ★ 2026-10-29:杀与停从此认锁不认名 —— 锁 PID 与进程镜像无关,
      打包版应用派生的守护(AeLocalizer.exe 镜像)也能精确看见。
    stale_ok=True 供“启动恢复”判断：锁文件时间旧但 PID 仍活，不要重复
    spawn 第二个 guard；普通停止/强杀仍用 stale_ok=False。
    """
    lock = guard_lock_path()
    if not lock or not os.path.exists(lock):
        return 0
    try:
        if not stale_ok and time.time() - os.path.getmtime(lock) > 100.0:
            _clear_guard_identity_cache()
            return 0
        # Positive managed identity fast path: same lock path/PID/ctime/token/
        # image and a live PID with the same creation time. No CIM spawn.
        pid_fast = _guard_identity_fast(lock)
        if pid_fast:
            return pid_fast
        raw = open(lock).read().strip()
        if not raw.isdigit():
            return 0
        pid = int(raw)
        if not pid_alive(pid):
            _clear_guard_identity_cache()
            return 0
        ident = _guard_side_identity(lock, pid)
        if ident is not None:
            got = _proc_creation_time(pid)
            if not got or got != ident[2]:
                _clear_guard_identity_cache()
                return 0
        if not pid_is_guard(pid):
            _clear_guard_identity_cache()
            return 0
        # Only a strong sidecar identity may prime the cache. Legacy or
        # malformed sidecars keep full conservative validation every time.
        _prime_guard_identity(lock, pid)
        return pid
    except Exception:
        _clear_guard_identity_cache()
        return 0


def pid_is_guard(pid: int) -> bool:
    """PID 复用防护:该 PID 命令行是否仍含 guard_uni。查不到按是(保守)。"""
    try:
        ps = ("(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine"
              % int(pid))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           timeout=15)
        return "guard_uni" in (r.stdout or "")
    except Exception:
        # 查不到身份就当不是 guard，宁可让上层去救，也不把复用 PID 当守护。
        return False


def guards_alive() -> bool:
    """是否存在活守护:单实例锁有效 或 命令行签名扫描命中。"""
    if read_guard_lock():
        return True
    return bool(guard_pids())


def guard_pids() -> list:
    """命令行含 guard_uni 的守护进程 PID 列表（PowerShell 读 CIM）——
    兜底扫描;主判据是单实例锁(read_guard_lock)。

    ★★ 2026-10-28 两次实测教训：
      ① 查询串自带 'guard_uni' 会把 PowerShell 查询进程自己匹配进来
        （排除 $PID 即可，不能靠镜像名过滤）；
      ② 只认 python.exe 镜像会把【打包版应用】派生的守护漏掉 ——
        PyInstaller 包里 sys.executable 是 AeLocalizer.exe，它 spawn 的
        守护镜像也是它，命令行照样含 guard_uni.py。漏掉的守护杀不掉、
        停不掉，AE 一启动就被注入 → frida-agent 崩溃循环。
      ③ 第三版（当前）：判据 = 命令行含真实 spawn 签名
        guard_uni.py + apply 参数。第三轮实测发现，仅匹配 'guard_uni'
        会把【开发机上恰好提到这段文本的 shell 会话】也误报（长命进程
        的命令行里嵌着历史命令文本）；真正的守护启动固定是
        「<解释器> guard_uni.py apply <秒>」，'apply' 参数是强特征。
    """
    import subprocess

    ps = ("Get-CimInstance Win32_Process | Where-Object { "
          "$_.ProcessId -ne $PID "
          "-and $_.CommandLine -ne $null "
          "-and $_.CommandLine -match 'guard_uni\\.py.{0,2}apply' } "
          "| ForEach-Object { $_.ProcessId }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           timeout=30)
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def force_kill_guards() -> int:
    """强杀所有 guard_uni 守护进程，返回杀掉的数量。

    ★ 2026-10-25 实测：STOP_NOW.flag 对卡死的守护不可靠（四个卡死守护
      只走了一个），所以启动前/止血时需要这层硬手段。
      需要足够权限（守护从管理员应用起出来就是管理员）。

    ★★ 2026-10-29:先杀单实例锁里的 PID(精确、与镜像名无关 —— 打包版
      守护的镜像是 AeLocalizer.exe,按名字扫会漏,六连崩事故的根因),
      再跑命令行签名扫描兜底;两者并集去重。
    """
    import subprocess

    targets = []
    lock_pid = read_guard_lock()
    if lock_pid:
        targets.append(lock_pid)
    for p in guard_pids():
        if p not in targets:
            targets.append(p)

    n = 0
    for p in targets:
        try:
            subprocess.run(["taskkill", "/PID", str(p), "/F", "/T"],
                           capture_output=True, text=True, encoding="gbk",
                           errors="replace", timeout=15)
            n += 1
        except Exception:
            pass
    return n


def stop_engine(timeout: float = 12.0) -> tuple:
    """Signal this profile's guard to restore native text and wait for its exit."""
    flag = os.path.join(ENGINE_DICT_DIR, STOP_FLAG)
    initial = _control_runtime_identity(control_status())
    expected = initial if _runtime_identity_alive(initial) else None
    isolated = bool(os.environ.get("AELOC_DATA_ROOT", "").strip())
    try:
        os.makedirs(ENGINE_DICT_DIR, exist_ok=True)
        with open(flag, "w", encoding="utf-8") as f:
            f.write("stop")
    except OSError as exc:
        return False, "无法写入停止标志：%s" % exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = control_status()
        identity = _control_runtime_identity(status)
        if _runtime_identity_alive(identity):
            expected = identity
        child = _STARTED_GUARD
        # The guard removes its lock just before returning. Keep waiting on the
        # process once its identity is known, including that last interval.
        if (not _runtime_identity_alive(expected)
                and not (child is not None and child.poll() is None)
                and not read_guard_lock_pid_any()
                and (isolated or not (guards_alive() or read_legacy_guard_lock_pid_any(DATA_ROOT)))):
            try:
                os.remove(flag)
            except OSError:
                pass
            if expected and (identity != expected or status.get("state") != "stopped"
                             or status.get("popup_restore_unknown")):
                return False, "引擎已退出，但未确认 AE 原文已恢复。"
            mark_stopped()
            return True, "引擎已停止"
        time.sleep(0.5)

    # 进程还在：守护卡死无视 flag（2026-10-25 实翻车形态）。
    # 返回失败由调用方决定「止血」；flag 留着，守护恢复轮询后仍会自己退出。
    return False, "%.0f 秒内未确认原文恢复和引擎退出，请稍候重试。" % timeout


def prepare_dict(
    entries: Dict[str, str],
    *,
    attribution: Optional[Dict[str, List[str]]] = None,
    selected_groups: Optional[Set[str]] = None,
    keep_unowned: bool = True,
    drop_keys: Optional[Set[str]] = None,
):
    """按勾选生成引擎词典目录。返回 enginedict.DictBuildResult。"""
    from .enginedict import build_engine_dict

    return build_engine_dict(
        entries,
        ENGINE_DICT_DIR,
        attribution=attribution,
        selected_groups=selected_groups,
        keep_unowned=keep_unowned,
        drop_keys=drop_keys,
    )


def control_dir() -> str:
    return os.path.join(DATA_ROOT, "guard_runtime", "control")


def send_control(mode: str, gen: int, host: dict, request_id: str = "") -> bool:
    """原子写 managed runtime request.json（不停止/不重启 guard）。"""
    if mode not in ("on", "off", "init", "stop"):
        return False
    try:
        import json
        import uuid

        d = control_dir()
        os.makedirs(d, exist_ok=True)
        req = {"protocol": "aeloc-managed-guard/1",
               "gen": int(gen), "request_id": request_id or uuid.uuid4().hex,
               "host": {"pid": int(host.get("pid") or 0),
                        "ctime": int(host.get("ctime") or 0),
                        "image": str(host.get("image") or "")},
               "mode": mode}
        import uuid

        tmp = os.path.join(d, "request.json.tmp.%s.%s"
                           % (os.getpid(), uuid.uuid4().hex))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for i in range(40):
            try:
                os.replace(tmp, os.path.join(d, "request.json"))
                return True
            except PermissionError:
                if i == 39:
                    raise
                import time as _t
                _t.sleep(0.02)
        return False
    except Exception:
        return False


def control_status() -> dict:
    try:
        import json

        with open(os.path.join(control_dir(), "status.json"), encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _control_runtime_identity(status):
    if status.get("protocol") != "aeloc-managed-guard/1":
        return None
    try:
        pid = int(status["runtime_pid"])
        ctime = int(status["runtime_ctime"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (pid, ctime) if pid > 0 and ctime > 0 else None


def _runtime_identity_alive(identity):
    return bool(identity and pid_alive(identity[0]) and _proc_creation_time(identity[0]) == identity[1])


def profile_runtime_confirmed(status=None) -> bool:
    """A current control receipt belongs to this data root and a live runtime."""
    if status is None:
        status = control_status()
    identity = _control_runtime_identity(status)
    if not identity or status.get("control_ready") is not True:
        return False
    try:
        age = time.time() - float(status["updated"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return 0 <= age <= 90 and _proc_creation_time(identity[0]) == identity[1]


def _profile_runtime_mutex_exists() -> bool:
    """The running service creates this mutex for its actual configuration directory."""
    import ctypes
    import hashlib
    from ctypes import wintypes

    profile_hash = hashlib.sha256(os.path.abspath(DATA_ROOT).encode("utf-8")).hexdigest()[:24]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenMutexW(0x00100000, False, "Global\\AeLocalizerManagedGuard-" + profile_hash)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def active_language_session():
    """Identify an already applied language service on the current desktop and profile."""
    from . import proc

    status = control_status()
    if (status.get("mode") != "on" or status.get("language_ready") is not True
            or not profile_runtime_confirmed(status)):
        return None
    try:
        generation = int(status["gen"])
        if generation <= 0 or status.get("applied_gen") != generation or status.get("ack_gen") != generation:
            return None
        pid = int(status["runtime_pid"])
        created = int(status["runtime_ctime"])
        lock = os.path.join(DATA_ROOT, "guard_runtime", "out", "guard.lock")
        identity = _guard_side_identity(lock, pid)
        if (not identity or identity[2] != created or not _profile_runtime_mutex_exists()
                or read_guard_lock(stale_ok=True) != pid):
            return None
        host = status["host"]
        hosts = proc.list_ae_hosts()
        if len(hosts) != 1:
            return None
        live = hosts[0]
        if (host["pid"] != live["pid"] or not host["ctime"] or host["ctime"] != live["creation_time"]
                or os.path.normcase(host["image"]) != os.path.normcase(live["image"])
                or not proc.main_window(live["pid"])):
            return None
        return {"runtime_pid": pid, "runtime_ctime": created, "host_pid": live["pid"],
                "host_ctime": live["creation_time"], "host_image": os.path.normcase(live["image"]),
                "gen": generation}
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


def background_language_session():
    """Recognise a deliberate UI close only while that very same language session is active."""
    from . import settings

    config = settings.load(DATA_ROOT)
    marker = config.get("background_language_session")
    if (not isinstance(marker, dict) or config.get("engine_master_intent") is False
            or config.get("language_intent") is False):
        return None
    current = active_language_session()
    return current if current is not None and current == marker else None


def background_on_ae_startup():
    from . import settings
    config = settings.load(DATA_ROOT)
    return (config.get("background_on_ae_startup") is True
            and config.get("engine_master_intent") is not False)


def request_foreground_window():
    from . import settings
    if settings.get(DATA_ROOT, "background_on_ae_startup", False):
        settings.put(DATA_ROOT, "background_on_ae_startup", False)


def request_reload() -> bool:
    """让运行中的受控 guard 热换 engine_dict（不停止/重启）。"""
    out = collect_dir()
    if not out:
        return False
    try:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "RELOAD_DICT.flag"), "w",
                  encoding="utf-8") as f:
            f.write(str(time.time_ns()))
        return True
    except OSError:
        return False
