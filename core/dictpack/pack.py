"""词典包 .aeloc 的构建、读取、校验。

设计要点（对应 spec/dictpack-v1.md）：
  · 容器 = ZIP，内含 manifest.json / dict.json / 可选 glossary.json / LICENSE / README / icon
  · 完整性 = 词典数据与已登记文件的 SHA-256
  · 签名   = 可选 Ed25519，有签名时需通过验证
  · 安全   = 拒绝 zip-slip、拒绝执行、大小与条目上限
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

SCHEMA = "aeloc-dict/1"
GLOSSARY_SCHEMA = "aeloc-glossary/1"
MAX_PACK_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 200_000
ALLOWED_NAMES = {"manifest.json", "dict.json", "glossary.json", "LICENSE.txt", "README.md", "icon.png"}

_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")


class PackError(Exception):
    """词典包格式/校验错误。"""


@dataclass
class DictPack:
    manifest: Dict[str, Any]
    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    glossary: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    extras: Dict[str, bytes] = field(default_factory=dict)
    glossary_present: bool = False

    @property
    def pack_id(self) -> str:
        return str(self.manifest.get("id", ""))

    @property
    def name(self) -> str:
        return str(self.manifest.get("name", self.pack_id or "未命名词典"))

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "0.0.0"))

    @property
    def signed(self) -> bool:
        return "signature" in self.manifest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Any) -> bytes:
    """稳定序列化，用于签名：排序键、无多余空白、UTF-8。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_entry(key: str, value: Dict[str, Any]) -> None:
    if not key or not isinstance(key, str):
        raise PackError(f"词条键必须是非空字符串：{key!r}")
    if not isinstance(value, dict) or "zh" not in value:
        raise PackError(f"词条 {key!r} 缺少 zh 字段")
    zh = value["zh"]
    if not isinstance(zh, str) or not zh:
        raise PackError(f"词条 {key!r} 的 zh 必须是非空字符串")
    if _BAD_CHARS.search(zh):
        raise PackError(f"词条 {key!r} 的译名含控制字符或替换字符")
    if "priority" in value and not isinstance(value["priority"], int):
        raise PackError(f"词条 {key!r} 的 priority 必须是整数")


def _safe_zip_name(name: str) -> bool:
    if name not in ALLOWED_NAMES:
        return False
    if name.startswith("/") or ".." in name or "\\" in name:
        return False
    return True


def build_pack(
    *,
    pack_id: str,
    name: str,
    version: str,
    author: str,
    entries: Dict[str, Dict[str, Any]],
    glossary: Optional[Dict[str, Dict[str, Any]]] = None,
    license_text: str = "",
    readme: str = "",
    icon_png: Optional[bytes] = None,
    applies_to: Optional[Dict[str, Any]] = None,
    sign_key: Optional[bytes] = None,
    license_id: str = "",
    description: str = "",
    created: str = "",
) -> bytes:
    """构建 .aeloc 字节流。sign_key 为 ed25519 私钥原文（32 字节）时进行签名。"""
    if len(entries) > MAX_ENTRIES:
        raise PackError(f"词条数超过上限 {MAX_ENTRIES}")
    for k, v in entries.items():
        _validate_entry(k, v)

    dict_bytes = _canonical({"schema": SCHEMA, "entries": entries})
    files_meta: Dict[str, Dict[str, Any]] = {
        "dict.json": {"sha256": _sha256(dict_bytes), "bytes": len(dict_bytes)}
    }

    payload: Dict[str, bytes] = {"dict.json": dict_bytes}
    if glossary:
        g_bytes = _canonical({"schema": GLOSSARY_SCHEMA, "entries": glossary})
        payload["glossary.json"] = g_bytes
        files_meta["glossary.json"] = {"sha256": _sha256(g_bytes), "bytes": len(g_bytes)}
    if license_text:
        payload["LICENSE.txt"] = license_text.encode("utf-8")
    if readme:
        payload["README.md"] = readme.encode("utf-8")
    if icon_png:
        payload["icon.png"] = icon_png

    manifest: Dict[str, Any] = {
        "schema": SCHEMA,
        "id": pack_id,
        "name": name,
        "version": version,
        "author": author,
        "license": license_id,
        "created": created,
        "updated": created,
        "description": description,
        "appliesTo": applies_to or {"aeVersions": [], "plugins": [], "encoding": "auto"},
        "stats": {"entries": len(entries), "glossary": len(glossary or {})},
        "files": files_meta,
    }

    if sign_key:
        manifest["signature"] = _sign(manifest, payload, sign_key)

    manifest_bytes = _canonical(manifest)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        for n, b in payload.items():
            zf.writestr(n, b)
    data = buf.getvalue()
    if len(data) > MAX_PACK_BYTES:
        raise PackError(f"包体积超过上限 {MAX_PACK_BYTES} 字节")
    return data


