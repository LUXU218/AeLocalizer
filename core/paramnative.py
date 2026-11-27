"""参数名原生辅助通路(方案 D)的请求执行器。

把请求嵌入 jsx/parameter_bridge.jsx(@@REQUEST@@ 占位符),经
AfterFX.exe -r 转交给 AE 自己执行;AE 内脚本先校验进程身份(pid/创建
时间/路径,经 ae_host_identity.dll)。读取参数时由脚本直接读取工程；
写参数名时加载 ae_parameters.dll 写 PF 内联名字缓冲 + TDB SetDisplayName。
本模块只负责:构造请求、转交、
轮询结果、清理;写入本身的安全校验在辅助 DLL 里(按 FLT/TDB/U 三个
SHA-256 自查,不符即拒绝)。

移植自 After-Effects-Plugin-Localizer 的 run_native(MIT,
Copyright (c) 2026 verse),精简为 -r 转交单通道,CEP 后台桥待后续接入。

★ 已知副作用(上游同款):-r 转交会让 AE 主窗口恢复普通尺寸(WindowKeeper
  的自动恢复属于"自动行为",按用户偏好不做;窗口状态由用户自己调回)。
★ AE 首选项「允许脚本写入文件和访问网络」必须勾选,否则结果文件写不出来。
"""
from __future__ import annotations

import ctypes as C
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

STARTED = "started"
STAGES = {
    "1": ("进入脚本", ""),
    "start": ("进入脚本", ""),
    "instances": ("读取效果实例", ""),
    "collect": ("读取参数列表", ""),
    "loadHelper": ("加载参数辅助模块", "安全软件(360、火绒等)可能拦截了这个 DLL 的加载,请留意提示并允许;"),
    "configure": ("核对 AE 模块与名称编码", "这个 AE 版本可能与参数辅助模块不兼容;"),
    "labels": ("写入参数名称", ""),
    "result": ("写回结果", ""),
}

MISMATCH_TEXT = (
    "AE 与本程序的管理员权限不同,Windows 可能拦截脚本转交。"
    "请让两者用相同权限运行(建议都不要「以管理员身份运行」)后重试"
)


def native_root() -> Path:
    return Path(__file__).resolve().parent.parent / "native"


def jsx_root() -> Path:
    return Path(__file__).resolve().parent.parent / "jsx"


def bridge_root() -> Path:
    """请求目录:数据根下,程序与 AE 都可见。"""
    return Path(data_root()) / "ae-bridge"


_DATA_ROOT_OVERRIDE: list = []


def set_data_root(path) -> None:
    """调用方(应用/测试)显式注入数据根 —— 2026-11-01 解耦:core 不再
    主动反向 import app.backend(依赖方向单一化,接线由调用方完成)。"""
    _DATA_ROOT_OVERRIDE.clear()
    if path:
        _DATA_ROOT_OVERRIDE.append(str(path))


def data_root() -> Path:
    if _DATA_ROOT_OVERRIDE:
        return Path(_DATA_ROOT_OVERRIDE[0])
    env = os.environ.get("AELOC_DATA_ROOT", "").strip()
    if env:
        return Path(env)
    from app.backend import DATA_ROOT  # 兜底:未注入的旧调用路径维持旧行为

    return Path(DATA_ROOT)


def _short_path(path: Path) -> str:
    try:
        buffer = ctypes_wchar_buffer(32768)
        if C.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return str(path)


def ctypes_wchar_buffer(size: int):
    return C.create_unicode_buffer(size)


def relay_script(folder: Path) -> Path:
    """AE 对含空格的脚本路径会静默不执行(AE 2024 实测一个空格就够):
    优先用请求目录本身或其 8.3 形式,否则找第一个无空格的可写目录。"""
    for base in (folder, Path(_short_path(folder))):
        if " " not in str(base):
            return base / "request.jsx"
    candidates = [lambda: data_root() / "relay"]
    candidates += [
        lambda name=name: Path(os.environ[name]) / "AeLocalizer" / "relay"
        for name in ("TEMP", "PUBLIC", "ProgramData")
        if os.environ.get(name)
    ]
    for candidate in candidates:
        try:
            base = candidate()
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        for form in (str(base), _short_path(base)):
            if " " not in form:
                return Path(form) / f"{folder.name}.jsx"
    raise RuntimeError(
        "软件或数据目录路径含空格,AE 不会执行转交的脚本,且找不到无空格的可写目录。"
        "请把数据目录放到不含空格的路径"
    )


def elevated(pid: int) -> bool | None:
    from .proc import elevated as _elevated

    return _elevated(pid)


def target(process) -> dict:
    """进程身份(FILETIME 十六进制,绝不用 JS 会丢精度的数字)。"""
    creation = getattr(process, "creation_time", None)
    if not isinstance(creation, int):
        raise RuntimeError("无法确认 AE 进程创建时间,请重新连接")
    return {
        "pid": process.pid,
        "creationTime": f"{creation:016x}",
        "image": os.path.normcase(os.path.abspath(process.image)).replace("\\", "/"),
    }


