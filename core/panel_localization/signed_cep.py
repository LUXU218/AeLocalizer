"""Build a locally signed CEP copy with its own extension identity."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .panel_files import digest, inside


SIGNER_HASH = "dc2e711a46504830062ac1afede1f8ee08c1e21399b6c30d7d38cf907c35bcf5"
COPY_PREFIX = "io.aelocalizer.localized."
OWNER_FILE = "aelocalizer-source.txt"
MENU_SUFFIX = " (简体中文)"


def verified_signer(path):
    path = Path(path).absolute()
    if not path.is_file() or path.stat().st_size != 5518896 or digest(path.read_bytes()) != SIGNER_HASH:
        raise ValueError("请选择 Adobe 官方 Windows 64 位 ZXPSignCmd 4.1.103")
    return path


def _cep_root():
    return Path(os.environ["APPDATA"]) / "Adobe" / "CEP" / "extensions"


def _owned_copy(root, target):
    destination = inside(root, COPY_PREFIX + "p" + target.key[:24])
    if destination.exists():
        marker = inside(destination, OWNER_FILE)
        if not marker.is_file() or marker.read_text(encoding="ascii") != target.key:
            raise ValueError("汉化副本目录已被占用，请检查同名 CEP 扩展")
    return destination


def is_installed(target):
    return _owned_copy(_cep_root(), target).is_dir()


def remove_copy(target):
    root = _cep_root()
    destination = _owned_copy(root, target)
    if destination.is_dir():
        with tempfile.TemporaryDirectory(prefix="aeloc-cep-", dir=root.parent) as scratch:
            destination.rename(Path(scratch) / "previous")


def _manifest_for_copy(path, bundle_id, name):
    tree = ET.parse(path)
    root = tree.getroot()
    root.set("ExtensionBundleId", bundle_id)
    ids = {}
    for element in tree.iter("Extension"):
        identifier = element.get("Id")
        if identifier:
            element.set("Id", ids.setdefault(identifier, f"{bundle_id}.{len(ids) + 1}"))
    menus = list(tree.iter("Menu"))
    if not menus or not ids:
        raise ValueError("扩展清单没有可安装的独立面板菜单")
    for menu in menus:
        menu.text = (menu.text or name).strip() + MENU_SUFFIX
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return "、".join(dict.fromkeys(menu.text for menu in menus))


def _sign(signer, source, scratch):
    certificate = scratch / "local.p12"
    package = scratch / "localized.zxp"
    password = secrets.token_hex(24)
    commands = (
        (["-selfSignedCert", "US", "Local", "AeLocalizer", "Local CEP", password, str(certificate)], "创建本地证书"),
        (["-sign", str(source), str(package), str(certificate), password], "签名汉化副本"),
        (["-verify", str(package), "-skipOnlineRevocationChecks"], "校验汉化副本签名"),
    )
    for arguments, action in commands:
        result = subprocess.run([str(signer), *arguments], capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError(f"Adobe ZXPSignCmd 无法{action}（退出码 {result.returncode}）")
    return package


def install_copy(localizer, report, translations, signer):
    target = localizer.target
    if target.kind != "cep" or not target.signed or target.identifier.lower().startswith(("com.adobe.", COPY_PREFIX)):
        raise ValueError("仅支持带签名的第三方 CEP 原版扩展")
    signer = verified_signer(signer)
    changes = localizer.preview(report, translations, allow_signed=True)
    root = _cep_root()
    destination = _owned_copy(root, target)
    root.mkdir(parents=True, exist_ok=True)
    original = Path(target.root)

    def copy_ignore(directory, names):
        relative = Path(directory).relative_to(original)
        skipped = {name for name in names if name.lower() in ("meta-inf", ".debug", "mimetype")} if not relative.parts else set()
        for name in set(names) - skipped:
            inside(original, relative / name)
        return skipped

    with tempfile.TemporaryDirectory(prefix="aeloc-cep-", dir=root.parent) as directory:
        scratch = Path(directory)
        source = scratch / "source"
        shutil.copytree(original, source, ignore=copy_ignore)
        for relative, expected in report["hashes"].items():
            if digest(inside(source, relative).read_bytes()) != expected:
                raise ValueError("复制时扩展文件已变化，请重新读取界面文字")
        for relative, data in changes.items():
            inside(source, relative).write_bytes(data)
        menus = _manifest_for_copy(inside(source, target.entry), destination.name, target.name)
        (source / OWNER_FILE).write_text(target.key, encoding="ascii")
        package = _sign(signer, source, scratch)
        prepared = scratch / "prepared"
        prepared.mkdir()
        with ZipFile(package) as archive:
            if "META-INF/signatures.xml" not in archive.namelist():
                raise ValueError("签名包缺少 CEP 签名文件")
            for item in archive.infolist():
                file = inside(prepared, item.filename)
                if item.is_dir():
                    file.mkdir(parents=True, exist_ok=True)
                else:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as stream, file.open("xb") as output:
                        shutil.copyfileobj(stream, output)
        # Recheck ownership after signing, just before replacing a previously installed copy.
        previous = scratch / "previous"
        if _owned_copy(root, target).exists():
            destination.rename(previous)
        try:
            prepared.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    return menus
