"""自诊断：找出汉化失败的原因，给出可执行建议。

实测的三类汉化失败：
  ① 词典缺词（1375 次）—— 参数名不在词典里
  ② 布局未落定（探测期不写入）—— 刚启动时的前几个效果
  ③ 写入未生效（读回=None，1576 次）—— 多副本 / 静态数据

本模块把引擎日志翻译成人话，并给出下一步动作。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List

#: 内部占位名（AE 自动生成，不该翻）
PLACEHOLDER = re.compile(
    r"^(Generic (Checkbox|Slider|Color|Point|Float|Layer|Menu|Angle)"
    r"|Float(\w*) \d+|Bool \d+|Int(\w*) \d+|Checkbox$|Slider$|"
    r"Floatxy \d+ [XY]$|Point \d+$|Color \d+$|Intxy \d+ [XY]$|"
    r"Norm Scalexy \d+ [XY]$|Mocha Data\d+$|Layer \d+$|"
    r"Menu \d+$|Group \d+$|Angle \d+$)"
)


@dataclass
class Diagnosis:
    """一次汉化诊断的结果。"""

    writes: int = 0
    verified: int = 0
    readback_fail: int = 0
    missing: int = 0
    missing_real: List[str] = field(default_factory=list)
    placeholder: int = 0
    layout_ok: bool = False
    enum_no_ptr: int = 0
    enum_shrunk: int = 0

    @property
    def success_rate(self) -> float:
        if not self.writes:
            return 0.0
        return self.verified / self.writes

    def advice(self) -> List[str]:
        """给人看的下一步建议。"""
        out: List[str] = []
        if not self.layout_ok:
            out.append("布局尚未落定：在 AE 里打开任一效果（如 Sapphire）再试一次，")
            out.append("  引擎需要观察几次注册才能确定内存偏移。")
        if self.missing_real:
            out.append("有 %d 个参数名不在词典里，可在「词典」页补译后重启引擎："
                       % len(self.missing_real))
            for s in self.missing_real[:8]:
                out.append("    %s" % s)
            if len(self.missing_real) > 8:
                out.append("    …… 还有 %d 个" % (len(self.missing_real) - 8))
        if self.readback_fail > self.verified:
            # ★ 2026-10-29 实测修正:读回=None ≠ 未生效 —— Sapphire/Trapcode
            #   这类参数名在内存里有多份堆副本,引擎写入成功但读回指针跟不过去,
            #   界面实际显示以写入为准(用户截图实锤:读回失败的参数全部已中文)。
            #   原文案"写入多数未生效"是吓人的误报。
            out.append("有 %d 次写入无法读回校验 —— 该插件的参数名在内存里"
                       "有多份堆副本(如 Sapphire/Trapcode),写入通常已生效,"
                       "以 AE 界面实际显示为准;" % self.readback_fail)
            out.append("    若界面确实还是英文:把该效果从图层删掉重新添加"
                       "(或重启 AE,保持引擎开着),注册时会重新翻译。")
        if not out:
            out.append("一切正常。")
        return out

    def summary(self) -> str:
        return ("写入 %d 次，校验通过 %d（%.0f%%），未命中 %d 个词"
                % (self.writes, self.verified, self.success_rate * 100,
                   self.missing))


def is_placeholder(name: str) -> bool:
    """AE 自动生成的占位名。"""
    return bool(PLACEHOLDER.search(name))


def diagnose(log_path: str) -> Diagnosis:
    """从引擎日志做诊断。"""
    d = Diagnosis()
    if not os.path.exists(log_path):
        return d

    miss = set()
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[写入]" in line:
                d.writes += 1
                if "bytes ok" in line:
                    d.verified += 1
                elif "读回=None" in line:
                    d.readback_fail += 1
            elif "[未命中]" in line:
                m = re.search(r"\[未命中\] (.+?)\s+\(ptr=", line)
                if m:
                    miss.add(m.group(1).strip())
            elif "[布局确定]" in line:
                d.layout_ok = True
            elif "[枚举诊断]" in line:
                if "no-ptr" in line:
                    d.enum_no_ptr += 1
                elif "shrunk" in line:
                    d.enum_shrunk += 1

    for s in miss:
        if is_placeholder(s):
            d.placeholder += 1
        elif 2 <= len(s) <= 60:
            d.missing_real.append(s)

    d.missing = len(miss)
    d.missing_real.sort()
    return d
