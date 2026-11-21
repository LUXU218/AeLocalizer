"""Shared format key for newly generated dictionaries and optional legacy imports."""

import os


DICT_KEY = (os.environ.get("AELOC_LEGACY_DICT_KEY") or "AeLocalizer-dictionary-format-v1").encode("utf-8")