def _valid_identity(value) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("pid")) is int
        and value["pid"] > 0
        and isinstance(value.get("creationTime"), str)
        and bool(re.fullmatch("[0-9a-f]{16}", value["creationTime"]))
        and isinstance(value.get("image"), str)
        and Path(value["image"]).name.casefold() == "afterfx.exe"
    )


def same_identity(actual, expected) -> bool:
    return (
        _valid_identity(actual)
        and _valid_identity(expected)
        and actual["pid"] == expected["pid"]
        and actual["creationTime"] == expected["creationTime"]
        and actual["image"].replace("\\", "/").casefold() == expected["image"].replace("\\", "/").casefold()
    )


def host_version_of(profile) -> str:
    """脚本桥要的 app.version 口径("26.5x89");profile 没存就按构建号推导。
    ★ encoder.py 真机实测:AE 26.5.0.89 的 app.version = "26.5x89"。"""
    if getattr(profile, "host_version", ""):
        return profile.host_version
    parts = (profile.version or "").split(".")
    if len(parts) == 4:
        return "%s.%sx%s" % (parts[0], parts[1], parts[3])
    return ""


def native_layout(profile) -> str:
    """动态适配器布局串:根虚表,组向量,命名容器,容器类型(0 表/1 向量/2 树),
    parent/stream_match/count 三个 getter,十六进制。"""
    kind = 2 if getattr(profile, "named_tree", False) else int(bool(profile.named_vector))
    return ",".join(
        format(v, "x")
        for v in (profile.root_vtable, profile.group_vector, profile.named_map, kind,
                  profile.parent_get, profile.stream_match_get, profile.count_get)
    )


