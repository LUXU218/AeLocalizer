# -*- coding: utf-8 -*-
"""参数实时通道的 typed baseline。

只存身份 + 原始英文文本，不存地址/指针；地址每次从当前参数树重读。
中文显示但无 baseline 的实例不会被“全局反查”猜成英文。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

SCHEMA = 1


def _cjk(s: str) -> bool:
    return any("\u3400" <= c <= "\u9fff" for c in (s or ""))


class ParamBaseline:
    def __init__(self, data_root: str):
        self.root = str(data_root)
        self.path = os.path.join(self.root, "param_baseline.json")
        self._lock = threading.RLock()
        self.identity = {}
        self.entries = {}
        self.unknown = []
        self._unknown_keys = set()
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                obj = json.load(f)
            if obj.get("schema") == SCHEMA:
                self.identity = obj.get("identity") or {}
                self.entries = obj.get("entries") or {}
                self.unknown = obj.get("unknown") or []
                self._unknown_keys = {
                    (u.get("effect"), u.get("param"), u.get("reason"))
                    for u in self.unknown if isinstance(u, dict)}
        except Exception:
            pass

    def save(self):
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            obj = {"schema": SCHEMA, "identity": self.identity,
                   "entries": self.entries, "unknown": self.unknown[-200:]}
            fd, tmp = tempfile.mkstemp(prefix="param_baseline.", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def set_identity(self, identity: dict):
        """身份变化时清空当前可用账本；旧数据绝不能跨 AE/插件 hash 误用。"""
        with self._lock:
            if identity and identity != self.identity:
                self.identity = dict(identity)
                self.entries = {}
                self.unknown = []
                self._unknown_keys = set()
                self.save()

    def rebind(self, identity: dict):
        """一次干净初始化：显式换 identity 并清空旧账本。"""
        with self._lock:
            self.identity = dict(identity or {})
            self.entries = {}
            self.unknown = []
            self._unknown_keys = set()
            self.save()

    def identity_ok(self, identity: dict) -> bool:
        return not self.identity or not identity or self.identity == identity

    @staticmethod
    def key(effect: str, param: str) -> str:
        return "%s\x1f%s" % (effect or "", param or "")

    def get(self, effect: str, param: str):
        e = self.entries.get(self.key(effect, param))
        return dict(e) if isinstance(e, dict) else None

    def capture_rows(self, rows, *, overwrite: bool = False) -> dict:
        """从当前 rows 捕获原始英文；只接受非中文 display，绝不猜。"""
        added = skipped_cjk = conflicts = 0
        with self._lock:
            for row in rows or ():
                effect = str(row.get("effectMatchName") or "").strip()
                param = str(row.get("matchName") or "").strip()
                # 原文空白是 Sapphire/BCC 名称的一部分，绝不能 strip。
                name = str(row.get("name") or "")
                if not effect or not param or not name:
                    continue
                if _cjk(name):
                    skipped_cjk += 1
                    # 已有英文账本的中文显示是正常的“已应用”状态，不算缺失。
                    if self.get(effect, param):
                        continue
                    ukey = (effect, param, "display is CJK; needs clean baseline")
                    if ukey not in self._unknown_keys and len(self.unknown) < 200:
                        self._unknown_keys.add(ukey)
                        self.unknown.append({"effect": effect, "param": param,
                                             "reason": ukey[2]})
                    continue
                k = self.key(effect, param)
                old = self.entries.get(k)
                if old and old.get("english") and old["english"] != name and not overwrite:
                    conflicts += 1
                    continue
                if old and old.get("english") == name:
                    continue
                self.entries[k] = {
                    "effect": effect, "param": param,
                    "english": name, "pfName": str(row.get("pfName") or ""),
                    "guid": str(row.get("guid") or ""),
                    "zh": str(old.get("zh") or "") if old else "",
                }
                added += 1
            if added or skipped_cjk or conflicts:
                self.save()
        return {"added": added, "skipped_cjk": skipped_cjk,
                "conflicts": conflicts, "unknown": len(self.unknown)}

    def mark_chinese(self, effect: str, param: str, zh: str):
        """批量调用后由调用方 flush；不要每行 fsync 整个 JSON。"""
        with self._lock:
            k = self.key(effect, param)
            e = self.entries.get(k)
            if e:
                e["zh"] = str(zh or "")

    def flush(self):
        with self._lock:
            self.save()

    def english_for(self, effect: str, param: str, current: str = "") -> str:
        e = self.get(effect, param)
        if not e:
            return ""
        en = str(e.get("english") or "")
        if not en or _cjk(en):
            return ""
        if current and current not in (en, str(e.get("zh") or "")):
            return ""
        return en

    def param_names(self) -> set:
        return {str(e.get("english") or "") for e in self.entries.values()
                if e.get("english")}

    def missing_count(self, rows) -> int:
        n = 0
        for row in rows or ():
            if not self.get(row.get("effectMatchName") or "",
                            row.get("matchName") or ""):
                n += 1
        return n
