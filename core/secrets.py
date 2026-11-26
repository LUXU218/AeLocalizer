"""使用 Windows DPAPI 保存 API Key。

应用在当前 Windows 用户的上下文调用 CryptProtectData，由系统管理加密密钥。
默认的 ai_key.dat 存放 LXK1 文件标记和 DPAPI 密文。更换密钥时备份旧密文。
保存路径只写入加密结果，日志应省略密钥。加解密和发送请求时会使用
明文缓冲区，普通的内存释放不会清零缓冲区。
"""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes as w
from typing import Tuple

#: 文件魔数（用于识别与版本演进）
MAGIC = b"LXK1"

#: 保存新 Key 时备份上一份可解密的密文。测试须使用隔离的数据目录。
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
        # 释放 DPAPI 分配的缓冲区。
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
        # Windows 上 chmod 在这里清除只读属性，文件访问权限由 ACL 决定。
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

    恢复时使用 keep_previous=False，以保留用于恢复的旧密文备份。
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