def safe_unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def run_native(p, operations: list, *, action: str = "labels", timeout: int = 30,
               profile=None, data_root=None, project_token: str | None = None,
               read_scope: str | None = None) -> dict:
    """执行一次参数请求;返回 jsx 写回的结果 dict。

    ★ labels 的 operation 字段约定(2026-10-28 实测,ae_parameters.c:291):
      全部为【字符串】 —— address/parent 是十六进制串("%X"),guid 是 32 位
      hex 串,before/after 是普通字符串。JS 传数字会直接
      "ReferenceError: 对象无效"(TaggedData 类型不符)。
      address/guid/parent 由方案 B(paramcatalog.rows)提供。
    """
    if action not in ("labels", "codec", "catalog", "selection"):
        raise ValueError("未知的参数请求动作:" + action)
    expected = target(p)
    identity_helper = native_root() / "ae_host_identity.dll"
    if not identity_helper.is_file():
        raise RuntimeError("宿主身份模块缺失，请检查便携包是否已完整解压")
    helper = native_root() / "ae_parameters.dll" if action in ("labels", "codec") else None
    if helper is not None and not helper.is_file():
        raise RuntimeError("参数辅助模块缺失:请运行 tools/build_native.py 构建")

    root = Path(data_root) / "ae-bridge" if data_root else bridge_root()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / uuid.uuid4().hex
    folder.mkdir(parents=True)
    output = folder / "result.json"
    permit = folder / "permit"
    script = None
    launcher = None
    try:
        permit.write_text("pending", encoding="ascii")
        request = {
            "targetHost": expected,
            "identityHelper": identity_helper.as_posix(),
            "action": action,
            "requestId": folder.name,
            "hostVersion": host_version_of(profile) if profile else None,
            "projectToken": project_token,
            "operations": operations,
            "output": output.as_posix(),
            "permit": permit.as_posix(),
            "expires": int((time.time() + timeout - 2) * 1000),
        }
        if read_scope:
            request["readScope"] = read_scope
        if helper is not None:
            request["helper"] = helper.as_posix()
            if profile is not None:
                request["adapter"] = {
                    "flt": profile.flt_sha,
                    "tdb": profile.tdb_sha,
                    "codec": profile.codec_sha,
                    "layout": native_layout(profile),
                    "encoding": profile.encoding,
                }

        script = relay_script(folder)
        template = (jsx_root() / "parameter_bridge.jsx").read_text(encoding="utf8")
        # AE 的命令行脚本加载器可能按系统旧编码解读非 ASCII 源码。
        source = template.replace("@@REQUEST@@", json.dumps(request, ensure_ascii=True))
        source = "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in source)
        script.write_text(source, encoding="ascii")

        launcher = subprocess.Popen(
            [p.image, "-r", str(script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if output.exists():
                try:
                    result = json.loads(output.read_text(encoding="utf-8-sig"))
                    if result.get("requestId") != folder.name or result.get("action") != action:
                        raise RuntimeError("AE 响应的请求身份不匹配,未接受结果")
                    if not same_identity(result.get("hostIdentity"), expected):
                        raise RuntimeError("AE 响应的宿主身份不匹配,未接受结果")
                    return result
                except ValueError:
                    pass  # 半个文件,继续等
            time.sleep(0.1)
        stage = _stage(folder)
        step, hint = STAGES.get(stage, ("未知步骤" if stage else "AE 没有进入脚本", ""))
        mismatch = "" if stage else _elevation_hint(p.pid)
        cause = f"AE 已开始执行脚本但 {timeout} 秒内没有完成,停在「{step}」:{hint}" if stage else (
            "AE 没有执行转交的脚本:" + mismatch
            if mismatch
            else "AE 没有执行转交的脚本:请关闭 AE 中可能弹出的对话框,并确认"
                 "「编辑 → 首选项 → 脚本和表达式 → 允许脚本写入文件和访问网络」已勾选"
        )
        raise TimeoutError("AE 参数操作超时,已撤销执行许可。" + cause)
    finally:
        safe_unlink(permit)
        if launcher is not None and launcher.pid != p.pid and launcher.poll() is None:
            try:
                launcher.terminate()
                launcher.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if script is not None:
            safe_unlink(script)
        for owned in (folder / STARTED, output):
            safe_unlink(owned)
        try:
            folder.rmdir()
        except OSError:
            pass


def _stage(folder: Path) -> str | None:
    try:
        return (folder / STARTED).read_text(encoding="ascii", errors="replace").strip()[:32] or "start"
    except OSError:
        return None


def _elevation_hint(pid: int) -> str:
    from .proc import elevation_mismatch

    return elevation_mismatch(pid) or MISMATCH_TEXT


def probe_catalog(p, timeout: float = 120.0, *, expected_version: str = "") -> list:
    """跑 catalog_probe.jsx:让 AE 自报 app.effects 全目录(displayName/
    matchName/category),供「自动识别与适配」的 registry_layout 交叉校验
    当对照面。只读、不碰工程。返回 catalog 行列表;超时/为空抛 RuntimeError。

    ★ 2026-10-28 接线:脚本一直有,驱动一直缺 —— §8.6 的
      「静态候选 → 探针 → layout 校验 → save_local(enabled=True)」
      现在由 backend.adapterFit 串起来。
    """
    root = Path(data_root()) / "ae-bridge"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / uuid.uuid4().hex
    folder.mkdir(parents=True)
    output = folder / "probe.json"
    script = None
    launcher = None
    try:
        src = (jsx_root() / "catalog_probe.jsx").read_text(encoding="utf8")
        # ★ 占位是 var outputPath = @@OUTPUT@@; —— 替换值必须带引号,
        #   裸路径会让 JSX 语法错误、脚本静默失败(实测 150s 超时)
        src = src.replace("@@OUTPUT@@", '"' + output.as_posix() + '"')
        src = "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in src)
        script = relay_script(folder)
        Path(script).write_text(src, encoding="ascii")
        launcher = subprocess.Popen(
            [p.image, "-r", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if output.exists():
                try:
                    report = json.loads(output.read_text(encoding="utf-8-sig"))
                except ValueError:
                    time.sleep(0.2)
                    continue
                if report.get("error"):
                    raise RuntimeError("目录探针在 AE 内报错:" + str(report["error"])[:120])
                if expected_version and report.get("aeVersion") != expected_version:
                    raise RuntimeError("目录探针返回的 AE 构建与当前编辑会话不一致")
                catalog = report.get("catalog") or []
                if not catalog:
                    raise RuntimeError("目录探针返回空目录(app.effects 为空?)")
                return catalog
            time.sleep(0.3)
        raise RuntimeError("目录探针 %.0f 秒没有返回结果(AE 首选项「允许脚本写入"
                           "文件和访问网络」是否勾选?)" % timeout)
    finally:
        if script is not None:
            safe_unlink(script)
        safe_unlink(output)
        try:
            folder.rmdir()
        except OSError:
            pass
        if launcher is not None and launcher.pid != p.pid:
            try:
                # A relay can keep an AE window briefly after writing its reply.
                # Rechecking the editor while that window exists sees two hosts.
                launcher.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    launcher.terminate()
                except OSError:
                    if launcher.poll() is None:
                        raise
                launcher.wait(timeout=5)


def split_packed(operations: list, module_of) -> tuple:
    """按「目标模块是否 packed」把操作分成可写/跳过两堆。

    姊妹项目 ae-i18n 1.1.0 实测（core/protect.packed 的清单来源）：对 packed
    插件写 pd->name 会令 addProperty 失败 —— 它们的名字只能采集不能写。
    module_of(op) 返回该操作目标所属模块的路径或文件名（未知返回 None，
    未知目标放行：宁缺毋滥只对「确认 packed」动手拦）。
    返回 (allowed, skipped)。
    """
    from .protect import packed as _packed

    allowed, skipped = [], []
    for op in operations:
        mod = module_of(op)
        if mod and _packed(str(mod)):
            skipped.append(op)
        else:
            allowed.append(op)
    return allowed, skipped
