"""本地只读资源服务：把「渲染页 + 一张壁纸目录」交给 QtWebEngine。

★ 为什么需要它：WebWallGL 在浏览器里用 fetch 读 ``scene.pkg`` / ``project.json``。
  ``file://`` 下会被 CORS 拦掉（Chromium 的安全模型），所以起一个**只监听
  127.0.0.1**、带一次性 token 的 HTTP 服务最稳：
    - 端口随机（bind 0），不暴露到外网；
    - 路径里的 token 每次启动随机，页面地址不可猜；
    - 只服务这一张壁纸的目录，且做目录包含校验（项目里的老规矩：
      第三方 project.json 是不可信输入）。
  ★ 默认不开：只有用户把场景壁纸切成「实时渲染」时才起。
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import threading
import uuid
from typing import Optional

#: 渲染页与 JS 渲染核心所在目录（app/assets/webwallgl）
ASSET_NAMES = ("index.html", "webwallgl.global.min.js", "LICENSE")


def _contained(base: str, rel: str) -> Optional[str]:
    """把 rel 解析进 base，越界返回 None（符号链接也挡住）。"""
    try:
        base_abs = os.path.realpath(base)
        target = os.path.realpath(os.path.join(base_abs, rel))
    except (OSError, ValueError):
        return None
    if target == base_abs or target.startswith(base_abs + os.sep):
        return target
    return None


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "aeloc-scene/1"

    # 由 SceneServer 注入
    token: str = ""
    asset_dir: str = ""
    #: ★ 共享可变状态（换壁纸只改这个 dict，**不重启监听**）：
    #:   端口/token 不变 → 渲染页 origin 不变 → 可以在同一个页面里 inst.load() 切场景，
    #:   省掉整页重载（2026-11-18 架构优化）。
    state: dict = {}

    def log_message(self, *args):  # 静默：别把访问日志打到用户控制台
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        if path.lower().endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.lower().endswith((".html", ".htm")):
            ctype = "text/html; charset=utf-8"
        elif path.lower().endswith(".json"):
            ctype = "application/json; charset=utf-8"
        else:
            ctype = "application/octet-stream"
        self._send(200, body, ctype)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        prefix = "/t/%s/" % self.token
        if path == "/t/%s" % self.token:
            self.send_response(301)
            self.send_header("Location", prefix)
            self.end_headers()
            return
        if not path.startswith(prefix):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        rel = path[len(prefix):]
        if rel in ("", "/", "index.html"):
            self._send_file(os.path.join(self.asset_dir, "index.html"))
            return
        if rel.startswith("wp/"):
            rest = rel[3:]
            if rest.lower() == "scene.pkg":
                pkg = str(self.state.get("pkg") or "")
                if pkg and os.path.isfile(pkg):
                    self._send_file(pkg)
                    return
            target = _contained(str(self.state.get("dir") or ""), rest)
            if not target:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send_file(target)
            return
        if rel in ASSET_NAMES:
            self._send_file(os.path.join(self.asset_dir, rel))
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SceneServer:
    """一次只托管一张壁纸；换壁纸就重新 start()。"""

    def __init__(self, asset_dir: str):
        self._asset_dir = asset_dir
        self._httpd: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._url = ""
        self._wallpaper_dir = ""
        self._pkg_override = ""

    # ---- 状态 ----
    @property
    def url(self) -> str:
        return self._url

    @property
    def running(self) -> bool:
        return self._httpd is not None

    # ---- 生命周期 ----
    def start(self, wallpaper_dir: str, pkg_override: str = "") -> str:
        """托管这张壁纸并把渲染页地址返回；同目录 + 同补丁包则直接复用。

        pkg_override：视频贴图已转码的补丁包（Chromium 播不了 H.264，见 scene_patch）。
        """
        wallpaper_dir = os.path.abspath(wallpaper_dir)
        pkg_override = os.path.abspath(pkg_override) if pkg_override else ""
        if self._httpd is not None:
            # ★ 已在这个端口服务：只换目标，不重启（页面 origin 保持不变 → 可热切场景）
            self._state["dir"] = wallpaper_dir
            self._state["pkg"] = pkg_override
            self._wallpaper_dir = wallpaper_dir
            self._pkg_override = pkg_override
            return self._url
        self.stop()

        token = uuid.uuid4().hex
        self._state = {"dir": wallpaper_dir, "pkg": pkg_override}
        handler = type("_BoundHandler", (_Handler,), {
            "token": token,
            "asset_dir": self._asset_dir,
            "state": self._state,
        })
        httpd = _Server(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        self._httpd = httpd
        self._wallpaper_dir = wallpaper_dir
        self._pkg_override = pkg_override
        self._url = "http://127.0.0.1:%d/t/%s/" % (port, token)
        self._thread = threading.Thread(target=httpd.serve_forever, name="aeloc-scene", daemon=True)
        self._thread.start()
        return self._url

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        self._url = ""
        self._wallpaper_dir = ""
        self._pkg_override = ""
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._thread = None


def asset_dir() -> str:
    """渲染页目录：<repo>/app/assets/webwallgl（打包后跟着资源根走）。"""
    import sys

    if getattr(sys, "frozen", False):
        root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "app", "assets", "webwallgl")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "app", "assets", "webwallgl")
