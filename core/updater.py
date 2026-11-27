"""手动检查 GitHub Releases，下载、校验并安装更新包。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

UA = "AeLocalizer-Updater"
CHUNK = 64 * 1024
_GITHUB_REPOSITORY = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9_.-]+", re.IGNORECASE | re.ASCII)
_GITHUB_REPOSITORY_URL = re.compile(
    r"https://github\.com/([^/]+/[^/]+)(?:/releases(?:/latest)?)?", re.IGNORECASE)


def _open(url: str, timeout: float, direct: bool):
    """打开一个 URL。direct=True 时无视一切代理直连。"""
    if direct:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return opener.open(req, timeout=timeout)


def _read_bytes(url: str, timeout: float, direct: bool = False) -> bytes:
    """默认先走系统代理，失败直连兜底；direct=True 只直连（测试/localhost 用）。"""
    if direct:
        with _open(url, timeout, direct=True) as resp:
            return resp.read()
    try:
        with _open(url, timeout, direct=False) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError):
        # 第二次也失败就让它抛 —— 两条路都不通是真的不通
        # （urllib.error 没有 ProxyError；代理失败统一表现为 URLError，
        #   而 URLError 本身继承 OSError。）
        with _open(url, timeout, direct=True) as resp:
            return resp.read()


def fetch_manifest(url: str, timeout: float = 8.0,
                   direct: bool = False) -> Dict[str, object]:
    """拉版本清单并做最基本校验。坏清单/网络不通直接抛异常。"""
    raw = _read_bytes(url, timeout, direct=direct)
    obj = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(obj, dict):
        raise ValueError("清单不是 JSON 对象")
    ver = str(obj.get("version") or "").strip()
    if not ver:
        raise ValueError("清单缺 version 字段")
    obj["version"] = ver
    return obj


def compare_versions(a: str, b: str) -> int:
    """比版本号：a<b 返回 -1，相等 0，a>b 返回 1。

    按点分段取数字比（"1.10" > "1.9"），容忍前缀 v/V 和尾部
    非数字段（"1.0.0-beta" 当 1.0.0，末段取开头的数字）。
    """
    def parts(v: str):
        v = v.strip().lstrip("vV")
        out = []
        for seg in v.split("."):
            m = re.match(r"(\d+)", seg.strip())
            out.append(int(m.group(1)) if m else 0)
        return out or [0]

    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def check(app_version: str, manifest: Dict[str, object]) -> Dict[str, object]:
    """纯逻辑：拿清单跟本地版本比，给设置页一个可显示的结论。"""
    latest = str(manifest.get("version") or "").strip()
    if not latest:
        raise ValueError("清单缺 version 字段")
    has_update = compare_versions(latest, app_version) > 0
    return {
        "has_update": has_update,
        "latest": latest,
        "download_url": str(manifest.get("url") or ""),
        "sha256": str(manifest.get("sha256") or "").strip().lower(),
        "notes": str(manifest.get("notes") or ""),
    }


def normalize_github_repository(value: str) -> str:
    value = value.strip().removesuffix("/")
    if not value:
        return ""
    address = _GITHUB_REPOSITORY_URL.fullmatch(value)
    repository = address[1] if address else value
    if address and repository.lower().endswith(".git"):
        repository = repository[:-4]
    if (_GITHUB_REPOSITORY.fullmatch(repository)
            and repository.split("/")[1] not in (".", "..")):
        return repository
    raise ValueError("请输入 GitHub 仓库地址或 owner/repo")


def check_github_release(repository: str, app_version: str,
                         timeout: float = 8.0) -> Dict[str, object]:
    repository = normalize_github_repository(repository)
    if not repository:
        raise ValueError("未配置 GitHub 仓库")
    endpoint = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        release = json.loads(_read_bytes(endpoint, timeout))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("未找到公开的正式版本，请检查仓库地址及 Releases") from exc
        raise
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise ValueError("GitHub 未返回正式版本")

    tag = str(release.get("tag_name") or "")
    if not re.fullmatch(r"[vV]?[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("GitHub 版本标签格式无效，应为 vX.Y.Z")
    version = tag.lstrip("vV")
    result = check(app_version, {"version": version})
    if not result["has_update"]:
        return result

    name = f"AeLocalizer-{version}-win64.zip"
    assets = release.get("assets")
    if not isinstance(assets, list):
        assets = []
    asset = next((entry for entry in assets
                  if isinstance(entry, dict) and entry.get("name") == name
                  and entry.get("state") == "uploaded"), None)
    if asset is None:
        raise ValueError(f"v{version} 缺少 Windows x64 更新包 {name}")
    digest = re.fullmatch(r"sha256:([a-f0-9]{64})", str(asset.get("digest") or ""), re.IGNORECASE)
    if not digest:
        raise ValueError(f"v{version} 的更新包缺少有效的 SHA-256 摘要")
    url = str(asset.get("browser_download_url") or "")
    prefix = f"https://github.com/{repository}/"
    resource = f"releases/download/{tag}/{name}"
    if url[:len(prefix)].lower() != prefix.lower() or url[len(prefix):] != resource:
        raise ValueError("GitHub 更新包的下载地址无效")
    result.update(download_url=url, sha256=digest[1].lower())
    return result


def download(url: str, dest_dir: str, sha256: str = "",
             progress: Optional[Callable[[int, int], None]] = None,
             timeout: float = 30.0, direct: bool = False) -> str:
    """下载更新包到 dest_dir，边下边算哈希，校验通过才落正式文件名。

    先写 .part 临时文件，全部通过后 os.replace —— 中途断网/哈希不符
    都不会留下一个「看起来下载完了」的坏包。
    返回最终文件完整路径。校验失败抛 ValueError。
    """
    os.makedirs(dest_dir, exist_ok=True)
    # 从 URL 取文件名；取不到就给个固定的
    name = os.path.basename(urllib.request.urlparse(url).path or "") or "aeloc-update.bin"
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "aeloc-update.bin"
    final = os.path.join(dest_dir, name)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    os.close(fd)
    h = hashlib.sha256()
    total = 0
    try:
        try:
            resp = _open(url, timeout, direct=False) if not direct else _open(url, timeout, direct=True)
        except (urllib.error.URLError, OSError):
            resp = _open(url, timeout, direct=True)
        with resp:
            expect_total = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    total += len(chunk)
                    if progress:
                        pct = int(total * 100 / expect_total) if expect_total else -1
                        try:
                            progress(total, pct)
                        except Exception:
                            pass
        if sha256:
            got = h.hexdigest()
            if got != sha256.strip().lower():
                raise ValueError("更新包校验不符（sha256 %s…）" % got[:12])
        os.replace(tmp, final)
        return final
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def open_folder(path: str) -> bool:
    """在资源管理器里打开目录（Windows 专用工具，失败返回 False）。"""
    try:
        if not os.path.isdir(path):
            return False
        os.startfile(path)  # noqa: S606 —— Windows 惯用
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 自动更新（2026-11-19）
#
# 为什么需要脚本：[程序无法覆盖正在运行的自己] —— 新包必须等主进程退出后再落地。
# 用 PowerShell 而不是 .bat：包/程序目录可能含中文路径，.bat 受代码页影响，
# PS 按 UTF-8 读脚本更稳。
#
# 流程：等 PID 退出 → 整个程序目录改名为 <dir>.old-时间戳（瞬时，不复制 700MB）
#      → 解压新包到原路径 → 启动新版 → 删除备份
#      任一步失败 → 回滚（改回原名）
# ---------------------------------------------------------------------------

def _ps_quote(value: str) -> str:
    """PowerShell 单引号字符串：内部单引号翻倍。"""
    return "'" + str(value).replace("'", "''") + "'"


def can_self_update(app_dir: str) -> bool:
    """程序目录是否可写（装在 Program Files 下就不可写，需要换便携目录）。"""
    try:
        if not app_dir or not os.path.isdir(app_dir):
            return False
        probe = os.path.join(app_dir, ".aeloc_write_test")
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def user_install_dir() -> str:
    """免管理员写入的安装位置：%LOCALAPPDATA%\\Programs\\AeLocalizer。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", "AeLocalizer")


