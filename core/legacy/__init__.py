"""旧版部署词典（C:\\ProgramData\\SapphireCN\\dict）的只读解析。"""
from .reader import LegacyDictError, read_dict_file, load_legacy_layer, LAYER_ORDER

__all__ = ["LegacyDictError", "read_dict_file", "load_legacy_layer", "LAYER_ORDER"]
