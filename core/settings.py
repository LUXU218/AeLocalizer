"""用户设置持久化（当前选的 AE 版本等）。

重启后应保留上次选中的 AE 版本。总取安装列表首项会将 AE 2025 切回 2026。

  原因：Backend.__init__ 里写的是
        self._version = self._installs[0][0]    # 永远取排在最前面的（2026）
  根本没有把用户选过的版本存下来。

  这里补一个极简的 settings.json，只存少量标量。
  ★ 读写都必须容错：文件被写坏 / 权限不足时静默回退默认值，
    绝不能因为设置文件坏了导致软件起不来。
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

FILE_NAME = "settings.json"


def settings_path(data_root: str) -> str:
    return os.path.join(data_root, FILE_NAME)


def load(data_root: str) -> Dict[str, Any]:
    """读设置。任何异常都返回空字典（回退默认行为）。"""
    p = settings_path(data_root)
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data_root: str, obj: Dict[str, Any]) -> bool:
    """原子写设置。失败返回 False（不影响主流程）。"""
    p = settings_path(data_root)
    try:
        os.makedirs(data_root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=data_root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


def get(data_root: str, key: str, default: Any = None) -> Any:
    return load(data_root).get(key, default)


def put(data_root: str, key: str, value: Any) -> bool:
    obj = load(data_root)
    if obj.get(key) == value:
        return True
    obj[key] = value
    return save(data_root, obj)