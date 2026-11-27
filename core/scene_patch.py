"""把场景包里 **Chromium 播不了** 的视频贴图转码并重打包。

★ 为什么需要它（2026-11-17 实测）：PySide6 6.12 的 WebEngine 是**不带专有编解码器**
  的 Chromium —— `canPlayType('video/mp4; codecs="avc1..."')` 返回空，<video> 报
  `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`；而 VP8/VP9/AV1 正常。Wallpaper Engine 的
  场景壁纸里，人物层常常是 **H.264 的"视频贴图"**（.tex 里包着 MP4），于是整张壁纸
  只剩黑色背景。

★ 做法：把每个视频 .tex 的 MP4 载荷转成 **VP9-in-MP4**（Chromium 认这个），
  保留 .tex 头部不动（头部第 12 字节处声明的是"贴图类型"，与视频编码无关），
  再把整个包重打包成一份**未压缩**的副本缓存起来，由 scene_server 顶替原始包。
  顺带收益：720p VP9 比 4K H.264 小得多，场景加载快很多。

★ 只读原来那张包，绝不修改工坊文件。
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Callable, List, Optional, Tuple

from . import wallpaper_engine as _we

#: 转码以显示器高度为目标。固定 720p 会降低高分辨率显示器上的画质，
#: 按屏幕高度转码可兼顾清晰度和渲染负载。
DEFAULT_TARGET_HEIGHT = 1080
#: 转码后帧率：跟源一致（不降帧，动作观感不变）
TARGET_FPS = 60
#: AV1(NVENC) 的质量档：cq 越小越好。34 在 1440p60 约 6 Mbps，观感接近原片
AV1_CQ = 34
#: 没有硬件编码器时的 VP9 回退码率（按高度换算，见 _bitrate_for）
VP9_KBPS_PER_MEGAPIXEL = 3500
#: 单条贴图的转码上限（秒），防止异常大包把界面卡太久
TRANSCODE_TIMEOUT = 1800


def find_video_texes(pkg_path: str) -> List[str]:
    """列出包里所有「载荷是 MP4」的 .tex（即 Chromium 播不了的那些）。"""
    try:
        with open(pkg_path, "rb") as f:
            data = f.read()
        _magic, entries = _we.parse_pkg(data)
    except (OSError, ValueError):
        return []
    out = []
    for path, off, stored, size, is_lz4 in entries:
        if not path.lower().endswith(".tex"):
            continue
        try:
            body = _we._read_entry(data, off, stored, size, is_lz4)
        except (ValueError, struct.error):
            continue
        if _we._find_ftyp(body) >= 0:
            out.append(path)
    return out


def pick_encoder(ffmpeg: str) -> str:
    """优先硬件 AV1（RTX 40 系/新 Arc 都有）；没有就回退 libvpx-vp9。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                           text=True, timeout=20, creationflags=flags)
        if "av1_nvenc" in (r.stdout or ""):
            return "av1_nvenc"
    except (OSError, subprocess.SubprocessError):
        pass
    return "libvpx-vp9"


def _bitrate_for(height: int) -> str:
    px = (height * height * 16 / 9) / 1_000_000.0
    return "%dk" % max(2500, int(px * VP9_KBPS_PER_MEGAPIXEL))


def _transcode(src_bytes: bytes, ffmpeg: str, target_height: int = DEFAULT_TARGET_HEIGHT,
               encoder: str = "") -> bytes:
    """MP4(H.264) → MP4(AV1 或 VP9)，按**显示器高度**、60fps、无音频。失败返回空字节。"""
    tmp = tempfile.mkdtemp(prefix="aeloc_tex_")
    try:
        src = os.path.join(tmp, "in.mp4")
        dst = os.path.join(tmp, "out.mp4")
        with open(src, "wb") as f:
            f.write(src_bytes)
        vf = "scale=-2:%d:flags=fast_bilinear,fps=%d" % (max(360, target_height), TARGET_FPS)
        enc = encoder or pick_encoder(ffmpeg)
        if enc == "av1_nvenc":
            codec = ["-c:v", "av1_nvenc", "-preset", "p1", "-tune", "hq",
                     "-rc", "vbr", "-cq", str(AV1_CQ), "-b:v", "0"]
        else:
            # libvpx-vp9 的 cpu-used 上限就是 8，写 9 会直接报 option out of range
            codec = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                     "-row-mt", "1", "-threads", "10",
                     "-b:v", _bitrate_for(target_height)]
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-an", "-vf", vf] + codec + [
            "-movflags", "+faststart", "-f", "mp4", dst,
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(cmd, capture_output=True, timeout=TRANSCODE_TIMEOUT, creationflags=flags)
        if not os.path.isfile(dst) or os.path.getsize(dst) < 1024:
            return b""
        with open(dst, "rb") as f:
            return f.read()
    except (OSError, subprocess.SubprocessError):
        return b""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_pkg(out_path: str, items: List[Tuple[str, object]]) -> None:
    """按 PKGV 容器格式写出。

    items: [(路径, 载荷)]，载荷是 ``bytes``（内存，已转码/已解压）
    或 ``(源文件路径, 偏移, 长度)``（原样拷贝，不占内存）。
    """
    index = []
    cursor = 0
    for path, payload in items:
        size = len(payload) if isinstance(payload, bytes) else int(payload[2])
        index.append((path, cursor, size))
        cursor += size

    head = b""
    magic = b"PKGV0024"
    head += struct.pack("<i", len(magic)) + magic
    head += struct.pack("<i", len(index))
    for path, rel, length in index:
        pb = path.encode("utf-8")
        head += struct.pack("<i", len(pb)) + pb
        head += struct.pack("<II", rel, length)

    tmp = out_path + ".tmp"
    with open(tmp, "wb") as dst:
        dst.write(head)
        for _path, payload in items:
            if isinstance(payload, bytes):
                dst.write(payload)
                continue
            src_path, off, length = payload
            with open(src_path, "rb") as src:
                src.seek(off)
                left = left_total = length
                while left > 0:
                    chunk = src.read(min(4 * 1024 * 1024, left))
                    if not chunk:
                        break
                    dst.write(chunk)
                    left -= len(chunk)
                if left:
                    # 读短了就用 0 补齐，保证索引里的长度与文件一致
                    dst.write(b"\x00" * left)
    os.replace(tmp, out_path)


