# -*- coding: utf-8 -*-
"""术语修正层（term_fix）—— 把已确认的错误译名钉成正确译法。

为什么要单独一层
----------------
守护（guard_uni / WIN.build_dict）按固定顺序合并多层词典，**先到先得**
（WIN.py 的 build_dict 用 setdefault）：

    term_fix -> extra_overrides -> dict_full_groundtruth
             -> param_names_zh -> dict_ae_groundtruth -> all_plugins

我们生成的目录（engine_dict/）里只有 extra_overrides。所以同一个错译
会在每次重建时从 extra_overrides 里冒出来；要让它彻底生效，必须写在
**优先级更高的 term_fix 层**，而不是反复去改 extra_overrides。

错译从哪来（2026-10-30 实测）
-----------------------------
守护的 JS 只做整串精确查表（DICT[nm]），**不存在分词/逐词拼接**；
"NR Color Strength -> 降噪" 这种日志就是词典里真有这条错条目。
错条目是历史 AI 批量产出 + 早期规则表留下的，与现提示词无关
（core/ai/prompt.py 的术语表已经是 Glow=辉光 / Matte=遮罩 这一套）。

修正依据（不是个人口味，是词典自己的多数派）
--------------------------------------------
拿 engine_dict/extra_overrides.json 统计同一词根的主流译法：
    Glow      辉光 113 : 发光 85   -> 统一 辉光
    Size      大小 355 : 尺寸 52   -> 统一 大小
    Ambient   环境  27 : 周围  2   -> 统一 环境
    Occlusion 遮挡   9 : 遮蔽  2   -> 统一 遮挡
    Specular  高光  34 : 镜面  1   -> 统一 高光
    Velocity  速度  88 : 速率 10   -> 统一 速度
    Point     点   355 : 要点  2   -> 统一 点
差得多的（Atmosphere 只有 2 条、NR 只有 3 条不成体系）按行业惯例定：
Atmosphere=大气（AE 里是"大气"类效果）、NR=降噪（Noise Reduction）。

刻意没动的
----------
* Norm Scale = 标准缩放：全库 520 条都是这个译法，只改少数几条会制造
  新的不一致；要改就得整族改，属于下一轮（见 docs/兼容性-AE2021-2023.md）。
* Tint = 色调：AE 自带 Tint 效果官方中文就叫"色调"，不是错译。
* Bypass Mocha = 绕过 Mocha：绕过/旁路都成立，不动。
"""
from __future__ import annotations

import os
from typing import Dict, Optional

#: 引擎这一层认的文件名（WIN.build_dict 的第一个来源）
TERM_FIX_BASE = "term_fix"
TERM_FIX_JSON = TERM_FIX_BASE + ".json"
TERM_FIX_DAT = TERM_FIX_BASE + ".dat"

