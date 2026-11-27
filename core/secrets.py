"""API Key 的安全存储（Windows DPAPI 加密）。

★ 需求（2026-10-22）：
  Key 要能保存住，但绝不能明文落盘。

★ 为什么用 DPAPI 而不是自己造 AES：
  · CryptProtectData 由系统托管密钥，密钥绑定【当前 Windows 用户】
  · 拷到别的机器 / 别的用户 -> 解不开（连同用户名一起被保护）
  · 不需要在程序里存放任何主密钥，避免"密钥写死在代码里"的伪加密
  · Windows 原生 API，无第三方依赖

★ 存储形态：
  %LOCALAPPDATA%\\AeLocalizer\\ai_key.dat
  内容 = b"LXK1" + DPAPI 密文（二进制，绝不含明文）
  换 Key 时旧密文会留一份：ai_key.dat.bak（同样只有密文）

★ 安全约定：
  · 任何日志/异常信息都不得包含 Key 原文
  · 明文只存在于进程内存，退出即随对象回收
"""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes as w
from typing import Tuple

#: 文件魔数（用于识别与版本演进）
MAGIC = b"LXK1"

#: 上一个 Key 的备份后缀。
#:
#: 测试曾在默认数据目录覆盖已保存的 DPAPI 密文。更换密钥前备份旧密文，
#: 便于恢复上一份有效密钥。隔离测试目录的用法见 tests/test_backend.py。
BACKUP_SUFFIX = ".bak"

#: 默认存储路径
def default_path(data_root: str = "") -> str:
    if not data_root:
        data_root = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "AeLocalizer",
        )
    return os.path.join(data_root, "ai_key.dat")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes) -> tuple:
    """构造 DATA_BLOB，并返回需要保持引用的缓冲（防止被 GC）。"""
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _dpapi(protect: bool, data: bytes) -> bytes:
    """调用 CryptProtectData / CryptUnprotectData。"""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    blob_in, _keep = _make_blob(data)
    blob_out = _Blob()

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "AeLocalizer AI Key",     # 描述（写进密文，非敏感）
            None, None, None,
            0,                         # CRYPTPROTECT_UI_FORBIDDEN 可另加
            ctypes.byref(blob_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None, None, None, None,
            0,
            ctypes.byref(blob_out),
        )

    if not ok:
        err = ctypes.get_last_error()
        raise OSError("DPAPI %s 失败 (code=%d)" % (
            "加密" if protect else "解密", err))

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        # 释放系统分配的内存（明文/密文都可能在内，必须清）
        try:
            kernel32.LocalFree(blob_out.pbData)
        except Exception:
            pass


def protect(text: str) -> bytes:
    """把字符串加密成字节串。"""
    return _dpapi(True, text.encode("utf-8"))


def unprotect(blob: bytes) -> str:
    """把加密字节串还原成字符串。"""
    return _dpapi(False, blob).decode("utf-8", "replace")


# ---------------------------------------------------------------- 存取


def save_key(key: str, path: str = "", keep_previous: bool = True) -> Tuple[bool, str]:
    """加密保存 Key。返回 (成功, 说明)。

    ⚠ 返回的说明里【绝不包含】Key 原文。

    keep_previous=True 时，如果磁盘上已经有一条**不同的、能解开**的 Key，
    先把旧密文备份到 ``ai_key.dat.bak``，再写新的。
    这样"被别的程序/测试覆盖"时还能用 restore_previous() 救回来。

    ★ 空 Key 一律拒绝写入 —— 绝不能用空串覆盖掉已保存的 Key。
    """
    key = (key or "").strip()
    if not key:
        return False, "Key 为空"
    p = path or default_path()
    try:
        blob = protect(key)
    except Exception as exc:
        return False, "加密失败：%s" % str(exc)[:80]
    # ---- 覆盖前先留一份旧密文 ----
    if keep_previous:
        try:
            old = load_key(p)
            if old and old != key:
                bak = p + BACKUP_SUFFIX
                shutil.copyfile(p, bak)
                try:
                    import stat

                    os.chmod(bak, stat.S_IREAD | stat.S_IWRITE)
                except Exception:
                    pass
        except OSError:
            pass  # 备份失败不能阻止正常保存
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(MAGIC + blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        # 收紧权限：只允许当前用户读写（防同机其他账户窥探）
        try:
            import stat

            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        except Exception:
            pass
        return True, "已加密保存"
    except Exception as exc:
        return False, "写入失败：%s" % str(exc)[:80]


def load_key(path: str = "") -> str:
    """读取并解密 Key。失败返回空串（不抛异常、不打印内容）。"""
    p = path or default_path()
    if not os.path.exists(p):
        return ""
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    if not raw.startswith(MAGIC):
        # 不是我们的格式（可能是早期明文文件）-> 拒绝读取，提示用户重设
        return ""
    try:
        return unprotect(raw[len(MAGIC):])
    except Exception:
        return ""


def has_key(path: str = "") -> bool:
    return bool(load_key(path))


def clear_key(path: str = "") -> bool:
    """删除已保存的 Key（连同上一个 Key 的备份一起删）。"""
    p = path or default_path()
    ok = True
    for f in (p, p + BACKUP_SUFFIX):
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            ok = False
    return ok


# ---------------------------------------------------------------- 上一个 Key


def previous_key(path: str = "") -> str:
    """读备份里的上一个 Key。没有/解不开返回空串。"""
    p = path or default_path()
    bak = p + BACKUP_SUFFIX
    if not os.path.exists(bak):
        return ""
    try:
        with open(bak, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    if not raw.startswith(MAGIC):
        return ""
    try:
        return unprotect(raw[len(MAGIC):])
    except Exception:
        return ""


def has_previous(path: str = "") -> bool:
    return bool(previous_key(path))


def restore_previous(path: str = "") -> str:
    """把备份里的上一个 Key 恢复成当前 Key。返回恢复出来的 Key（失败为空串）。

    ★ 恢复时 keep_previous=False：否则会把"当前被覆盖的假 Key"又备份一遍，
      反而把真正的旧备份冲掉。
    """
    p = path or default_path()
    old = previous_key(p)
    if not old:
        return ""
    ok, _ = save_key(old, p, keep_previous=False)
    return old if ok else ""


def path_hint(path: str = "") -> str:
    """给界面显示的位置说明。"""
    return path or default_path()
