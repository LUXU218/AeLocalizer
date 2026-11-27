"""Prompt 工程：决定译文质量的地方。

设计要点：
  · 系统提示固定（可版本化，改动会让缓存失效）
  · 术语表只摘取**相关**的 40 条，不是全量塞进去（省 token）
  · 强制 JSON 输出，附一个示例（few-shot 对格式稳定性帮助最大）
  · 明确"不翻译什么"（专有名词、内部标识、占位符）
"""
from __future__ import annotations

from typing import Dict, List, Optional

#: prompt 版本 —— 改动会让所有缓存失效，必须手动升版本
#: prompt 版本。★ 每次改动 SYSTEM_PROMPT 或 few-shot 都要 +1。
#:
#: 它参与缓存 key 的计算（Cache.key 里有一段），所以改版本会自动让旧缓存失效。
#: 2026-10-23: p3 -> p4，把角色从"Adobe 本地化专家"改成
#:             "影视后期专业中英翻译"，并加入行业标准术语对照表。
PROMPT_VERSION = "p5"

#: 风格
STYLE_CONCISE = "concise"      # 专业简洁（默认）
STYLE_FAITHFUL = "faithful"    # 忠于原文
STYLE_BILINGUAL = "bilingual"  # 双语对照

STYLE_LABEL = {
    STYLE_CONCISE: "专业简洁：优先短词，符合 AE 中文版习惯",
    STYLE_FAITHFUL: "忠于原文：尽量保留原文结构与修饰",
    STYLE_BILINGUAL: "双语对照：输出「英文 (中文)」形式",
}

SYSTEM_PROMPT = """你是一名影视后期（VFX / 调色 / 剪辑）领域的专业中英翻译，
长期为 After Effects、DaVinci Resolve、Nuke 的界面和中文教材做术语本地化。
你的读者是调色师、合成师、剪辑师——译名要让他们一眼看懂，
并且必须使用他们日常交流时的【行业标准术语】，不能按字面直译、更不能拟声。

★ 影视后期行业标准译法（优先于字面义，务必遵守）：
    Swish / Swish Pan   → 甩镜 / 扫掠   （不是"嗖嗖"）
    Glow                → 辉光          （不是"亮光""发光体"）
    Grain               → 颗粒          （不是"谷物""纹理"）
    Key / Keying        → 抠像 / 抠像器 （不是"键控""钥匙"）
    Matte               → 遮罩          （不是"席子""哑光"）
    Rotoscope           → 转描
    Tracking / Tracker  → 跟踪 / 跟踪器
    Warp                → 扭曲 / 变形
    Wipe                → 擦除
    Dissolve            → 溶解
    Blend               → 混合
    Light Wrap          → 光线包裹
    Chroma Key          → 色度抠像
    Depth of Field      → 景深
    Motion Blur         → 动态模糊
    Black & White       → 黑白
    Levels              → 色阶
    Curves              → 曲线
    Saturation          → 饱和度
    Exposure            → 曝光
    Gimbal / Stabilize  → 稳定器 / 稳定
    Sharpen             → 锐化
    Unsharp Mask        → 钝化蒙版      （AE 官方译法，不是"非锐化遮罩"）
    Noise / Denoise     → 噪点 / 降噪
    Prism               → 棱镜
    Lens Flare          → 镜头光晕
    Light Leak          → 漏光

翻译规则：
1. 严格使用给定术语表，术语表内的译名一个字都不能改。
2. 译名必须是一个【能读懂的中文意思】，不能是字母缩写。
   - 绝对禁止输出 "BC"、"BCC"、"S"、"X" 这类只有字母的碎片。
   - 每个译名至少要有一个完整的中文词（如「辉光」「颗粒」「卷动」）。
   - 保留原文含义和必要修饰，不按英文原名的长度删减译文。
3. 保留专有名词与型号不译：Mocha、Cinema 4D、GPU、HDR、LUT、4K、RGBA、XYZ。
4. 保留占位符与编号：%s、#1、Point 2 → 点 2、Layer 3 → 图层 3。
5. 不翻译全大写内部标识（如 BCC_FOO、ADBE_GLOW）。
6. ★ 厂商前缀必须原样保留在最前面，不能吞掉、不能翻译：
     S_Glow → S_发光（不是"发光"）
     BCC Blur → BCC 模糊（不是"模糊"）
     BCC+Looks → BCC+外观（不是"外观"）
     BCC+Film Glow → BCC+胶片辉光
     CC Hair → CC 毛发（不是"毛发"）
     Sapphire_Glow → Sapphire_发光
     VideoCopilot Element → VideoCopilot 元素
   这些前缀是厂商标识，去掉后用户在效果菜单里分不清是谁家的。
7. ★ 输出数组的条目数必须与输入的「待译」条目数【完全相等】：
   一条不能少、不能多、不能合并、不能跳过、不能改变顺序。
8. 不要输出解释、注释或多余文字，只输出 JSON 数组。"""