#: 已确认的错误译名 -> 正确译名
#: 键必须与引擎拿到的英文原名逐字一致（大小写、空格都算）
TERM_FIXES: Dict[str, str] = {
    # ---- Glow 一族：全库多数派是"辉光"，"发光"是漏网的少数派 ----
    "Glow": "辉光",
    "Glow Color": "辉光颜色",
    "Glow Radius": "辉光半径",
    "Glow Width": "辉光宽度",
    "Glow Under LED": "LED 下方辉光",
    "Glow S2": "辉光 S2",
    # ---- 单词根被译错，但同族复合词是对的 ----
    "Ambient": "环境",              # Ambient Bright=环境亮度，只错基词
    "Occlusion": "遮挡",            # Occlusion From=遮挡来源
    "Specular": "高光",             # Specular Amount=高光强度
    "Specular Strength": "高光强度",
    "Velocity": "速度",             # 全库 88:10
    "Roughness": "粗糙度",          # 与 Roughness Scale=粗糙度缩放 对齐
    # Wrap 在 AE 的平铺类参数里是"拼贴"（词典里已有的整条枚举
    # "…|Wrap X|Wrap Y|…" -> "…|X 拼贴|Y 拼贴|…" 就是依据），
    # 统一轴向枚举中的译法，避免同系列混用「环绕」和「拼贴」。
    "Wrap X": "X 拼贴",
    "Wrap Y": "Y 拼贴",
    "Wrap Z": "Z 拼贴",
    "Blur Res": "模糊分辨率",        # 不是"品质"，Res=Resolution
    "Shutter Duration": "快门持续时间",  # 不是"拖尾长度"
    # ---- 多词短语被截断成半个词意 ----
    "NR Color Strength": "降噪颜色强度",
    "NR Passes": "降噪次数",
    "NR Style": "降噪样式",
    "Force Scene Reset": "强制重置场景",
    "Last parameter": "末个参数",
    "Inverse Warp": "反向扭曲",
    "Upsampling Method": "上采样方法",
    "Threshold Add Color": "阈值添加颜色",
    "Stroke Size": "描边大小",
    "Color Phase": "颜色相位",
    # ---- 中文里夹空格/语序不一致 ----
    "Center Scale Brightness": "中心缩放亮度",
    "Edge Scale Brightness": "边缘缩放亮度",
    "Occlusion Scale Brightness": "遮挡缩放亮度",
    "Light X": "灯光 X",
    "Light Y": "灯光 Y",
    # Size 本身要钉住：历史数据里出现过 "Size = 字数"（把 Size 当成字数统计），
    # 而同一份词典里 355 条都是"大小"。
    "Size": "大小",
    "Size Blue": "蓝色大小",
    "Size Green": "绿色大小",
    "Size Red": "红色大小",
    "Size Rel Far": "相对大小 远",
    "Size Rel Near": "相对大小 近",
    "Size Rel X": "相对大小 X",
    "Size Rel Y": "相对大小 Y",
    # ---- 编号类：要点 -> 点，空格统一 ----
    "Point 1 Enable": "点 1 启用",
    "Point 2 Enable": "点 2 启用",
    "Point 3 Enable": "点 3 启用",
    "Point 4 Enable": "点 4 启用",
    "Point1 Uses Mocha": "点 1 使用 Mocha",
    "Point2 Uses Mocha": "点 2 使用 Mocha",
    "Point3 Uses Mocha": "点 3 使用 Mocha",
    "Point4 Uses Mocha": "点 4 使用 Mocha",
    "Kernel Crop1": "内核裁剪 1",
    "Kernel Crop2": "内核裁剪 2",
    # ---- Atmosphere 一族：雾效/氛围/大气 三套混用，统一成"大气" ----
    "Atmosphere": "大气",
    "Atmosphere Amp": "大气幅度",
    "Atmosphere Darks": "大气暗部",
    "Atmosphere Detail": "大气细节",
    "Atmosphere Freq": "大气频率",
    "Atmosphere Lights": "大气灯光",
    "Atmosphere Seed": "大气种子",
}


def merged(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """返回最终要落盘的修正表（内置 + 调用方追加，后者覆盖前者）。"""
    out = dict(TERM_FIXES)
    if extra:
        for k, v in extra.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                out[k] = v
    return out


def write_term_fix(out_dir: str, extra: Optional[Dict[str, str]] = None) -> int:
    """把修正表写进引擎词典目录，返回条数。

    ★ 同时写 .json 与 .dat：引擎优先读 .dat（WIN._load_any 先看 .dat），
      但 .json 留着便于人工核对。写 .dat 必须用 core.legacy.writer，
      它产生的是引擎三代解密逻辑都能解的老格式。
    """
    data = merged(extra)
    os.makedirs(out_dir, exist_ok=True)
    import json

    path = os.path.join(out_dir, TERM_FIX_JSON)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)

    from .legacy.writer import write_dat

    write_dat(data, os.path.join(out_dir, TERM_FIX_DAT))
    return len(data)


def ensure_term_fix(out_dir: str, extra: Optional[Dict[str, str]] = None) -> int:
    """确保修正层已落盘且与当前代码一致，返回重写条数（0 = 无需重写）。

    ★ 给"升级用户"用：他们已经有一份旧引擎词典，不会为了修译名去重建，
      所以启动引擎前补一次即可生效；已是最新时零写入、零 IO 抖动。
    """
    if not out_dir or not os.path.isdir(out_dir):
        return 0
    data = merged(extra)
    if not os.path.exists(os.path.join(out_dir, TERM_FIX_DAT)):
        return write_term_fix(out_dir, extra)
    try:
        if read_term_fix(out_dir) == data:
            return 0
    except Exception:
        pass
    return write_term_fix(out_dir, extra)


def clear_term_fix(out_dir: str) -> bool:
    """删掉修正层（用于"全部还原"与测试清理）。"""
    ok = True
    for name in (TERM_FIX_JSON, TERM_FIX_DAT):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                ok = False
    return ok


def read_term_fix(out_dir: str) -> Dict[str, str]:
    """读回已落盘的修正层（诊断/测试用）。.dat 优先，与引擎一致。"""
    from .legacy.reader import read_dict_file

    dat = os.path.join(out_dir, TERM_FIX_DAT)
    if os.path.exists(dat):
        obj = read_dict_file(dat)
        if isinstance(obj, dict):
            return obj
    import json

    p = os.path.join(out_dir, TERM_FIX_JSON)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}