def build_update_script(pid: int, app_dir: str, zip_path: str, log_path: str,
                        exe_name: str = "AeLocalizer.exe",
                        in_place: bool = True, target_dir: str = "") -> str:
    """生成"等退出→改名备份→解压→重启→清理"的 PowerShell 脚本。

    纯函数（不落盘、不执行），便于单测；路径全部用单引号包裹并转义。
    """
    app_dir = os.path.abspath(app_dir)
    zip_path = os.path.abspath(zip_path)
    log_path = os.path.abspath(log_path)
    # in_place=False：原目录不可写（装在 Program Files），改用用户目录装新版本，
    # 这样**完全不需要管理员**；旧目录原样留着，用户自己删即可。
    target_dir = os.path.abspath(target_dir) if (not in_place and target_dir) else app_dir
    return """$ErrorActionPreference = 'Stop'
$TargetPid = {pid}
$AppDir = {app_dir}
$TargetDir = {target_dir}
$InPlace = {in_place}
$Zip = {zip_path}
$Log = {log_path}
$ExeName = {exe_name}
$Bak = "$TargetDir.old-" + [Guid]::NewGuid().ToString('N')
$BackedUp = $false
$TargetCreated = $false

# Windows 无法重命名进程自己的当前目录。更新脚本若继承旧程序目录，
# Rename-Item 会失败并回滚。先切到临时目录，再替换程序文件。
Set-Location -LiteralPath $env:TEMP

function Log($m) {{
    try {{ Add-Content -LiteralPath $Log -Value ("[" + (Get-Date -Format 'HH:mm:ss') + "] " + $m) -Encoding UTF8 }} catch {{}}
}}

Log "=== 自动更新开始 pid=$TargetPid 目录=$AppDir 包=$Zip"
try {{
    if (-not (Test-Path -LiteralPath $Zip)) {{ throw "更新包不存在：$Zip" }}

    Log "等待主进程退出"
    for ($i = 0; $i -lt 240; $i++) {{
        $p = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
        if (-not $p) {{ break }}
        Start-Sleep -Milliseconds 500
    }}
    Start-Sleep -Milliseconds 800
    if (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue) {{ throw "主进程 120 秒后仍在运行，放弃替换" }}

    if (-not $InPlace) {{
        Log "原目录不可写，改装到用户目录：$TargetDir（无需管理员）"
    }}
    if (Test-Path -LiteralPath $TargetDir) {{
        Log "备份：$TargetDir -> $Bak"
        for ($r = 1; $r -le 10; $r++) {{
            try {{
                Rename-Item -LiteralPath $TargetDir -NewName (Split-Path -Leaf $Bak)
                $BackedUp = $true
                break
            }}
            catch {{
                if ($r -eq 10) {{ throw }}
                Log "改名重试 $r（$($_.Exception.Message)）"
                Start-Sleep -Milliseconds 400
            }}
        }}
    }}

    Log "解压新版本"
    [IO.Directory]::CreateDirectory((Split-Path -Parent $TargetDir)) | Out-Null
    New-Item -ItemType Directory -Path $TargetDir | Out-Null
    $TargetCreated = $true
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($tar) {{
        & tar.exe -xf $Zip -C $TargetDir
        if ($LASTEXITCODE -ne 0) {{ throw "tar 解压失败（$LASTEXITCODE）" }}
    }} else {{
        Expand-Archive -LiteralPath $Zip -DestinationPath $TargetDir -Force
    }}

    $exe = Join-Path $TargetDir $ExeName
    if (-not (Test-Path -LiteralPath $exe)) {{ throw "解压后找不到 $ExeName" }}

    Log "启动新版本：$exe"
    Start-Process -FilePath $exe -WorkingDirectory $TargetDir

    Log "更新完成，清理备份"
    Start-Sleep -Seconds 2
    if ($BackedUp) {{
        try {{ Remove-Item -LiteralPath $Bak -Recurse -Force }}
        catch {{ Log "清理备份失败：$Bak（$($_.Exception.Message)）" }}
    }}
    Log "=== 成功"
}} catch {{
    Log ("失败：" + $_.Exception.Message)
    try {{
        $rollback = $TargetDir
        if ($TargetCreated) {{ Remove-Item -LiteralPath $rollback -Recurse -Force }}
        if ($BackedUp) {{
            Log "回滚"
            Rename-Item -LiteralPath $Bak -NewName (Split-Path -Leaf $rollback)
        }}
        Log "=== 失败，原安装可用"
    }} catch {{
        Log "=== 回滚失败：$($_.Exception.Message)，备份路径：$Bak"
    }}
    exit 1
}}
""".format(pid=int(pid), app_dir=_ps_quote(app_dir), zip_path=_ps_quote(zip_path),
           log_path=_ps_quote(log_path), exe_name=_ps_quote(exe_name),
           target_dir=_ps_quote(target_dir),
           in_place=("$true" if in_place else "$false"))
