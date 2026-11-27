"""质量闸：AI 产出的每一批译文必须过 6 道检查才进"草稿"。

这是"用户可维护"和"软件乱改我东西"的分界线 —— 没有闸门，AI 的错译会
直接进词典，用户就再也不敢用了。

六道闸（顺序执行，任一不过就标记原因）：
  1. JSON 合法      可解析、是数组、每条有 en/zh
  2. 长度            不超目标编码的可用字节；超了自动缩短并标记
  3. 术语一致        术语表内词条不得改（strict）
  4. 禁用错译        历史踩坑表（Right->正确、Fog Color->牙齿颜色 …）
  5. 未译检测        输出 == 输入（原样返回英文）→ 标记未译，不计成功
  6. 编码安全        含控制字符 / U+FFFD / emoji → 拒绝该条

通过的进"草稿区"，用户可逐条复核 / 全部接受 / 丢弃。绝不直接写进生效词典。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..encoder import WORLD_ANSI, WORLD_UTF8, plan_ansi

#: 闸门结果状态
OK = "ok"
SHORTENED = "shortened"
REJECTED = "rejected"
UNTRANSLATED = "untranslated"
GLOSSARY_FIXED = "glossary_fixed"

#: 历史踩坑表 —— 这些译名是实测翻错的，一律拒绝
BAD_TRANSLATIONS: Dict[str, str] = {
    "Right": "正确",
    "Highlights": "重点说明",
    "Fog Color": "牙齿颜色",
    "Spacing": "空格",
    "Quality": "质量",
    "Points": "要点",
    # ★ 2026-10-30 真机抓到的历史错译（AE 2022 上实测写入日志），
    #   错因都是"多词短语被截成半个词意"或"单词根译错、复合词才对"。
    #   这些条目的正确译法已钉在 core/termfix.py（优先级最高的一层），
    #   这里再加一道闸，防止 AI 重新生成同样的错译。
    "Glow": "发光",
    "Glow Color": "发光颜色",
    "Glow Width": "发光宽度",
    "Size": "字数",
    "Ambient": "周围",
    "Occlusion": "遮蔽",
    "Specular": "镜像",
    "Velocity": "速率",
    "Blur Res": "模糊品质",
    "Shutter Duration": "拖尾长度",
    "NR Color Strength": "降噪",
    "NR Passes": "降噪",
    "NR Style": "降噪",
    "Force Scene Reset": "强制",
    "Last parameter": "末个 parameter",
    "Upsampling Method": "Upsampling 方法",
    # Wrap 在 AE 的平铺参数里是"拼贴"（词典里已有的整条枚举就是这个译法）
    "Wrap X": "X 环绕",
    "Wrap Y": "Y 环绕",
}

_FENCE = chr(96) * 3

_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")


@dataclass
class DraftEntry:
    """一条 AI 产出的草稿词条。"""

    en: str
    zh: str
    status: str = OK
    reason: str = ""
    used_zh: str = ""          # 实际会被写入的（可能是缩短后的）
    source_en: str = ""        # 原始英文（en 可能被清洗过）

    @property
    def accepted(self) -> bool:
        return self.status in (OK, SHORTENED, GLOSSARY_FIXED)


@dataclass
class GateReport:
    """一批的闸门汇总。"""

    total: int = 0
    ok: int = 0
    shortened: int = 0
    glossary_fixed: int = 0
    untranslated: int = 0
    rejected: int = 0
    entries: List[DraftEntry] = field(default_factory=list)
    parse_error: str = ""

    @property
    def accepted(self) -> List[DraftEntry]:
        return [e for e in self.entries if e.accepted]

    def summary(self) -> str:
        return (
            "通过 %d · 缩短 %d · 术语修正 %d · 未译 %d · 拒绝 %d"
            % (self.ok, self.shortened, self.glossary_fixed, self.untranslated, self.rejected)
        )


# ---------------------------------------------------------------- 各道闸


def strip_fence(text: str) -> str:
    """去掉模型爱加的围栏包裹。"""
    t = text.strip()
    if not t.startswith(_FENCE):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith(_FENCE):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_response(text: str) -> Tuple[List[dict], str]:
    """闸 1：从模型输出里解析出 [{'en':..., 'zh':...}, ...]。

    容忍：围栏包裹、前后多余文字、单对象而非数组、entries 包装。
    """
    t = strip_fence(text)
    candidates = [t]
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = t.find(opener), t.rfind(closer)
        if 0 <= i < j:
            candidates.append(t[i:j + 1])

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get("entries"), list):
                obj = obj["entries"]
            elif "en" in obj and "zh" in obj:
                obj = [obj]
            else:
                continue
        if not isinstance(obj, list):
            continue
        out = []
        for item in obj:
            if not isinstance(item, dict):
                continue
            en = item.get("en") or item.get("english") or item.get("source")
            zh = item.get("zh") or item.get("chinese") or item.get("target")
            if isinstance(en, str) and isinstance(zh, str) and en:
                out.append({"en": en, "zh": zh})
        if out:
            return out, ""
    return [], "无法从模型输出里解析出 JSON 数组"


def check_length(
    en: str,
    zh: str,
    world: str,
    *,
    slack: int = 0,
    allow_shrink: bool = True,
) -> Tuple[str, str, str]:
    """闸 2：长度。返回 (status, used_zh, reason)。"""
    plan = plan_ansi(en, zh, world, slack=slack, allow_shrink=allow_shrink)
    if plan.status == "ok":
        return OK, zh, ""
    if plan.status == "shortened":
        return SHORTENED, plan.used_zh, plan.reason
    if plan.status == "unencodable":
        return REJECTED, "", plan.reason
    return REJECTED, "", plan.reason


def check_glossary(
    en: str, zh: str, glossary: Dict[str, Dict[str, object]]
) -> Tuple[bool, str, str]:
    """闸 3：术语一致。返回 (是否改动, 应采用译名, 原因)。"""
    g = glossary.get(en)
    if not g:
        return False, zh, ""
    want = str(g.get("zh", ""))
    if not want or zh == want:
        return False, zh, ""
    if g.get("strict"):
        return True, want, "术语表要求「%s」" % want
    return False, zh, ""


def check_blacklist(en: str, zh: str) -> Tuple[bool, str]:
    """闸 4：禁用错译。返回 (是否命中, 原因)。"""
    bad = BAD_TRANSLATIONS.get(en)
    if bad and zh == bad:
        return True, "命中历史错译黑名单（%s 的该译名已弃用）" % en
    return False, ""


def check_untranslated(en: str, zh: str) -> bool:
    """闸 5：未译检测 —— 原样返回英文。"""
    return zh.strip().lower() == en.strip().lower()


def check_too_short(en: str, zh: str) -> Tuple[bool, str]:
    """闸 4.5：译文短到没有意义。

    2026-10-23：1064 条缓存中有 102 条译文为 BC，例如 BCC DeGrain OBS。
    需要在 AI 结果进入缓存与词典前拦截无意义的缩写。

       原来没有任何一道闸拦它：
         · 不是原样返回英文（check_untranslated 放过）
         · 长度 2 字节，PiPL 槽位绰绰有余（check_length 放过）
         · _is_junk 只在"缩短候选"里筛，AI 直接返回的垃圾它管不到
       因此无意义的 BC 曾进入缓存、草稿和词典。

       判据（只拦明显无意义的）：
         · 纯 ASCII 且 < 3 字符        -> "BC" / "A" / "xy"
         · 纯 ASCII 且只是原文前缀缩水 -> "BCC DeGrain OBS" -> "BCC"
         · 完全没有中文且不是原文里的型号词 -> 说明根本没翻译
    """
    s = (zh or "").strip()
    if not s:
        return True, "译文为空"
    has_cjk = any("一" <= c <= "鿿" for c in s)
    if has_cjk:
        # 含中文：至少要有 1 个汉字，且总长 >= 2
        if len(s) < 2:
            return True, "译文过短（%r）" % s
        return False, ""
    # 纯 ASCII
    if len(s) < 3:
        return True, "译文过短（%r）" % s

    # ★ 译文恰好等于厂商前缀本身（"BCC" / "CC" / "S_"）：
    #   说明模型只吐了前缀、根本没翻译。不能因为它在原文里是"独立词"就放过。
    for p in VENDOR_PREFIXES:
        if s.upper() == p.strip().upper():
            return True, "译文只是厂商前缀（%r）" % s

    # ★ 再看它是不是原文里的【独立】型号词（HDR / GPU / LUT / Mocha 这种）。
    #   必须用词边界判断：
    #     "HDR" 在 "HDR Mode" 里是独立词      -> 保留合理
    #     "BC"  在 "BCC DeGrain OBS" 里不是     -> 是 BCC 的前两个字母，垃圾
    if re.search(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])",
                 en, re.IGNORECASE):
        return False, ""

    if en.strip().startswith(s) and len(s) < len(en.strip()) * 0.6:
        return True, "译文只是英文前缀缩水（%r）" % s
    return True, "译文没有中文（%r）" % s


def check_encoding(zh: str) -> Tuple[bool, str]:
    """闸 6：编码安全。返回 (是否通过, 原因)。"""
    if not zh:
        return False, "空译名"
    if _BAD_CHARS.search(zh):
        return False, "含控制字符或替换字符 U+FFFD"
    if _EMOJI.search(zh):
        return False, "含 emoji"
    if len(zh) > 200:
        return False, "译名过长（>200 字符）"
    return True, ""


# ---------------------------------------------------------------- 总入口


#: 厂商/系列前缀。原文有、译文也必须保留。
#:
#: 缺少前缀校验时，草稿曾丢失厂商和系列标识，例如：
#:        S_Cartoon      -> 卡通          （应为 S_卡通）
#:        S_ColorFuse    -> 色彩          （应为 S_色彩）
#:        S_EdgeDetect   -> 边缘检测      （应为 S_边缘检测）
#:   AI 把 S_ 当成无意义前缀吃掉了。
#:   shrink.VENDOR_PREFIXES 里虽然有 "S_"，但那只用于【缩短】时的判重，
#:   从来没有"补回来"的动作；prompt 里也只写了"不翻译内部标识"。
VENDOR_PREFIXES = (
    "Boris Continuum Complete ",
    "Boris FX ",
    "Sapphire ",
    "VideoCopilot ",
    "BCC+",
    "BCC ",
    "CC ",
    "S_",
    "Sapphire_",
)


def restore_vendor_prefix(en: str, zh: str) -> Tuple[str, bool]:
    """原文带厂商前缀但译文丢了 -> 补回来。返回 (新译文, 是否改过)。

    ★ 只补【确实丢了】的：译文已经带着前缀就不动，避免 S_ 变 S_S_。
      长的前缀优先匹配（BCC+ 先于 BCC），否则 "BCC+Looks" 会被
      "BCC " 规则误判。
    """
    body = (zh or "").strip()
    if not body:
        return zh, False
    for p in sorted(VENDOR_PREFIXES, key=len, reverse=True):
        if not en.startswith(p):
            continue
        head = p.rstrip()
        # 译文已经带了（允许 S_ 或 S 空格两种写法）
        if body.upper().startswith(p.upper()) or body.upper().startswith(head.upper()):
            return zh, False
        if p.endswith(" "):
            return head + " " + body, True
        return head + body, True
    return zh, False


def run_gates(
    raw_items: List[dict],
    *,
    world: Optional[str] = WORLD_UTF8,
    glossary: Optional[Dict[str, Dict[str, object]]] = None,
    slack: int = 0,
) -> GateReport:
    """对一批原始结果跑完 6 道闸。"""
    glossary = glossary or {}
    rep = GateReport(total=len(raw_items))

    for item in raw_items:
        en = (item.get("en") or "").strip()
        zh = (item.get("zh") or "").strip()

        # ★ 闸 0：厂商前缀必须保留（S_ / BCC / CC / BCC+ …）
        #   放在最前面，因为补完前缀长度会变，后面的长度闸要按补过的算。
        zh, _prefix_fixed = restore_vendor_prefix(en, zh)

        entry = DraftEntry(en=en, zh=zh, source_en=en)

        ok, why = check_encoding(zh)
        if not ok:
            entry.status, entry.reason = REJECTED, why
            rep.rejected += 1
            rep.entries.append(entry)
            continue

        hit, why = check_blacklist(en, zh)
        if hit:
            entry.status, entry.reason = REJECTED, why
            rep.rejected += 1
            rep.entries.append(entry)
            continue

        if check_untranslated(en, zh):
            entry.status, entry.reason = UNTRANSLATED, "模型原样返回英文，未翻译"
            rep.untranslated += 1
            rep.entries.append(entry)
            continue

        # ★ 闸 4.5：译文短到没意义（"BC" 这种）
        too_short, why_short = check_too_short(en, zh)
        if too_short:
            entry.status, entry.reason = REJECTED, why_short
            rep.rejected += 1
            rep.entries.append(entry)
            continue

        fixed = False
        changed, want, why = check_glossary(en, zh, glossary)
        if changed:
            entry.zh = want
            zh = want
            entry.status, entry.reason = GLOSSARY_FIXED, why
            rep.glossary_fixed += 1
            fixed = True
        else:
            entry.status = OK

        status, used, reason = (OK, zh, "") if world is None else check_length(
            en, zh, world, slack=slack)
        if status == REJECTED:
            entry.status, entry.reason = REJECTED, reason
            rep.rejected += 1
            rep.entries.append(entry)
            continue
        if status == SHORTENED:
            if not fixed:
                entry.status = SHORTENED
                rep.shortened += 1
            entry.reason = reason
            entry.used_zh = used
        else:
            entry.used_zh = zh
            if entry.status == OK:
                rep.ok += 1

        rep.entries.append(entry)

    return rep


def gates_from_text(
    text: str,
    *,
    world: Optional[str] = WORLD_UTF8,
    glossary: Optional[Dict[str, Dict[str, object]]] = None,
    slack: int = 0,
) -> GateReport:
    """从模型原始文本跑完闸 1 + 其余 5 道。"""
    items, err = parse_response(text)
    if err:
        return GateReport(total=0, parse_error=err)
    return run_gates(items, world=world, glossary=glossary, slack=slack)
