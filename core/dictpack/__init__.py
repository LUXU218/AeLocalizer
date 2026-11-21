"""AeLocalizer 词典包（.aeloc）读写。"""
from .pack import DictPack, PackError, build_pack, read_pack, write_pack

__all__ = ["DictPack", "PackError", "build_pack", "read_pack", "write_pack"]
