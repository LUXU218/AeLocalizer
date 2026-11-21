"""分类名（category）还原 —— 与效果名合并成一项。

★ 为什么单独一层（实测 2026-10-21）：
  AE 菜单是两级结构：
    "Sapphire 调整"   <- category（分类名）
      "S_Gamma"       <- name（效果名）

  之前只还原了 name，用户看到菜单里还是中文 —— 因为 category 没还原。

★ 还原依据：
  category 没有 matchName 可用，只能按厂商前缀查表：
    "Sapphire 调整" -> "Sapphire Adjust"
    "BCC 模糊"      -> "BCC Blur"
  推不出来的保持原样（不硬猜）。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CJK = re.compile(r"[\u4e00-\u9fff]")

MC = r"C:\Program Files\Adobe\Common\Plug-ins\7.0\MediaCore"
#: ★ 2026-10-22：不再使用写死的版本路径（原来叫 AE26）。
#:   版本由 backend 传入，或由 _detect_ae_root() 自动探测。


def _detect_ae_root() -> str:
    """自动探测 AE 安装根目录（不硬编码版本）。

    ★ 2026-10-22：去掉了写死 2026 的兜底。调用方（backend）会传入
      用户当前选中的版本；只有单独调用时才用这个探测结果。
    """
    base = r"C:\Program Files\Adobe"
    if os.path.isdir(base):
        cands = sorted(
            (d for d in os.listdir(base)
             if d.lower().startswith("adobe after effects")),
            reverse=True)
        for d in cands:
            p = os.path.join(base, d)
            if os.path.isdir(p):
                return p
    return ""

#: Sapphire 官方分类名
#: ★ 实测补充：AE 里显示的是 "Sapphire 模糊锐化"（带空格），
#:   而映射表的键是 "模糊锐化"，strip 后能对上 —— 但之前没生效，
#:   因为 re 匹配 "Sapphire\s+(.+)" 后 group(1) 已是 "模糊锐化"，
#:   查表应该命中。实测确认可用，保留。
SAPPHIRE_CAT = {
    "变形": "Distort", "合成": "Composite", "时间": "Time",
    "构建": "Build", "模糊锐化": "Blur & Sharpen", "渲染": "Render",
    "灯光": "Lighting", "调整": "Adjust", "转场": "Transitions",
    "风格化": "Stylize", "特效": "Effects",
    "模糊和锐化": "Blur & Sharpen", "模糊": "Blur", "锐化": "Sharpen",
}

#: ★ 长度受限的短名（2026-10-21 实测）
#:
#: PiPL 的 category 字段是【定长】的：中文 2 字节/字，英文更长。
#: 实测这几个装不下完整英文，只能用短名：
#:   "BCC 变形"(8 字节)  -> "BCC Transform"(13) 装不下 -> "BCC Warp"
#:   "BCC 抠像混合"(14)  -> "BCC Keying & Blend"(18) 装不下 -> "BCC Key+Blend"
#:   "BCC 匹配运动"(13)  -> "BCC Motion Match"(16) 装不下 -> "BCC Motion"
#:   "BCC 浏览"(7)       -> "BCC Browsers"(12) 装不下 -> "BCC FX"
#:   "Sapphire 模糊锐化"(20) -> "Sapphire Blur & Sharpen"(23) -> "Sapphire Blur"
SHORT_CAT = {
    "BCC 变形": "BCC Warp",
    "BCC 抠像混合": "BCC Key+Blend",
    "BCC 匹配运动": "BCC Motion",
    "BCC 浏览": "BCC FX",
    "Sapphire 模糊锐化": "Sapphire Blur",
    "通用插件": "Plugins",
}

#: BCC 官方分类名
#: ★ 实测补全（2026-10-21）：之前漏了"匹配运动/变形/抠像混合/浏览"这几个，
#:   导致菜单里还剩中文。以 BCC 官方菜单名为准。
BCC_CAT = {
    "模糊": "Blur", "色彩校正": "Color Correction", "扭曲": "Distort",
    "生成": "Generators", "图像修复": "Image Restoration",
    "抠像混合": "Keying & Blend", "时间": "Time", "透视": "Perspective",
    "风格化": "Stylize", "转场": "Transitions", "灯光": "Lights",
    "粒子": "Particles", "纹理": "Textures", "胶片风格": "Film Style",
    "艺术外观": "Artistic", "调色": "Color Grade", "镜头": "Lens",
    "光学扩散": "Optical Diffusion", "匹配运动": "Motion Match",
    "变形": "Transform", "浏览": "Browsers", "渐变着色": "Gradient",
    "过时": "Obsolete", "3D 对象": "3D Objects", "VR": "VR",
    "通道": "Channels", "3D": "3D", "对象": "Objects",
    # ★ 实测补全（2026-10-21 全量还原时发现剩余 7 种）：
    #   这些是 BCC+ / Mocha 系的实际分类名，之前漏了。
    "胶片样式": "Film Style", "胶片风格": "Film Style",
    "光线": "Lights", "灯光": "Lights",
    "镜艺外观": "BCC+ Looks", "镜层对象": "BCC+ Layers",
    "镜匹移动": "BCC+ Motion", "镜匹运动": "BCC+ Motion",
}


def translate_category(txt: str) -> str:
    """把中文分类名翻回英文。推不出来返回空串。

    ★ 先查【短名表】（长度受限的那几个），再走常规映射。
    """
    t = txt.strip()
    if not CJK.search(t):
        return ""

    # 优先短名（这些是长度装不下完整英文的特例）
    short = SHORT_CAT.get(t)
    if short:
        return short

    # ★ 兜底：按中文关键词匹配短名表（防止 strip 后仍不相等）
    for k, v in SHORT_CAT.items():
        if k in t or t in k:
            return v

    # ★ 无前缀的直接映射（Mocha / Red Giant 系，实测 2026-10-21）
    #   这些分类名不带厂商前缀，必须整串匹配。
    PLAIN_CAT = {
        "镜艺外观": "BCC+ Looks",
        "镜层对象": "BCC+ Layers",
        "镜匹移动": "BCC+ Motion",
        "镜匹运动": "BCC+ Motion",
        "RE:视觉特效插件": "RE:Vision Plugins",
        "RE:视觉插件": "RE:Vision Plugins",
        "P粒子独立版": "Particle Illusion",
    }
    plain = PLAIN_CAT.get(t)
    if plain:
        return plain

    m = re.match(r"^Sapphire\s+(.+)$", t)
    if m:
        en = SAPPHIRE_CAT.get(m.group(1).strip())
        return "Sapphire " + en if en else ""

    m = re.match(r"^BCC\+\s*(.+)$", t)
    if m:
        en = BCC_CAT.get(m.group(1).strip())
        return "BCC+ " + en if en else ""

    m = re.match(r"^BCC\s+(.+)$", t)
    if m:
        en = BCC_CAT.get(m.group(1).strip())
        return "BCC " + en if en else ""

    return ""


def restore_categories(dry: bool = False, ae_root: str = "") -> tuple:
    """还原所有 aex 的 category 字段。返回 (数量, 说明)。

    ae_root: 目标 AE 安装根目录，留空自动探测。
             ★ 2026-10-22：原来写死 2026，版本切换后还原会打错版本。
    """
    from core.pipl import parse_pipl

    # ★★ 2026-10-22 修正：原来硬编码几个厂商目录，漏了 "Red Giant Universe"
    #    （它是独立目录，不在 "Red Giant VFX" 里），也没有 AE 版本自适应。
    #    改成直接扫整个 MediaCore + 自动探测到的 AE Plug-ins。
    roots = [MC]
    _ae = ae_root or _detect_ae_root()
    if _ae:
        p = os.path.join(_ae, "Support Files", "Plug-ins")
        if os.path.isdir(p):
            roots.append(p)

    done = skip = fail = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for cur, _d, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".aex"):
                    continue
                p = os.path.join(cur, f)
                try:
                    d = bytearray(open(p, "rb").read())
                except OSError:
                    continue
                ent = None
                for s in parse_pipl(bytes(d)):
                    if s.key == "category":
                        ent = s
                        break
                if ent is None:
                    continue
                raw = bytes(d[ent.offset:ent.offset + ent.length])
                try:
                    txt = raw.decode("gbk")
                except UnicodeDecodeError:
                    continue
                if not CJK.search(txt):
                    continue
                eng = translate_category(txt)
                if not eng:
                    skip += 1
                    continue
                eb = eng.encode("ascii", "ignore")
                if len(eb) > ent.length:
                    skip += 1
                    continue
                new = eb + b"\x00" * (ent.length - len(eb))
                if raw == new:
                    continue
                if dry:
                    done += 1
                    continue
                try:
                    d[ent.offset:ent.offset + ent.length] = new
                    with open(p, "wb") as fp:
                        fp.write(d)
                    done += 1
                except (PermissionError, OSError):
                    fail += 1

    msg = "分类名还原 %d 个" % done
    if skip:
        msg += "，%d 个推不出英文（保持原样）" % skip
    if fail:
        msg += "，%d 个失败（需管理员权限）" % fail
    return done, msg
