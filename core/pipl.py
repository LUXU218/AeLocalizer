"""PiPL 解析：从 .aex / .dll 里读出效果名、matchName、分类、厂商。

PiPL 是 Adobe 插件资源块，.aex 的 .rsrc 段里是一串「item」：
    'MIB8' + 4 字符属性键 + 00 00 00 00 + 4 字节 item 长度 + 1 字节串长 + 字符串

我们只读这几项：
    MIB8eman  -> name       效果显示名（**唯一可安全改写的字段**）
    MIB8ANMe  -> matchName  内部标识（**绝不可改**，改了 AE 报
                            "A MatchName being used is not UTF8"）
    MIB8gtac  -> category   分类名

为什么必须只读 matchName：
    matchName 是工程文件里引用效果的键。改了它，所有已存工程都找不到效果；
    而且 AE 会校验它必须是合法 UTF-8，改错直接弹窗。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

#: 属性键（4 字符）
KEY_NAME = "eman"      # MIB8eman -> name
KEY_MATCH = "ANMe"     # MIB8ANMe -> matchName
KEY_CATEGORY = "gtac"  # MIB8gtac -> category

_ATTR_TO_KEY = {KEY_NAME: "name", KEY_MATCH: "matchName", KEY_CATEGORY: "category"}

#: item 头长度：'MIB8'(4) + 属性键(4) + 保留(4) = 12，之后是 4 字节长度
_HDR = 12
_MAX_ITEM = 8192


@dataclass(frozen=True)
class PiplString:
    """一个 PiPL 字符串 item 的位置与内容。"""

    key: str            # 'name' / 'matchName' / 'category'
    attr: str           # 原始 4 字符属性键
    offset: int         # 字符串数据在文件里的绝对偏移
    length: int         # 声明的字符串长度（字节）
    value: bytes        # 原始字节
    declared_item_len: int = 0

    def clean_value(self) -> bytes:
        """去掉尾部被写坏的部分。

        ★ 实测问题：BCC 老系列的 name 字段 string length 被写成了 24，
          但实际内容只有 20 字节（"BCC 3D Image Shatter"），
          尾部 3 个 \x00 + 下一个 item 的首字母 'M' 被算进了长度。

          判据：遇到第一个 \x00 就截断。PiPL 里字符串本就不该含 NUL，
          且尾部 NUL 一定是"声明长度 > 实际长度"的痕迹。
        """
        v = self.value
        z = v.find(b"\x00")
        return v[:z] if z >= 0 else v

    def text(self, encoding: str = "gbk") -> str:
        """解码显示名。

        ★ 必须用 clean_value()：BCC 的字符串尾部可能混进垃圾字节，
          直接 decode 会得到 "BCC 3D Image Shatter\x00\x00\x00M"。

        ★★ 默认编码必须是 GBK 而不是 UTF-8（2026-10-20 实测修正）
          .aex 的 PiPL 槽位在所有 AE 版本上都是 GBK 写入的。
          但有些汉字的 GBK 字节【恰好也是合法 UTF-8】，例如：
              '投影' -> cd b6 d3 b0
              cd b6 是合法 UTF-8 双字节序列（U+0376 希腊字母）
              d3 b0 也是合法 UTF-8 双字节序列（U+04F0 西里尔字母）
          所以先试 UTF-8 会"成功"，解出 'ͶӰ' 这种乱码而不是报错，
          根本走不到 GBK 那一轮。实测 4 个文件踩到这个坑：
              S_DropShadow.aex / BCCDropShadow.aex / BCCTwister.aex / BCCTwisterPrTr.aex

          改为默认 GBK：绝大多数文件直接对；真的存了 UTF-8 的文件
          （理论上不存在，但留着兜底）GBK 解出来会是乱码但不会崩，
          再由调用方按需传 encoding="utf-8"。
        """
        v = self.clean_value()
        for enc in (encoding, "utf-8", "latin-1"):
            try:
                return v.decode(enc)
            except Exception:
                continue
        return v.decode("latin-1", "replace")

    def writable(self) -> bool:
        """只有 name / category 允许改写；matchName 永远只读。"""
        return self.key in ("name", "category")


@dataclass
class PluginInfo:
    """扫描到的一个插件（一个 .aex / .dll 文件）。"""

    path: str
    file_name: str
    display_name: str = ""
    match_name: str = ""
    category: str = ""
    size: int = 0
    has_pipl: bool = False
    strings: List[PiplString] = field(default_factory=list)
    error: str = ""
    #: name 的来源：pipl（真 PiPL）| filename（文件名兜底）| none
    name_origin: str = "none"

    @property
    def key(self) -> str:
        """稳定标识：优先 matchName，其次文件名。"""
        return self.match_name or self.file_name

    @property
    def name_editable(self) -> bool:
        """只有真 PiPL 的 name 才能安全改写；文件名兜底的不算。"""
        return self.name_origin == "pipl"

    @property
    def is_builtin_adobe(self) -> bool:
        """Adobe 内置效果（可直接用 AE 官方中文，不需要我们汉化）。

        ★★ 2026-10-22 修正（原判定把一大批第三方误伤成「内置」）：
          旧规则是「matchName 以 ADBE 开头 或 路径含 \effects\」，
          结果下面这些全被判成内置、界面上锁死不能选：
            · DLSS_Neural_Video / DepthScanner / GoodbyeGreenscreen
              —— 第三方，但包装器把 matchName 写成了 "ADBE xxx"
            · CycoreFX（CC 系列）/ Mocha / Keylight
              —— 第三方，只是装在了 Effects 的子目录里
          实测 10 个「内置」里只有 "Effects (228 个效果)" 是真的。

          现在要求【同时】满足：
            ① matchName 以 ADBE 开头
            ② 位于 Plug-ins\Effects\ 下【且没有更深的子目录】
          这样 MediaCore 里的第三方、以及 Effects 子目录里的第三方
          都能正常勾选汉化了。
        """
        # ★ 只能用路径判断：实测 Adobe 内置效果的 PiPL matchName 是【空串】
        #   （3DGlasses.aex / AddGrain.aex 等 228 个全是 ''），
        #   所以「matchName 以 ADBE 开头」这条规则完全不能用。
        low = self.path.lower().replace("/", "\\")
        marker = "\\plug-ins\\effects\\"
        i = low.find(marker)
        if i < 0:
            return False
        rest = low[i + len(marker):]
        # 直接在 Effects 下 = Adobe 内置；更深层（CycoreFXHD / Mocha / ...）= 第三方
        return "\\" not in rest

    @property
    def rel_dir(self) -> str:
        return os.path.dirname(self.path)


def iter_pipl_strings(data: bytes) -> Iterator[PiplString]:
    """扫描整个字节串，产出所有可识别的 PiPL 字符串 item。"""
    pos = 0
    n = len(data)
    while True:
        i = data.find(b"MIB8", pos)
        if i < 0 or i + _HDR + 5 > n:
            return
        attr = data[i + 4:i + 8]
        try:
            key = attr.decode("ascii")
        except Exception:
            pos = i + 4
            continue

        if key not in _ATTR_TO_KEY:
            pos = i + 4
            continue

        p = i + _HDR
        item_len = int.from_bytes(data[p:p + 4], "little")
        strlen = data[p + 4]
        off = p + 5
        if off + strlen > n or item_len > _MAX_ITEM or item_len < strlen:
            pos = i + 4
            continue

        yield PiplString(
            key=_ATTR_TO_KEY[key],
            attr=key,
            offset=off,
            length=strlen,
            value=data[off:off + strlen],
            declared_item_len=item_len,
        )
        pos = i + 4


def parse_pipl(data: bytes) -> List[PiplString]:
    return list(iter_pipl_strings(data))


#: AE 资源引用前缀：PiPL 里写成 "$$$/AE/AEGP/Name/MetaPanel=Metadata Browser"
#: AE 界面实际显示的是【等号右边】，左边只是资源表键名。
_REF_PREFIX = "$$$"


def _resolve_name_ref(name: str) -> str:
    """解析 AE 资源引用语法，取出真正的显示名。

    ★ 2026-10-22 实测问题：
      扫描到 33 个插件的 display_name 是
        "$$$/AE/AEGP/Name/MetaPanel=Metadata Browser"
      这种形式（AE 的资源引用：左边是资源键、右边是默认值）。
      原样拿去显示/翻译时，界面上会出现一长串 $$$/AE/AEGP/... 路径，
      而且 AI 翻译会把整个键当成人名去翻 —— 完全没意义。

      这里把等号右边的默认值取出来（没有等号就取路径最后一段）。
    """
    s = (name or "").strip()
    if not s.startswith(_REF_PREFIX):
        return s
    i = s.find("=")
    if i >= 0:
        val = s[i + 1:].strip()
        if val:
            return val
    tail = s.rstrip("/").rsplit("/", 1)[-1].strip()
    return tail or s


def read_plugin(path: str) -> PluginInfo:
    """读一个插件文件，尽力提取可用的名字信息。永不抛异常。"""
    info = PluginInfo(path=path, file_name=os.path.basename(path))
    try:
        info.size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        info.error = "读取失败: %s" % exc
        return info

    try:
        strings = parse_pipl(data)
    except Exception as exc:  # 防御：畸形文件不该让扫描崩
        info.error = "解析失败: %s" % exc
        return info

    info.strings = strings
    info.has_pipl = bool(strings)
    for s in strings:
        if s.key == "name" and not info.display_name:
            info.display_name = _resolve_name_ref(s.text())
            info.name_origin = "pipl"
        elif s.key == "matchName" and not info.match_name:
            info.match_name = s.text()
        elif s.key == "category" and not info.category:
            info.category = s.text()

    if not info.display_name:
        info.display_name = os.path.splitext(info.file_name)[0]
        info.name_origin = "filename"
    return info


# ---------------------------------------------------------------- 目录扫描

#: 相对 AE 安装根目录的插件子目录
PLUGIN_SUBDIRS = (
    "Support Files/Plug-ins",
    "Support Files/Plug-ins/Effects",
    "Support Files/Plug-ins/Format",
)

#: Adobe 公共插件目录（第三方常装在这里）
COMMON_PLUGIN_DIRS = (
    r"C:\Program Files\Adobe\Common\Plug-ins\7.0\MediaCore",
)

PLUGIN_EXTS = (".aex", ".aexx")


def find_ae_installs() -> List[Tuple[str, str]]:
    """返回 [(版本号, 安装根目录)]，按版本降序。

    只做目录枚举（不查注册表）—— 用户可能装在非默认盘。
    """
    roots: List[Tuple[str, str]] = []
    bases = [
        r"C:\Program Files\Adobe",
        r"C:\Program Files (x86)\Adobe",
    ]
    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.lower().startswith("adobe after effects"):
                continue
            parts = name.split()
            ver = parts[-1] if parts else ""
            roots.append((ver, os.path.join(base, name)))
    roots.sort(key=lambda x: x[0], reverse=True)
    return roots


def iter_plugin_files(root: str) -> Iterator[str]:
    """递归产出一个目录下的所有插件文件（不跟随符号链接）。"""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(PLUGIN_EXTS):
                yield os.path.join(dirpath, fn)


def scan_directory(root: str) -> List[PluginInfo]:
    out: List[PluginInfo] = []
    for p in iter_plugin_files(root):
        out.append(read_plugin(p))
    out.sort(key=lambda x: x.display_name.lower())
    return out


def scan_ae(ae_root: str, include_common: bool = True) -> List[PluginInfo]:
    """扫描一个 AE 安装的所有插件目录（按绝对路径去重）。

    include_common=True 时一并扫描 Adobe 公共 MediaCore 目录 ——
    绝大多数第三方（Boris FX / Maxon / Digital Anarchy）都装在那里，
    只看 AE 自己的 Plug-ins 会漏掉一大半。
    """
    seen = set()
    out: List[PluginInfo] = []
    dirs = [os.path.join(ae_root, s.replace("/", os.sep)) for s in PLUGIN_SUBDIRS]
    if include_common:
        dirs.extend(COMMON_PLUGIN_DIRS)
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for p in iter_plugin_files(d):
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                continue
            seen.add(k)
            out.append(read_plugin(p))
    out.sort(key=lambda x: x.display_name.lower())
    return out