def profile_for(target_height: int, ffmpeg: str = "") -> str:
    """缓存画像：分辨率 + 编码器。变了就要重转（换显示器/换机器）。"""
    enc = pick_encoder(ffmpeg) if ffmpeg else "auto"
    return "%s-%dp%d" % (enc, int(target_height), TARGET_FPS)


def patch_scene_pkg(pkg_path: str, out_path: str, ffmpeg: str,
                    log: Optional[Callable[[str], None]] = None,
                    target_height: int = DEFAULT_TARGET_HEIGHT,
                    encoder: str = "") -> Tuple[bool, str]:
    """生成一份"视频贴图已转成 VP9"的 pkg 副本。返回 (成功, 说明)。

    ★ 并发转码（2026-11-17 提速）：5 条 4K 视频串行要 24 秒，2 路并行约一半；
      ffmpeg 自己已多线程，再开更多路只会互相抢核。
    ★ 转失败的贴图保留**原始字节**，绝不写空条目（那会把包弄坏）。
    """
    say = log or (lambda _m: None)
    try:
        with open(pkg_path, "rb") as f:
            data = f.read()
        _magic, entries = _we.parse_pkg(data)
    except (OSError, ValueError) as exc:
        return False, "读包失败：%s" % str(exc)[:60]
    if not entries:
        return False, "包里没有条目"

    # 第一遍：分出「要转码的视频贴图」和「原样条目」；视频条目先放原始 body 兜底
    plain: list = []          # [(路径, 载荷)] 载荷 = bytes | (源文件,偏移,长度)
    videos: list = []         # [(路径, tex头, 原body, 视频载荷)]
    for path, off, stored, size, is_lz4 in entries:
        body = None
        if path.lower().endswith(".tex"):
            try:
                body = _we._read_entry(data, off, stored, size, is_lz4)
            except (ValueError, struct.error):
                body = None
        if body is not None:
            ft = _we._find_ftyp(body)
            if ft >= 0 and len(body) - ft > 1024:
                videos.append((path, body[:ft], body, body[ft:]))
                plain.append((path, body))
                continue
            plain.append((path, body))
            continue
        if not is_lz4:
            plain.append((path, (pkg_path, off, stored)))
        else:
            try:
                plain.append((path, _we._read_entry(data, off, stored, size, is_lz4)))
            except (ValueError, struct.error):
                return False, "解压条目失败：%s" % path[:60]

    del data

    patched = 0
    if videos:
        import concurrent.futures as _cf

        enc = encoder or pick_encoder(ffmpeg)
        say("正在转码 %d 条视频贴图（%s，目标 %dp%d）…"
            % (len(videos), "硬件 AV1" if enc == "av1_nvenc" else "软件 VP9",
               target_height, TARGET_FPS))

        def _one(item):
            return _transcode(item[3], ffmpeg, target_height, enc)

        with _cf.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_one, videos))

        done = {}
        for (path, head, body, _payload), out in zip(videos, results):
            if out:
                done[path] = head + out
                patched += 1
                say("转码完成：%s" % os.path.basename(path)[:40])
            else:
                say("转码失败，保留原样：%s" % os.path.basename(path)[:40])
        if done:
            plain = [(p, done.get(p, payload)) for (p, payload) in plain]

    try:
        _write_pkg(out_path, plain)
    except OSError as exc:
        return False, "写包失败：%s" % str(exc)[:60]
    if videos and patched == 0:
        return False, "视频贴图全都没转成功（ffmpeg 不可用？）"
    return True, "已转换 %d 条视频贴图 → %s" % (patched, os.path.basename(out_path))


def cache_dir(data_root: str) -> str:
    d = os.path.join(data_root, "we_live")
    os.makedirs(d, exist_ok=True)
    return d


def cached_patched_pkg(wallpaper_dir: str, data_root: str, wid: str,
                       profile: str = "") -> str:
    """已缓存的补丁包路径（不存在或源包变过就返回空串）。"""
    p = os.path.join(cache_dir(data_root), "%s.pkg" % wid)
    meta = p + ".json"
    if not (os.path.isfile(p) and os.path.isfile(meta)):
        return ""
    try:
        with open(meta, encoding="utf-8") as f:
            obj = json.load(f)
        src = os.path.join(wallpaper_dir, "scene.pkg")
        st = os.stat(src)
        if (obj.get("mtime") == st.st_mtime and obj.get("size") == st.st_size
                and str(obj.get("profile") or "") == str(profile or "")):
            return p
    except (OSError, ValueError):
        return ""
    return ""