#: few-shot。★ 2026-10-23 补齐带厂商前缀的例子 ——
#: 原来只有 3 条通用例子，模型对 "BCC+Film Glow" 这类会乱缩成 "BC"。
_FEWSHOT = [
    {"en": "Glow Threshold", "zh": "发光阈值"},
    {"en": "Wrap X", "zh": "环绕 X"},
    {"en": "Mocha Opacity", "zh": "Mocha 不透明度"},
    {"en": "BCC+Film Glow", "zh": "BCC+胶片辉光"},
    {"en": "BCC+Glow Darks", "zh": "BCC+发光暗部"},
    {"en": "BCC DeGrain OBS", "zh": "BCC 降噪"},
    {"en": "S_EdgeDetect", "zh": "S_边缘检测"},
    {"en": "CC Hair", "zh": "CC 毛发"},
    # ★ 2026-10-23：影视专业词。AI 容易按字面/拟声词瞎翻
    #   （实测把 Swish 译成"嗖嗖"），钉几条标准译法给它照抄。
    {"en": "BCC+Swish Pan", "zh": "BCC+甩镜"},
    {"en": "BCC+Swish Glow", "zh": "BCC+扫光"},
    {"en": "BCC+Swish Warp", "zh": "BCC+扫掠扭曲"},
]


#: 词级匹配时忽略的词：厂商前缀 + 通用词。
#: 不排除的话 "BCC" 会命中所有 BCC 效果，把 limit 全吃掉。
_MATCH_STOPWORDS = frozenset({
    "bcc", "cc", "s", "sapphire", "boris", "fx", "ae",
    "obs", "obsolete", "old", "new", "the", "and", "with", "for",
})


def _pick_glossary(
    names: List[str], glossary: Dict[str, Dict[str, object]], limit: int = 40
) -> Dict[str, str]:
    """只摘取与本批相关的术语，避免全量塞进去浪费 token。"""
    if not glossary:
        return {}
    picked: Dict[str, str] = {}
    # 1) 精确命中
    for n in names:
        g = glossary.get(n)
        if g and g.get("zh"):
            picked[n] = str(g["zh"])
    # 2) 词级命中：把本批名字拆词，找术语表里出现过的词
    #
    # 原先的术语匹配会漏掉含加号的效果名，例如 BCC+Swish Glow。
    #   原来只做「整词精确匹配」，而且拆分只认空格和下划线：
    #       本批 "BCC+Swish Glow"  -> 拆成 ['BCC+Swish', 'Glow']
    #       术语表 "BCC Swish Pan OBSOLETE" 整词对不上 -> 一条都摘不到
    #   缺少 Swish 的已有译法时，模型曾将其译成拟声词。
    #
    #   改成双向词级匹配：
    #     · 拆分时把 + / _ 都当分隔符
    #     · 术语表条目的任一「实词」与本批的任一实词相同即命中
    #       （"BCC Swish Pan OBSOLETE" 里的 Swish 命中本批的 Swish）
    if len(picked) < limit:
        words = set()
        for n in names:
            for w in n.replace("_", " ").replace("+", " ").split():
                lw = w.lower()
                # ★ 厂商前缀/通用词不参与匹配，否则 "BCC" 会把 478 条
                #   BCC 效果全兜进来，真正相关的 "Swish Pan" 反而被挤掉。
                if len(lw) > 2 and lw not in _MATCH_STOPWORDS:
                    words.add(lw)
        if words:
            # 长词优先：越长的词区分度越高（Swish > Glow > Pan）
            ordered = sorted(words, key=len, reverse=True)
            for w in ordered:
                if len(picked) >= limit:
                    break
                for gkey in sorted(glossary):
                    if len(picked) >= limit:
                        break
                    if gkey in picked:
                        continue
                    zh = glossary[gkey].get("zh")
                    if not zh:
                        continue
                    gwords = [
                        x.lower()
                        for x in gkey.replace("_", " ").replace("+", " ").split()
                    ]
                    if w in gwords:
                        picked[gkey] = str(zh)
    # 硬截断，绝不把整张术语表塞进去
    if len(picked) > limit:
        picked = dict(list(picked.items())[:limit])
    return picked


def build_messages(
    names: List[str],
    *,
    plugin: str = "",
    effect: str = "",
    glossary: Optional[Dict[str, Dict[str, object]]] = None,
    style: str = STYLE_CONCISE,
) -> List[Dict[str, str]]:
    """构造一次请求的 messages。"""
    gl = _pick_glossary(names, glossary or {})
    gl_text = "\n".join("%s => %s" % (k, v) for k, v in gl.items()) or "（无）"

    context = []
    if plugin:
        context.append("插件：%s" % plugin)
    if effect:
        context.append("效果：%s" % effect)
    context.append("风格：%s" % STYLE_LABEL.get(style, STYLE_LABEL[STYLE_CONCISE]))

    # ★ 2026-10-23：把"条数必须相等"和"zh 必须是中文"写进 user 里再说一遍。
    #   模型对 system 后半段的注意力会衰减，重复一次成本极低、效果明显。
    user = (
        "\n".join(context)
        + "\n\n术语表：\n" + gl_text
        + "\n\n待译（JSON，共 %d 条）：\n" % len(names) + _json_list(names)
        + "\n\n请输出同样结构的 JSON 数组，键名用 en / zh。"
        + "\n输出必须正好 %d 条，与输入一一对应、顺序一致。" % len(names)
        + "\n每条的 zh 必须是能读懂的中文（可以带厂商前缀），"
        + "禁止只给字母缩写（如 BC / BCC）。"
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
        {"role": "assistant", "content": _json_list_entries(_FEWSHOT)},
        {"role": "user", "content": "以上是格式示例。现在翻译上面「待译」里的全部条目。"},
    ]


def _json_list(names: List[str]) -> str:
    import json

    return json.dumps([{"en": n} for n in names], ensure_ascii=False)


def _json_list_entries(items: List[Dict[str, str]]) -> str:
    import json

    return json.dumps(items, ensure_ascii=False)


def make_messages_simple(names: List[str]) -> List[Dict[str, str]]:
    """最简版本（测试/自检用）。"""
    return build_messages(names)
