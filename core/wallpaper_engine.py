"""Wallpaper Engine 壁纸接入：安装探测、清单枚举、画面提取（2026-11-16）。

用户在设置页里选一张壁纸引擎的壁纸当背景。一类是视频壁纸（mp4 直接可播），
另一类是 scene 场景包（scene.pkg）—— 后者要拆容器才能拿到画面。

★★ 为什么这里要自己实现 PKG 解析：
   scene.pkg 是 Wallpaper Engine 的私有容器（虽然未加密）。没有现成 Python
   依赖，且本项目的纪律是「不引入无必要的第三方库」，所以按公开格式自己读。
   格式事实与两个参考实现交叉核对过：
     · RePKG           (github.com/notscuffed/repkg)
     · linux-wallpaperengine (github.com/Almamu/linux-wallpaperengine)
   另有一份同源的 TypeScript 实现 dsh-plugin-wallpaper-engine（本项目参考对象）。

★ PKG 容器格式：
    int32 长度 + "PKGVxxxx" 魔数
    int32 条目数
    每条目：int32 长度 + 路径, uint32 offset, uint32 length（offset 相对索引末尾）
  条目数据可能被 LZ4 块链压缩：
    u64 原始大小 + 重复的 [int32 解压长][int32 压缩长][LZ4 块]

★★ 压缩判定必须走**完整链校验**（`_probe_compressed`）：
   「原始大小 > 存储大小」这个粗判会大面积误判 —— 未压缩数据的前 8 字节是纹理
   内容，随手就是一个大得多的整数。链校验要求链**恰好**重建 originalSize 字节
   并**恰好**用完条目，实测本机 8 个 scene 全部为原始存储（误判率若用粗判会是 100%）。

★ TEX 纹理格式（`_tex_payload`）：
    "TEXV0005\\0" + "TEXI0001\\0" + 6 个 int32（格式/标志/纹理宽高/图像宽高）
    + TEXB000x\\0 + 容器体
  多数中文壁纸作者用的 scene 里，主纹理的 TEXB 容器直接装着**完整的 JPEG 或 PNG**
  （FreeImage 格式号 0 = 未知，即「不压缩，就是一张图」），把它整段切出来即可，
  不需要解码 DXT/BC 像素。实测本机 4/8 个 scene 属于这一类，且是全尺寸
  （4080x2480 / 3440x1440 / 2560x1440 / 8192x4832）。

★ 有些 scene 的主纹理里嵌的是 **MP4**（作者把视频塞进 .tex）：
  在条目开头 200 字节内找 'ftyp'（66 74 79 70），命中则 box 起点 = i-4，
  从那里切片就是一段标准 MP4。实测本机 3/8 个 scene 属于这一类。

★ 安全纪律（参考项目踩过的坑，第三方 project.json 是**不可信输入**）：
  `file` / `preview` 是上传者随便写的字符串，可能是 `..\\..\\Windows\\...`。
  枚举时一律做**目录包含校验**，越界的 `file` 整条丢弃，越界的 `preview` 只丢预览。

不做的事：不渲染 3D 场景（那需要完整 WebGL 引擎），不写用户的壁纸文件。
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: Wallpaper Engine 在 Steam 的 AppID —— 工坊内容目录就挂在它下面。
WE_APPID = "431960"

#: 单条目解压上限（工坊文件不可信，不能让一个坏包逼出几十 GB 分配）。
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
#: 单条目原始字节上限（同上）。
MAX_PKG_ENTRY_BYTES = 512 * 1024 * 1024

_PKG_ENTRY_FLAG_LZ4 = 1

#: 画面最多取到这么多像素宽度（背景图用不着 8K，缩放省内存和磁盘）。
MAX_WIDTH = 3840

#: 判定「这条纹理不是主画面」的关键词。
#:
#: ★★ 只收**材质通道**类词（法线/遮罩/光照/粗糙度…）—— 它们表示「这张图是某个
#:    材质的一个通道」，而不是壁纸画面本身。
#: ★ 刻意**不收** "vortex" "combined" "neon" "razer" "wave" "glow" 这类词：
#:    壁纸作者经常把自己的画面直接命名成这些（实测 Razer Bedroom 的主图就叫
#:    `new_wall_combined4k.tex`、Razer Vortex 的主图叫 `vortex.tex`），
#:    按参考实现那份更宽的黑名单会把这些真画面挡掉、退回缩略图封面。
#:    歧义词交给「先排除辅助纹理、再取最大载荷」的排序去解决。
_AUX_TEX_KEYWORDS = (
    "normal", "_n.", "mask", "lightmap", "light_map", "heightmap",
    "roughness", "metalness", "specular", "emission", "_ao", "ao_",
    "noise", "lut", "flowmap", "_flow", "waterripple", "ripple_mask",
    "cursor", "qrcode", "qr_code", "sponsor", "donate", "watermark",
    "logo", "avatar", "画师头像", "赞助", "提示框",
    "audio_bar", "audiobar", "simple_audio", "_font", "particle",
)

#: 图片类壁纸的扩展名（WE 的 image 类型：单图 / 幻灯片）。
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_VIDEO_EXT = (".mp4", ".webm", ".mkv", ".avi", ".mov")


@dataclass
class Wallpaper:
    """一张壁纸的清单条目（不含画面数据，画面按需提取）。"""

    id: str                 # 项目目录名（工坊 id 或自建项目名）
    title: str
    kind: str               # scene | video | web | image | application
    main: str               # 主文件（相对 project.json 所在目录）
    dir: str                # 项目目录绝对路径
    preview: str = ""       # 预览图（绝对路径，可能为空）
    source: str = "workshop"  # workshop | local
    content_rating: str = ""

    @property
    def main_abs(self) -> str:
        return os.path.join(self.dir, self.main) if self.main else ""


@dataclass
class Extracted:
    """一张壁纸提取出来的画面。"""

    path: str               # 落地文件（缓存里的图或视频）
    kind: str               # image | video
    width: int = 0
    height: int = 0
    #: True = 没能拿到真实画面，退回了封面图（缩略图级质量）。界面上要说清楚。
    from_preview: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)


# ---------------------------------------------------------------- 安装探测


def _reg_steam_path() -> str:
    """从注册表读 Steam 安装目录。用 winreg，不调 reg.exe（不弹控制台）。"""
    try:
        import winreg
    except ImportError:          # 非 Windows
        return ""
    for root, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(root, key) as k:
                v, _ = winreg.QueryValueEx(k, value)
                if v and os.path.isdir(v):
                    return os.path.normpath(v)
        except OSError:
            continue
    return ""


def _vdf_library_paths(vdf_path: str) -> List[str]:
    """解析 steamapps/libraryfolders.vdf，取出所有库目录。

    ★ 只认「含 431960 工坊目录」或本身就是 Steam 根：库文件里可能记录着早已删掉的盘符。
    """
    try:
        with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('"path"'):
            continue
        parts = s.split('"')
        if len(parts) >= 4:
            p = parts[3].replace("\\\\", "\\")
            if os.path.isdir(p):
                out.append(os.path.normpath(p))
    return out


def _is_we_root(path: str) -> bool:
    """这个目录是不是 Wallpaper Engine 的安装根。

    ★ 老版把 exe 放根目录，新版挪进 `distribution\\`（根目录只剩 launcher.exe）。
      所以在 `distribution\\` 里也放一个标记 —— 否则新装会被判成「没安装」，
      而这不是降级而是**消失**：projects/ 扫描和工坊归属都挂在安装根上。
    """
    markers = (
        "wallpaper32.exe", "wallpaper64.exe",
        os.path.join("distribution", "wallpaper32.exe"),
        os.path.join("distribution", "wallpaper64.exe"),
        os.path.join("distribution", "version.json"),
    )
    return any(os.path.isfile(os.path.join(path, m)) for m in markers)


def find_install() -> str:
    """定位 Wallpaper Engine 安装根（`projects\\` 与 `config.json` 所在层）。

    顺序：环境变量 AELOC_WE_ROOT → 注册表里的 Steam → 常见路径。
    环境变量优先，是为了测试与自检能把结果指到 fixture 上，而不是开发机的真实安装。
    """
    env = os.environ.get("AELOC_WE_ROOT", "").strip()
    if env and _is_we_root(os.path.normpath(env)):
        return os.path.normpath(env)

    candidates: List[str] = []
    steam = _reg_steam_path()
    if steam:
        candidates.append(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        candidates.extend(_vdf_library_paths(vdf))
    for c in (
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"D:\SteamLibrary",
        r"E:\SteamLibrary",
    ):
        candidates.append(c)

    seen = set()
    for root in candidates:
        p = os.path.normpath(os.path.join(root, "steamapps", "common", "wallpaper_engine"))
        if p in seen:
            continue
        seen.add(p)
        if _is_we_root(p):
            return p
    # 最后再试「非 Steam 库」的独立安装
    for c in (r"C:\Program Files (x86)\Wallpaper Engine", r"C:\Program Files\Wallpaper Engine"):
        if _is_we_root(c):
            return os.path.normpath(c)
    return ""


def workshop_dirs() -> List[str]:
    """列出所有**确实装着工坊壁纸**的内容目录。

    ★ 判据是「`steamapps\\workshop\\content\\431960` 存在」，不是「库里有
      wallpaper_engine 目录」—— 卸载残留会登记出一个永远出不了壁纸的库。
    ★ 去重用 normcase：Windows 上注册表给的是 `d:\\...`、VDF 里可能是 `D:\\...`，
      同一个目录会以两种大小写各出现一次（不去重就会把每张工坊壁纸扫两遍）。
    """
    libs: List[str] = []
    steam = _reg_steam_path()
    if steam:
        libs.append(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        libs.extend(_vdf_library_paths(vdf))
    out: List[str] = []
    seen = set()
    for lib in libs:
        d = os.path.normpath(os.path.join(lib, "steamapps", "workshop", "content", WE_APPID))
        key = os.path.normcase(d)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(d):
            out.append(d)
    return out


# ---------------------------------------------------------------- PKG 容器


def _lz4_block(src: bytes, dst_size: int) -> bytes:
    """解一个原始 LZ4 块（PKG 条目链与 TEX mipmap 都用这个格式）。"""
    if dst_size < 0 or dst_size > MAX_DECOMPRESSED_BYTES:
        raise ValueError("lz4: 解压尺寸越界 (%d)" % dst_size)
    dst = bytearray(dst_size)
    ip = 0
    op = 0
    n = len(src)
    while ip < n:
        token = src[ip]
        ip += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if ip >= n:
                    raise ValueError("lz4: 字面量长度截断")
                s = src[ip]
                ip += 1
                lit += s
                if s != 255:
                    break
        if ip + lit > n or op + lit > dst_size:
            raise ValueError("lz4: 字面量越界")
        dst[op:op + lit] = src[ip:ip + lit]
        ip += lit
        op += lit
        if ip >= n:
            break
        if ip + 2 > n:
            raise ValueError("lz4: 匹配偏移截断")
        offset = src[ip] | (src[ip + 1] << 8)
        ip += 2
        if offset == 0 or offset > op:
            raise ValueError("lz4: 非法匹配偏移 %d" % offset)
        mlen = token & 0x0F
        if mlen == 15:
            while True:
                if ip >= n:
                    raise ValueError("lz4: 匹配长度截断")
                s = src[ip]
                ip += 1
                mlen += s
                if s != 255:
                    break
        mlen += 4
        if op + mlen > dst_size:
            raise ValueError("lz4: 匹配越界")
        for _ in range(mlen):
            dst[op] = dst[op - offset]
            op += 1
    if op != dst_size:
        raise ValueError("lz4: 解压尺寸不符 (%d != %d)" % (op, dst_size))
    return bytes(dst)


def _probe_compressed(data: bytes, off: int, length: int) -> Optional[int]:
    """[off, off+length) 是不是一条完整 LZ4 块链；是则返回原始大小。

    ★ 必须是**完整链校验**，不能只看大小关系（见模块头注释）。
    """
    if length < 8:
        return None
    lo, hi = struct.unpack_from("<II", data, off)
    original = lo + hi * 0x100000000
    if original <= length or original > 0x7FFFFFFF:
        return None
    pos = off + 8
    total = 0
    end = off + length
    while total < original:
        if pos + 8 > end:
            return None
        uncomp, comp = struct.unpack_from("<ii", data, pos)
        if uncomp <= 0 or comp <= 0 or pos + 8 + comp > end:
            return None
        total += uncomp
        pos += 8 + comp
    return original if (total == original and pos == end) else None


def _read_entry(data: bytes, off: int, stored: int, size: int, is_lz4: bool) -> bytes:
    """读一条 PKG 条目（压缩的自动解压）。"""
    if size > MAX_PKG_ENTRY_BYTES:
        raise ValueError("条目过大 (%d)" % size)
    if not is_lz4:
        return data[off:off + stored]
    pos = off
    end = off + stored
    (original,) = struct.unpack_from("<Q", data, pos)
    if original != size:
        raise ValueError("条目大小不符")
    pos += 8
    out = bytearray()
    while len(out) < size:
        if pos + 8 > end:
            raise ValueError("压缩条目截断")
        uncomp, comp = struct.unpack_from("<ii", data, pos)
        pos += 8
        if uncomp <= 0 or comp <= 0 or len(out) + uncomp > size or pos + comp > end:
            raise ValueError("压缩条目损坏")
        out += _lz4_block(data[pos:pos + comp], uncomp)
        pos += comp
    return bytes(out)


def parse_pkg(data: bytes) -> Tuple[str, List[Tuple[str, int, int, int, bool]]]:
    """解析 PKG 索引。返回 (魔数, [(路径, 绝对偏移, 存储长, 原始长, 是否压缩)])。"""
    pos = 0

    def i32() -> int:
        nonlocal pos
        if pos + 4 > len(data):
            raise ValueError("PKG 头截断")
        (v,) = struct.unpack_from("<i", data, pos)
        pos += 4
        return v

    def u32() -> int:
        nonlocal pos
        if pos + 4 > len(data):
            raise ValueError("PKG 头截断")
        (v,) = struct.unpack_from("<I", data, pos)
        pos += 4
        return v

    n = i32()
    if n < 0 or n > 32:
        raise ValueError("PKG 魔数长度异常 (%d)" % n)
    magic = data[pos:pos + n].decode("utf-8", "replace")
    pos += n
    if not magic.startswith("PKGV"):
        raise ValueError("不是 PKG 容器（魔数 %r）" % magic)
    count = i32()
    if count < 0 or count > 0x100000:
        raise ValueError("PKG 条目数异常 (%d)" % count)
    raw_index = []
    for _ in range(count):
        ln = i32()
        if ln < 0 or ln > 4096:
            raise ValueError("PKG 路径长度异常 (%d)" % ln)
        path = data[pos:pos + ln].decode("utf-8", "replace")
        pos += ln
        raw_index.append((path, u32(), u32()))
    data_start = pos

    entries = []
    for path, rel, length in raw_index:
        off = data_start + rel
        if off < 0 or off + length > len(data):
            continue
        orig = _probe_compressed(data, off, length)
        entries.append((path, off, length, orig if orig else length, orig is not None))
    return magic, entries


def _find_ftyp(buf: bytes) -> int:
    """在数据前 200 字节内找 MP4 的 ftyp box，返回 box 起点（找不到 -1）。"""
    limit = min(200, max(0, len(buf) - 8))
    for i in range(limit):
        if buf[i:i + 4] == b"ftyp":
            start = i - 4
            if start >= 0:
                return start
    return -1


def _tex_payload(body: bytes) -> Tuple[str, bytes]:
    """从 TEX 纹理体里取出可直接使用的画面载荷。

    返回 (种类, 数据)：种类 = "video" | "jpeg" | "png" | ""
    ★ 顺序不能反：先判 MP4，再找 JPEG/PNG —— 有的 .tex 里 MP4 自身带封面图，
      先找 JPEG 会把「一张嵌在视频里的封面」当成整张壁纸。
    """
    ft = _find_ftyp(body)
    if ft >= 0:
        return "video", body[ft:]
    i = body.find(b"\x89PNG\r\n\x1a\n")
    if i >= 0:
        return "png", body[i:]
    i = body.find(b"\xff\xd8\xff")
    if i >= 0:
        return "jpeg", body[i:]
    return "", b""


def _is_aux_tex(path: str) -> bool:
    """这条纹理是不是遮罩/法线/水印一类的辅助资源（不是壁纸画面）。"""
    low = path.lower()
    return any(k in low for k in _AUX_TEX_KEYWORDS)


def scene_payload(pkg_path: str) -> Tuple[str, bytes, str]:
    """从 scene.pkg 里挑出「最像主画面」的那条纹理载荷。

    返回 (种类, 数据, 纹理路径)；拿不到返回 ("", b"", "")。
    ★ 打分规则：先排除辅助纹理；剩下的取载荷最大的 —— 壁纸的主画面几乎总是
      包里最大的那张图/视频（实测本机 8/8 符合）。
    """
    with open(pkg_path, "rb") as f:
        data = f.read()
    _magic, entries = parse_pkg(data)
    tex = [e for e in entries if e[0].lower().endswith(".tex")]

    cands = []
    for path, off, stored, size, is_lz4 in tex:
        try:
            body = _read_entry(data, off, stored, size, is_lz4)
        except (ValueError, struct.error):
            continue
        kind, payload = _tex_payload(body)
        if not payload:
            continue
        cands.append((_is_aux_tex(path), -len(payload), path, kind, payload))
    if not cands:
        return "", b"", ""
    cands.sort(key=lambda c: (c[0], c[1]))
    aux, _neg, path, kind, payload = cands[0]
    # ★ 只剩辅助纹理（法线/遮罩/水波纹）时不当成画面：那种图铺满窗口毫无意义，
    #   宁可退回封面图让用户看到真正的那张壁纸。实测 3660822428 只有一张
    #   waterripple normal map 能取出来，旧逻辑会把它当壁纸。
    if aux:
        return "", b"", ""
    return kind, payload, path


#: 松散 scene 项目里单个 .tex 的读取上限。官方自带项目都很小，超过这个量级
#: 多半不是「一张主图」，直接跳过比冒险读进内存好。
_LOOSE_TEX_MAX_BYTES = 64 * 1024 * 1024


def loose_scene_payload(w: "Wallpaper") -> Tuple[str, bytes, str]:
    """松散 scene（`scene.json` + 同目录散放的 .tex）里取主画面载荷。

    ★ 官方自带项目（Neon Sunset / Beach 等）是这种形态：它们的主纹理是
      BC/DXT 压缩块，本模块**不解码 DXT 像素**（那是另一个量级的工作），
      所以这类项目通常取不到载荷 → 调用方退回封面图。
      但只要有一条 .tex 里直接装着 JPEG/PNG/MP4，这里就能拿到真实画面。
    """
    cands = []
    root = w.dir
    for cur, _dirs, names in os.walk(root):
        for name in names:
            if not name.lower().endswith(".tex"):
                continue
            p = os.path.join(cur, name)
            try:
                if os.path.getsize(p) > _LOOSE_TEX_MAX_BYTES:
                    continue
                with open(p, "rb") as f:
                    body = f.read()
            except OSError:
                continue
            kind, payload = _tex_payload(body)
            if not payload:
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            cands.append((_is_aux_tex(rel), -len(payload), rel, kind, payload))
    if not cands:
        return "", b"", ""
    cands.sort(key=lambda c: (c[0], c[1]))
    aux, _neg, rel, kind, payload = cands[0]
    if aux:
        return "", b"", ""
    return kind, payload, rel


# ---------------------------------------------------------------- 画面提取


def find_ffmpeg() -> str:
    """找一个可用的 ffmpeg。找不到返回空串（调用方退回封面图，不当失败）。"""
    env = os.environ.get("AELOC_FFMPEG", "").strip()
    if env and os.path.isfile(env):
        return env
    from shutil import which

    p = which("ffmpeg")
    if p:
        return p
    for c in (
        os.path.expanduser(r"~\bin\ffmpeg8\ffmpeg.exe"),
        os.path.expanduser(r"~\bin\ffmpeg\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ):
        if os.path.isfile(c):
            return c
    return ""


def _run_ffmpeg_frame(ffmpeg: str, video: str, out_png: str,
                      max_width: int = MAX_WIDTH, timeout: int = 90) -> bool:
    """用 ffmpeg 抽一帧到 out_png。

    ★ 先跳 1 秒再抽：很多视频开头是黑场淡入，抽第 0 帧会得到一张全黑背景。
      视频短于 1 秒时退回第 0 帧。
    ★ 不用管道（本项目的运行环境里子进程管道有历史问题）；输出直接落文件，
      判据是「ffmpeg 正常退出且文件非空」—— 只看文件在不在会把半张残图当成功。
    """
    if not ffmpeg or not os.path.isfile(video):
        return False
    for ss in ("1", "0"):
        try:
            if os.path.exists(out_png):
                os.unlink(out_png)
        except OSError:
            pass
        args = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", ss, "-i", video,
            "-frames:v", "1", "-an",
            "-vf", "scale='min(%d,iw)':-2" % max_width,
            out_png,
        ]
        try:
            proc = subprocess.run(args, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and os.path.isfile(out_png) and os.path.getsize(out_png) > 1024:
            return True
    return False


def _image_size(path: str) -> Tuple[int, int]:
    """读图片尺寸（不加载像素）。Qt 不可用时退回 0,0。"""
    try:
        from PySide6.QtGui import QImageReader

        r = QImageReader(path)
        s = r.size()
        if s.isValid():
            return s.width(), s.height()
    except Exception:
        pass
    return 0, 0


def extract(w: Wallpaper, cache_dir: str) -> Optional[Extracted]:
    """把一张壁纸提取成本地可用的画面文件。

    ★ 结果落 cache_dir，文件名按壁纸 id + 主文件 mtime 派生 —— 换了壁纸内容
      就自动换文件名，不会把旧图当新图。调用方负责后续复制/改名。
    拿到真实画面返回 Extracted；拿不到时**退回封面图**（`from_preview=True`），
    只有连封面图都没有才返回 None。
    """
    os.makedirs(cache_dir, exist_ok=True)
    try:
        stamp = int(os.path.getmtime(w.main_abs)) if w.main_abs and os.path.isfile(w.main_abs) else 0
    except OSError:
        stamp = 0
    key = "%s-%d" % (w.id, stamp)

    result = _extract_real(w, cache_dir, key)
    if result is not None:
        return result
    return _extract_preview(w, cache_dir, key)


def _extract_real(w: Wallpaper, cache_dir: str, key: str) -> Optional[Extracted]:
    """真实画面提取（不含封面图兜底）。"""
    kind = w.kind
    main = w.main_abs

    # ---- 图片类：本身就是一张图，直接用 ----
    if kind == "image" and main and os.path.isfile(main):
        ext = os.path.splitext(main)[1].lower()
        dst = os.path.join(cache_dir, key + ext)
        if os.path.isfile(dst):
            ww, hh = _image_size(dst)
            if ww > 0 and hh > 0:
                return Extracted(dst, "image", ww, hh)
        import shutil

        try:
            shutil.copyfile(main, dst)
        except OSError:
            return None
        ww, hh = _image_size(dst)
        if ww <= 0 or hh <= 0:
            return None
        return Extracted(dst, "image", ww, hh)

    # ---- 视频类：直接把 mp4 交给播放器（不抽帧，实时播放质量最高）----
    if kind == "video" and main and os.path.isfile(main):
        return Extracted(main, "video")

    # ---- 场景类 ----
    if kind == "scene":
        pkind, payload, _tex = "", b"", ""
        try:
            if main and os.path.isfile(main) and main.lower().endswith(".pkg"):
                pkind, payload, _tex = scene_payload(main)
            else:
                # 松散 scene 项目：scene.json + 散放的 .tex
                pkind, payload, _tex = loose_scene_payload(w)
        except (ValueError, OSError, struct.error, MemoryError):
            return None
        if not payload:
            return None
        if pkind == "video":
            # 内嵌的是 MP4：落成视频文件后交给播放器（与视频壁纸同一路径）
            vpath = os.path.join(cache_dir, key + "-embed.mp4")
            if not (os.path.isfile(vpath) and os.path.getsize(vpath) > 4096):
                try:
                    with open(vpath, "wb") as f:
                        f.write(payload)
                except OSError:
                    return None
            return Extracted(vpath, "video")
        # 内嵌的是 JPEG/PNG：直接落地
        ext = ".png" if pkind == "png" else ".jpg"
        dst = os.path.join(cache_dir, key + ext)
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 1024):
            try:
                with open(dst, "wb") as f:
                    f.write(payload)
            except OSError:
                return None
        ww, hh = _image_size(dst)
        # ★ 必须校验能真解码：DXT 纹理的字节里可能恰好出现 FFD8FF，
        #   切出来的「JPEG」Qt 会报 Unsupported marker type 0x91。
        #   不解码验证就会把一张坏图存成背景（实测 arsenal/beach/deep_space 中招）。
        if ww <= 0 or hh <= 0:
            try:
                os.unlink(dst)
            except OSError:
                pass
            return None
        return Extracted(dst, "image", ww, hh)

    return None


def _extract_preview(w: Wallpaper, cache_dir: str, key: str) -> Optional[Extracted]:
    """兜底：用封面图当背景（缩略图级质量，但至少是这张壁纸的样子）。

    ★ 官方自带的三维场景（Neon Sunset 等）主纹理是 DXT 压缩块，本模块不解码
      像素，所以它们只能走这条路 —— 界面上必须标出「封面图」，不能让用户
      以为拿到的是原画。
    ★ GIF 封面不动（QML 的 AnimatedImage 理论上能放，但封面帧率/体积不适合
      当整窗背景），只取静态首帧视角的静态图。
    """
    src = w.preview
    if not src or not os.path.isfile(src):
        return None
    ext = os.path.splitext(src)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        ext = ".jpg"
    dst = os.path.join(cache_dir, key + "-preview" + ext)
    if not (os.path.isfile(dst) and os.path.getsize(dst) > 512):
        import shutil

        try:
            shutil.copyfile(src, dst)
        except OSError:
            return None
    ww, hh = _image_size(dst)
    return Extracted(dst, "image", ww, hh, from_preview=True)


# ---------------------------------------------------------------- 清单枚举


def _contained(base: str, rel: str) -> bool:
    """rel 解析后是否仍在 base 目录内（挡住 `..\\..\\Windows` 这类写法）。

    ★ 第三方 project.json 的 file/preview 是不可信输入，参考项目为此单独修过
      一个「可以读任意文件」的漏洞。这里一律先校验再使用。
    """
    if not rel:
        return False
    try:
        base_abs = os.path.abspath(base)
        target = os.path.abspath(os.path.join(base, rel))
    except (OSError, ValueError):
        return False
    return target == base_abs or target.startswith(base_abs + os.sep)


def _read_project(dir_path: str) -> Optional[Wallpaper]:
    """读一个壁纸项目的 project.json，构造清单条目。"""
    pj_path = os.path.join(dir_path, "project.json")
    if not os.path.isfile(pj_path):
        return None
    try:
        with open(pj_path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return None
    except (OSError, ValueError):
        return None

    pid = os.path.basename(dir_path)
    title = str(obj.get("title") or pid)
    declared = str(obj.get("type") or "").lower()
    main = str(obj.get("file") or "")

    # project.json 声明的类型不在已知范围内时按主文件扩展名推断
    kind = declared if declared in ("scene", "video", "web", "image", "application") else ""
    if not main:
        kind = kind or "image"
        main = ""
    if not kind:
        low = main.lower()
        if low.endswith(_VIDEO_EXT):
            kind = "video"
        elif low.endswith((".html", ".htm", ".js")):
            kind = "web"
        elif low.endswith(_IMAGE_EXT):
            kind = "image"
        else:
            kind = "scene"

    # scene 的主文件经常「声明 scene.json 但只发 scene.pkg」→ 逐个探
    if kind == "scene":
        for cand in (main, "scene.pkg", "scene.json"):
            if cand and os.path.isfile(os.path.join(dir_path, cand)) and _contained(dir_path, cand):
                main = cand
                break
        else:
            pkgs = [n for n in os.listdir(dir_path) if n.lower().endswith(".pkg")]
            if len(pkgs) == 1:
                main = pkgs[0]
            else:
                return None
    else:
        # 非 scene：声明的文件必须存在且在目录内，否则这条壁纸没主文件 → 丢弃
        if not main or not _contained(dir_path, main) or \
                not os.path.isfile(os.path.join(dir_path, main)):
            return None

    if kind != "scene" and not os.path.isfile(os.path.join(dir_path, main)):
        return None

    preview = str(obj.get("preview") or "")
    preview_abs = ""
    if preview and _contained(dir_path, preview):
        p = os.path.join(dir_path, preview)
        if os.path.isfile(p):
            preview_abs = p
    if not preview_abs and kind == "image":
        preview_abs = os.path.join(dir_path, main) if main else ""

    return Wallpaper(
        id=pid, title=title, kind=kind, main=main, dir=dir_path,
        preview=preview_abs,
        content_rating=str(obj.get("contentrating") or ""),
    )


#: project.json 里认识的参数类型（WE 自己的属性面板用同一套名字）
_PROP_TYPES = ("bool", "color", "slider", "combo", "text", "textinput",
               "group", "scenetexture", "file", "directory")
_HTML_TAG = re.compile(r"<[^>]*>")
#: WE 的 text 里常见 &nbsp; / &amp; / 甚至漏了分号的 &nbsp —— 一并吃掉
_HTML_ENT = re.compile(r"&(?:nbsp|amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);?", re.I)
_LOC_KEY = re.compile(r"^[a-z][a-z0-9_]{6,}$")

def _prop_label(raw, name: str) -> str:
    """属性显示名：去掉 WE 常用的 HTML、把本地化 key 换成属性名。"""
    s = _HTML_TAG.sub("", str(raw or ""))
    s = _HTML_ENT.sub(" ", s)
    s = " ".join(s.split())
    # 本地化 key（ui_browse_properties_scheme_color）和上传者塞进来的长串都不当标题
    if not s or _LOC_KEY.match(s) or (len(s) > 40 and " " not in s):
        return name
    return s[:60]


#: Wallpaper Engine 把「用户调过的壁纸属性」存在自己的 config.json 里
#: 结构：<userid>.wproperties.<入口文件绝对路径(正斜杠)>.Monitor0.{属性名: 值}
#: ★ 只写这一个分支，别的一概不碰 —— 那是 WE 自己的配置文件。
_WE_CONFIG = "config.json"


def _normalize_entry(path: str) -> str:
    """WE 用正斜杠的绝对路径当 key。"""
    return os.path.abspath(path).replace("\\", "/")


def _load_we_config(install: str):
    p = os.path.join(install, _WE_CONFIG)
    if not os.path.isfile(p):
        return p, None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return p, None
    return (p, obj) if isinstance(obj, dict) else (p, None)


def _wproperties_branch(obj: dict):
    """取 config.json 里的 ``<userid>.wproperties``（用户 id 是拿不到名字的那层）。"""
    for key, val in obj.items():
        if not isinstance(val, dict) or key.startswith("?"):
            continue
        wp = val.get("wproperties")
        if isinstance(wp, dict):
            return val, wp
    return None, None


def _entry_key(wp: dict, entry_path: str):
    """在 wproperties 里找这张壁纸的键 —— **大小写/斜杠不敏感**。

    ★★ 2026-11-17 修（用户「点了没反应 / 要能立刻刷新」的真因）：
      WE 自己的键可能是 ``D:/SteamLibrary/...``，而 Steam 库路径返回的盘符可能小写。
      早先直接拿 ``os.path.abspath`` 的结果当键，于是在同一份
      config.json 里造出一个**只差大小写的重复键**：WE 永远只读它自己那个键，
      我们写进去的值它看不见 —— 表现就是"改了没反应"，而我们的面板也读不到
      WE 里已有的值。
    """
    want = _normalize_entry(entry_path)
    if want in wp:
        return want
    low = want.lower()
    for key in wp:
        if str(key).replace("\\", "/").lower() == low:
            return key
    return None


def _merged_entry(wp: dict, entry_path: str) -> Dict[str, object]:
    """把（可能重复的）条目合起来，返回 Monitor0 的值。

    重复键里**较新的（我们自己写的）覆盖较早的** —— 那是用户最后一次的意图。
    """
    key = _entry_key(wp, entry_path)
    merged: Dict[str, object] = {}
    if key is None:
        return merged
    want_low = _normalize_entry(entry_path).lower()
    for k in [key] + [x for x in wp if x != key]:
        if str(k).replace("\\", "/").lower() != want_low:
            continue
        val = wp.get(k)
        if isinstance(val, dict):
            mon = val.get("Monitor0")
            if isinstance(mon, dict):
                merged.update(mon)
    return merged


def read_wproperty_overrides(w: Wallpaper, install: str) -> Dict[str, object]:
    """读这张壁纸在 WE 里被改过的属性值（没有就返回空字典）。"""
    _p, obj = _load_we_config(install)
    if not obj:
        return {}
    _user, wp = _wproperties_branch(obj)
    if not wp:
        return {}
    return _merged_entry(wp, os.path.join(w.dir, w.main))


def write_wproperty(w: Wallpaper, install: str, name: str, value) -> Tuple[bool, str]:
    """把单个属性值写进 WE 的 config.json（只动这一个键）。

    返回 ``(成功, 说明)``。**不写 project.json，不改工坊文件**：
    WE 自己的属性面板也是把这批值存在 config.json 里的（实测 8 个壁纸都有，
    且其中有些键 project.json 里根本没有，说明那是 WE 的运行时状态）。
    WE 未运行时写入 → 它下次启动读取；WE 正在运行的话退出时会整份覆盖，
    所以调用方要先确认 WE 没在跑。
    """
    path, obj = _load_we_config(install)
    if obj is None:
        return False, "找不到壁纸引擎的 config.json"
    user, wp = _wproperties_branch(obj)
    if wp is None:
        return False, "config.json 里没有可写的 wproperties 分支"
    entry_path = os.path.join(w.dir, w.main)
    entry_key = _entry_key(wp, entry_path) or _normalize_entry(entry_path)
    # 清理历史遗留的「只差大小写」重复键：合并进选定键，然后删掉多余的
    merged = _merged_entry(wp, entry_path)
    dup = [k for k in list(wp)
           if k != entry_key
           and str(k).replace("\\", "/").lower() == _normalize_entry(entry_path).lower()]
    for d in dup:
        wp.pop(d, None)
    entry = wp.get(entry_key)
    if not isinstance(entry, dict):
        entry = {}
        wp[entry_key] = entry
    mon = entry.get("Monitor0")
    if not isinstance(mon, dict):
        mon = {}
    mon = dict(merged)
    entry["Monitor0"] = mon
    mon[str(name)] = value

    backup = path + ".aeloc.bak"
    try:
        if not os.path.isfile(backup):
            import shutil as _shutil

            _shutil.copyfile(path, backup)
        tmp = path + ".aeloc.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        return False, "写入失败：%s" % str(exc)[:60]
    return True, "已写入壁纸引擎配置"


def read_properties(w: Wallpaper) -> List[dict]:
    """读壁纸自己声明的可调参数（``project.json`` → ``general.properties``）。

    ★★ 这一层**只能读、只能展示**（2026-11-16）：参数的消费方是 Wallpaper Engine
    自己的场景渲染器（scene.json 里写的是 ``{"user": "<属性名>"}`` 绑定，见
    docs/decisions.md）。本工具的背景是从壁纸里**提取的静态画面/内嵌视频**，
    不跑 WE 的渲染器，所以改这些值不会改变这里看到的背景 —— 界面上必须把这句话
    摆在参数旁边，否则又是一个「改了没反应」的死控件。

    ★ 第三方 project.json 是不可信输入：只取白名单字段、剥 HTML、限制条数与长度。
    """
    pj_path = os.path.join(w.dir, "project.json")
    try:
        with open(pj_path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return []
    general = obj.get("general") if isinstance(obj, dict) else None
    props = general.get("properties") if isinstance(general, dict) else None
    if not isinstance(props, dict):
        return []

    out: List[dict] = []
    for name, d in list(props.items())[:200]:
        if not isinstance(d, dict):
            continue
        ptype = str(d.get("type") or "").lower()
        if ptype not in _PROP_TYPES:
            continue
        item = {
            "name": str(name)[:48],
            "type": ptype,
            "label": _prop_label(d.get("text"), str(name)),
            "value": _plain(d.get("value")),
        }
        if ptype == "slider":
            for k in ("min", "max", "step", "precision"):
                if isinstance(d.get(k), (int, float)):
                    item[k] = d[k]
        elif ptype == "combo":
            opts = []
            for o in list(d.get("options") or [])[:40]:
                if isinstance(o, dict):
                    opts.append({"label": _prop_label(o.get("label"), str(o.get("value") or "")),
                                 "value": _plain(o.get("value"))})
            item["options"] = opts
        try:
            order = float(d.get("order"))
        except (TypeError, ValueError):
            order = float(len(out))
        out.append((order, item))

    out.sort(key=lambda t: t[0])
    return [it for _, it in out]


def _plain(v):
    """只放 JSON 基本类型出去（数字 / 布尔 / 短字符串），其余一律空串。"""
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    return str(v)[:120] if isinstance(v, str) else ""


def _hidden_names(root: str) -> set:
    """读 visibility.json：用户在 WE 里「从库中移除」的壁纸仍留在盘上。

    ★ 不读它就会把已移除的壁纸继续列出来。文件缺失/坏 JSON 一律「不过滤」——
      宁可多列，不能凭空少列。
    """
    path = os.path.join(root, "visibility.json")
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return set()
    out = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if v is False:
                out.add(str(k))
                out.add(str(k).replace("\\", "/"))
                out.add(str(k).split("\\")[-1].split("/")[-1])
            elif isinstance(v, dict):
                walk(v)

    walk(obj)
    return out


def scan(install: str = "", workshops: Optional[List[str]] = None) -> List[Wallpaper]:
    """枚举所有可用壁纸（自建项目 + 工坊订阅）。"""
    roots: List[Tuple[str, str]] = []   # (目录, 来源)
    if install:
        for sub in ("defaultprojects", "myprojects"):
            p = os.path.join(install, "projects", sub)
            if os.path.isdir(p):
                roots.append((p, "local"))
    if workshops is None:
        workshops = workshop_dirs()
    for ws in workshops:
        if os.path.isdir(ws):
            roots.append((ws, "workshop"))

    found: Dict[str, Wallpaper] = {}
    seen_ids = set()
    seen_roots = set()
    for root, source in roots:
        rkey = os.path.normcase(root)
        if rkey in seen_roots:
            continue
        seen_roots.add(rkey)
        try:
            names = os.listdir(root)
        except OSError:
            continue
        hidden = _hidden_names(root)
        for name in names:
            if name in hidden:
                continue
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            w = _read_project(d)
            if not w:
                continue
            key = w.id.lower()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            w.source = source
            found[key] = w
    # ★ 不区分大小写排序：纯 `sorted(key=title)` 会把 "Beta" 排到 "alpha" 前面
    #   （ASCII 里大写 < 小写），列表看着像没排序。中文不受影响。
    return sorted(found.values(), key=lambda w: (w.title.casefold(), w.title))


def default_cache_dir(data_root: str) -> str:
    return os.path.join(data_root, "we_cache")
