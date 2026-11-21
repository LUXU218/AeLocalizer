"""编码层：把「语义层的 UTF-8 译名」翻译成「目标 AE 版本能接受的字节」。

这是 2026 乱码问题的正解所在 —— **词典永远存 UTF-8，落盘/写内存时才决定用哪种编码**。

两套编码世界（实测得出，不是查版本号表）：
  · ANSI 世界（AE 2021 ~ 2025）  → 名字缓冲按系统代码页（中文系统 = GBK/CP936）解释
  · UTF-8 世界（AE ≥ 26.2）      → 名字缓冲按 UTF-8 解释

三条通道各有约束：
  ① ANSI 缓冲（参数名/枚举值）  可用字节 = 原英文的 ASCII 长度
  ② UTF-16 副本（时间轴面板）   与版本无关，但要处理 MSVC std::wstring 的 SSO 边界
  ③ .aex PiPL 静态字段          跟 ANSI 世界同编码，但**长度槽位是死的**

关键决策（D6）：Continuum DLL 的 .rdata 静态串在 2026 上仍写 GBK —— 定长槽位放不下
UTF-8（每汉字 3 字节），写 GBK（每汉字 2 字节）再让 UTF-8 修复层在 UI 前转码，
是唯一"既不超长又能正确显示"的组合。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .shrink import gbk_len, shortest_fitting

#: 编码世界
#: ★★ 2026-10-20 真机实测（AE 26.5x89）：
#:   .aex PiPL 槽位     → GBK
#:   运行期参数名读入    → GBK（采到 \xd7\xd4\xb6\xaf = "自动" 的 GBK 编码）
#:   Continuum .rdata   → GBK
#:   「AE 2026 改用了 UTF-8」这个前提在【中文】场景下不成立 ——
#:   UTF-8 的调用确实存在（328 次/30 秒），但采样里 0 条含中文。
#:   所以中文链路一律用 ANSI(GBK)。
WORLD_ANSI = "ansi"      # GBK / CP936  ← 中文链路的唯一正确选择
WORLD_UTF8 = "utf8"      # 保留：仅用于非中文/兼容场景

#: 写入通道
CH_ANSI = "ansi"          # ret+0x40
CH_UTF16 = "utf16"        # ret+0xF0 侧
CH_PIPL = "pipl"          # .aex MIB8eman / MIB8gtac
CH_RDATA = "rdata"        # Continuum DLL .rdata

#: MSVC std::wstring SSO：内联缓冲 8 个 wchar_t，末位留 NUL → 最多 7 个字符内联
SSO_INLINE_MAX_CHARS = 7
SSO_INLINE_MAX_BYTES = 15  # UTF-16LE：7 字符 + NUL = 16 字节，扣掉 NUL 是 14，安全取 15

#: 写入策略（★ 2026 的核心洞察）
STRATEGY_UTF8_NATIVE = "utf8_native"   # 直接把 UTF-8 写进缓冲
STRATEGY_GBK_FIXER = "gbk_fixer"       # 写 GBK，由 UTF-8 修复层在 UI 前转码
STRATEGY_SKIP = "skip"

#: 计划状态
ST_OK = "ok"
ST_SHORTENED = "shortened"
ST_TOO_LONG = "too_long"
ST_UNENCODABLE = "unencodable"


@dataclass(frozen=True)
class EntryPlan:
    """一条译名在某版本上的写入计划。"""

    key: str
    zh: str
    world: str
    status: str
    payload: bytes = b""
    used_zh: str = ""
    avail_bytes: int = 0
    need_bytes: int = 0
    utf16_len: int = 0
    sso_inline: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (ST_OK, ST_SHORTENED)


def encode_text(zh: str, world: str) -> Optional[bytes]:
    """按编码世界编码；不可表示返回 None。"""
    if world == WORLD_UTF8:
        return zh.encode("utf-8")
    if world == WORLD_ANSI:
        try:
            return zh.encode("gbk")
        except Exception:
            return None
    raise ValueError(f"未知编码世界: {world}")


def byte_len(zh: str, world: str) -> Optional[int]:
    b = encode_text(zh, world)
    return None if b is None else len(b)


def sso_inline_for(zh: str) -> bool:
    """UTF-16 副本是否走 SSO 内联（历史 bug 高发区，必须特别校验）。"""
    return len(zh) <= SSO_INLINE_MAX_CHARS


def plan_ansi(
    key: str,
    zh: str,
    world: str,
    *,
    avail: Optional[int] = None,
    allow_shrink: bool = True,
    slack: int = 0,
) -> EntryPlan:
    """为 ANSI 缓冲规划一次写入。

    avail 缺省 = 原英文 ASCII 长度 + slack（运行时探测到的额外余量）。
    """
    if avail is None:
        avail = len(key.encode("utf-8", "replace")) + max(0, slack)

    need = byte_len(zh, world)
    if need is None:
        return EntryPlan(
            key=key, zh=zh, world=world, status=ST_UNENCODABLE,
            avail_bytes=avail, reason=f"译名含 {world} 无法表示的字符",
        )
    if need <= avail:
        return EntryPlan(
            key=key, zh=zh, world=world, status=ST_OK, payload=encode_text(zh, world) or b"",
            used_zh=zh, avail_bytes=avail, need_bytes=need,
            utf16_len=len(zh), sso_inline=sso_inline_for(zh),
        )

    if allow_shrink:
        fit = _shortest_fitting_world(zh, avail, world)
        if fit is not None:
            return EntryPlan(
                key=key, zh=zh, world=world, status=ST_SHORTENED,
                payload=encode_text(fit, world) or b"", used_zh=fit,
                avail_bytes=avail, need_bytes=need,
                utf16_len=len(fit), sso_inline=sso_inline_for(fit),
                reason=f"{need} > {avail}，缩短为「{fit}」",
            )

    return EntryPlan(
        key=key, zh=zh, world=world, status=ST_TOO_LONG,
        avail_bytes=avail, need_bytes=need,
        reason=f"{need} > 可用 {avail} 字节，且无可用短译",
    )


def _shortest_fitting_world(zh: str, avail: int, world: str) -> Optional[str]:
    """挑一个能放下的译名候选。

    ★ 2026-10-22 修正：名字叫 shortest_fitting，但语义应该是
      「放得下的最长候选」—— 取最短会白丢信息
      （「BCC 模糊溶解」能放下完整 4 字，却只取「模糊溶」）。
      同时跳过 _is_junk 判定的垃圾候选（"BC"/"BCC" 这类纯英文前缀），
      否则界面上会出现一堆 BC。
    """
    from .shrink import _is_junk, candidates

    if world == WORLD_ANSI:
        return shortest_fitting(zh, avail)

    best: Optional[str] = None
    best_n = -1
    for c in candidates(zh):
        if _is_junk(c, zh):
            continue
        try:
            n = len(c.encode("utf-8"))
        except Exception:
            continue
        if n <= avail and n > best_n:
            best, best_n = c, n
    return best


def plan_pipl(key: str, zh: str, world: str, *, avail: Optional[int] = None) -> EntryPlan:
    """.aex PiPL 静态字段：长度槽位固定，超长只能缩短或放弃。"""
    return plan_ansi(key, zh, world, avail=avail, allow_shrink=True)


def plan_rdata(key: str, zh: str, *, avail: Optional[int] = None) -> EntryPlan:
    """Continuum DLL .rdata 静态串：**永远 GBK**（D6 决策）。

    原因：定长槽位按原 ASCII 长度分配，UTF-8 每汉字 3 字节几乎必然超长；
    GBK 每汉字 2 字节刚好够。显示由 UTF-8 修复层在 UI 前转码。
    """
    if avail is None:
        avail = len(key.encode("utf-8", "replace"))
    plan = plan_ansi(key, zh, WORLD_ANSI, avail=avail, allow_shrink=True)
    if plan.status == ST_TOO_LONG:
        return EntryPlan(
            key=key, zh=zh, world=WORLD_ANSI, status=ST_TOO_LONG,
            avail_bytes=avail, need_bytes=plan.need_bytes,
            reason=plan.reason + "（.rdata 定长槽位，无法扩展）",
        )
    return plan


def plan_utf16(zh: str) -> EntryPlan:
    """UTF-16 副本：只要不是空串就能写；重点是标出 SSO 内联状态。"""
    if not zh:
        return EntryPlan(key="", zh=zh, world=WORLD_UTF8, status=ST_UNENCODABLE, reason="空串")
    return EntryPlan(
        key="", zh=zh, world=WORLD_UTF8, status=ST_OK,
        payload=zh.encode("utf-16-le"), used_zh=zh,
        avail_bytes=len(zh) * 2, need_bytes=len(zh) * 2,
        utf16_len=len(zh), sso_inline=sso_inline_for(zh),
    )


def plan_all(key: str, zh: str, world: str, *, avail: Optional[int] = None) -> dict:
    """一次给出三条通道的计划，供 UI 的"长度闸"逐列显示。"""
    a = plan_ansi(key, zh, world, avail=avail)
    w = plan_utf16(zh)
    p = plan_pipl(key, zh, world, avail=avail)
    r = plan_rdata(key, zh, avail=avail)
    return {"ansi": a, "utf16": w, "pipl": p, "rdata": r}


def choose_strategy(
    key: str,
    zh: str,
    world: str,
    *,
    fixer_available: bool = False,
    channel: str = CH_ANSI,
    slack: int = 0,
) -> str:
    """选哪种写法。

    ★★ 更正（2026-10-20，读源码后）：**UTF-8 世界里必须直接写 UTF-8**，
    不能"写 GBK 让修复层转码"。理由：

      1. 修复层（AE-UTF8-Fixer）只做编码转换，**不扩大缓冲区**。
         它拿到的是定长数组 PF_DEF_NAME，超出就 memcpy_s 截断 +
         TrimInvalidTailUTF8 砍掉不完整字符 —— 结果是**丢字**。
      2. 转换后 UTF-8 比 GBK 更长（3 字节/汉字 vs 2 字节/汉字），
         本来放得下的 GBK 译文转成 UTF-8 反而放不下。
      3. 现有引擎 useUtf8() 就是 >=2026 直接写 UTF-8，与之一致才对。

    修复层的真正价值在**别处**（见 plan_rdata 与 docs/decisions.md D8）：
    它兜住"插件自带的 GBK 静态串"和"我们改不动的 DLL .rdata 副本"。

    slack: 运行时探测到的额外余量（名字末尾之后的连续 0 字节数）。
           引擎实测靠这个把大量"按原 ASCII 长度算不够"的条目救回来。
    """
    avail = len(key.encode("utf-8", "replace")) + max(0, slack)

    if channel in (CH_RDATA, CH_PIPL):
        # ★★ 实测（AE MCP 2026-10-20）：
        #   .aex 的 PiPL 槽位与 Continuum DLL 的 .rdata 一样，【恒为 GBK】。
        #   按 UTF-8 写入会被 AE 当 GBK 解码 -> 乱码（"通道切换" -> "閫氶亾鍒囨崲"）。
        #   这条与 AE 版本无关，2021~2026 都一样。
        p = plan_ansi(key, zh, WORLD_ANSI, avail=avail)
        return "gbk_native" if p.ok else STRATEGY_SKIP

    if world == WORLD_ANSI:
        p = plan_ansi(key, zh, WORLD_ANSI, avail=avail)
        return "gbk_native" if p.ok else STRATEGY_SKIP

    # UTF-8 世界：直接写 UTF-8（与引擎 useUtf8() 一致）
    p = plan_ansi(key, zh, WORLD_UTF8, avail=avail)
    return STRATEGY_UTF8_NATIVE if p.ok else STRATEGY_SKIP


def plan_for_strategy(
    key: str,
    zh: str,
    world: str,
    strategy: str,
    *,
    avail: Optional[int] = None,
    slack: int = 0,
) -> EntryPlan:
    if avail is None:
        avail = len(key.encode("utf-8", "replace")) + max(0, slack)
    """按选定策略产出真正要写进内存的字节。"""
    if strategy in ("gbk_native", STRATEGY_GBK_FIXER):
        return plan_ansi(key, zh, WORLD_ANSI, avail=avail)
    if strategy == STRATEGY_UTF8_NATIVE:
        return plan_ansi(key, zh, WORLD_UTF8, avail=avail)
    return EntryPlan(
        key=key, zh=zh, world=world, status=ST_TOO_LONG,
        avail_bytes=avail or len(key), reason="按当前策略放弃写入",
    )


def probe_slack(buf: bytes, name_len: int, limit: int = 64) -> int:
    """模拟引擎的"运行时余量探测"：数名字末尾之后有多少连续 0 字节。

    buf: 从名字起始处读到的字节（建议 >= name_len + limit）
    name_len: 原英文名字的字节长度
    返回: 可额外使用的字节数（不含那个终止 NUL 本身）
    """
    if name_len < 0 or name_len >= len(buf):
        return 0
    n = 0
    i = name_len
    while i < len(buf) and buf[i] == 0 and n < limit:
        n += 1
        i += 1
    # 末尾那个 NUL 必须保留给终止符，所以可用余量 = 连续 0 的个数 - 1
    return max(0, n - 1)


def describe(key: str, zh: str, world: str) -> str:
    """给 UI 用的一行摘要：运行时能不能装下这条译文。

    ★ 2026-11-15 改版：原来输出「GBK 12/14 · UTF-8 16/14 · SSO 走堆 · 当前=ansi」，
      那是静态改写 .aex 时代的字节预算视图。静态写盘整层删除后，运行时 hook
      不写任何文件，唯一会失败的是「译文比原名长、装不进内存里的定长名字槽」。
      所以这里直接给这个结论，不再堆编码细节。

    world: 运行时承载那条缓冲的构建编码（ansi / utf8），由调用方按 AE 版本给。
    """
    if not zh:
        return "无译文"
    avail = len(key.encode("utf-8", "replace"))
    need = byte_len(zh, WORLD_UTF8 if world == WORLD_UTF8 else WORLD_ANSI)
    if need is None:
        return "编码不可写"
    if need <= avail:
        return "可写入（余 %d 字节）" % (avail - need)
    return "超出 %d 字节，可能被截断" % (need - avail)