def _sign(manifest: Dict[str, Any], payload: Dict[str, bytes], key: bytes) -> Dict[str, str]:
    """ed25519 签名；cryptography 缺失时抛 PackError（调用方决定是否降级）。"""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except Exception as exc:  # pragma: no cover
        raise PackError("缺少 cryptography，无法签名") from exc

    import base64

    sk = Ed25519PrivateKey.from_private_bytes(key)
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    h = hashlib.sha256()
    h.update(_canonical({k: v for k, v in manifest.items() if k != "signature"}))
    for n in sorted(payload):
        h.update(n.encode())
        h.update(payload[n])
    sig = sk.sign(h.digest())
    return {
        "alg": "ed25519",
        "publicKey": base64.b64encode(pub).decode(),
        "sig": base64.b64encode(sig).decode(),
    }


def verify_pack(pack: DictPack) -> tuple[bool, str]:
    """校验完整性（必然）与签名（若存在）。返回 (ok, message)。"""
    payload = dict(pack.extras)
    payload["dict.json"] = _canonical({"schema": SCHEMA, "entries": pack.entries})
    if pack.glossary_present or pack.glossary:
        payload["glossary.json"] = _canonical(
            {"schema": GLOSSARY_SCHEMA, "entries": pack.glossary}
        )

    files = pack.manifest.get("files")
    if not isinstance(files, dict) or "dict.json" not in files:
        return False, "缺少 dict.json 的 SHA-256 校验信息"
    if "glossary.json" in payload and "glossary.json" not in files:
        return False, "缺少 glossary.json 的 SHA-256 校验信息"
    for n, meta in files.items():
        if n not in payload:
            return False, f"缺少文件 {n}"
        if not isinstance(meta, dict) or not meta.get("sha256"):
            return False, f"缺少 {n} 的 SHA-256 校验信息"
        if _sha256(payload[n]) != meta["sha256"]:
            return False, f"{n} 校验失败（内容被修改）"

    if not pack.signed:
        return True, "未签名"
    sig = pack.manifest["signature"]
    if not isinstance(sig, dict) or sig.get("alg") != "ed25519":
        return False, "签名校验失败（签名格式或算法无效）"
    try:
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return False, "无法验证签名（缺少 cryptography）"

    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["publicKey"], validate=True))
        h = hashlib.sha256()
        h.update(_canonical({k: v for k, v in pack.manifest.items() if k != "signature"}))
        for n in sorted(payload):
            h.update(n.encode())
            h.update(payload[n])
        pub.verify(base64.b64decode(sig["sig"], validate=True), h.digest())
        return True, "签名有效"
    except Exception:
        return False, "签名校验失败（可能被篡改或公钥不符）"


def read_pack(data: bytes) -> DictPack:
    """解析 .aeloc 字节流。完整性与签名由 verify_pack 检查。"""
    if len(data) > MAX_PACK_BYTES:
        raise PackError(f"包体积超过上限 {MAX_PACK_BYTES} 字节")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackError("不是有效的 .aeloc（ZIP）文件") from exc

    names = zf.namelist()
    for n in names:
        if not _safe_zip_name(n):
            raise PackError(f"包内含非法条目：{n}")
    if "manifest.json" not in names:
        raise PackError("缺少 manifest.json")
    if "dict.json" not in names:
        raise PackError("缺少 dict.json")

    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise PackError(f"schema 不匹配：{manifest.get('schema')!r}，期望 {SCHEMA}")

    dict_obj = json.loads(zf.read("dict.json").decode("utf-8"))
    entries = dict_obj.get("entries") or {}
    if len(entries) > MAX_ENTRIES:
        raise PackError(f"词条数超过上限 {MAX_ENTRIES}")
    for k, v in entries.items():
        _validate_entry(k, v)

    glossary: Dict[str, Dict[str, Any]] = {}
    if "glossary.json" in names:
        g = json.loads(zf.read("glossary.json").decode("utf-8"))
        if g.get("schema") != GLOSSARY_SCHEMA:
            raise PackError("glossary.json schema 不匹配")
        glossary = g.get("entries") or {}

    extras = {n: zf.read(n) for n in names if n in ("LICENSE.txt", "README.md", "icon.png")}
    return DictPack(
        manifest=manifest, entries=entries, glossary=glossary, extras=extras,
        glossary_present="glossary.json" in names,
    )


def write_pack(pack_bytes: bytes, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(pack_bytes)
    os.replace(tmp, path)
