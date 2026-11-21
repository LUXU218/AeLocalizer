"""构建参数辅助原生模块(方案 D):native/*.c → %TEMP% 外的 native/*.dll。

用法(普通用户权限即可,产物进仓库 native/):
    python tools/build_native.py

使用 64 位 MinGW-w64 的 GCC 兼容编译器。
产物只在本机验证过布局的构建上由请求执行器启用——DLL 本身按 FLT/TDB
SHA-256 自查,不匹配就拒绝工作。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "native"

TARGETS = [
    ("ae_parameters.c", "ae_parameters.dll", ["-DAE_DYNAMIC"]),
    ("ae_host_identity.c", "ae_host_identity.dll", []),
    # 2026-11-01 性能重写:词典密钥流加解密 + 跨进程批量读
    ("aeloc_cipher.c", "aeloc_cipher.dll", []),
    ("aeloc_rpm.c", "aeloc_rpm.dll", []),
]


def find_compiler() -> list:
    for name in ("gcc", "x86_64-w64-mingw32-gcc"):
        path = shutil.which(name)
        if path:
            return [path]
    raise RuntimeError(
        "找不到 MinGW-w64 GCC。请把 64 位工具链的 bin 目录加入 PATH。"
    )


def build() -> int:
    cc = find_compiler()
    print("编译器:", cc[0])
    ok = 0
    for source, dll, extra in TARGETS:
        src = OUT / source
        dst = OUT / dll
        cmd = [*cc, "-O2", "-Wall", "-shared", "-municode", *extra,
               str(src), "-o", str(dst), "-lkernel32", "-luser32", "-ladvapi32"]
        print(">>", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not dst.exists():
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            print(f"✘ {source}")
            continue
        warnings = [ln for ln in (proc.stderr or "").splitlines() if "warning" in ln.lower()]
        for w in warnings:
            print("  !", w)
        print(f"✔ {dll}({dst.stat().st_size} 字节)")
        ok += 1
    return ok


if __name__ == "__main__":
    done = build()
    if done != len(TARGETS):
        sys.exit(1)
    print("全部原生模块构建完成")
