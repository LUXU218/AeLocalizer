# -*- coding: utf-8 -*-
"""param/enum 通道级词典过滤，不按插件组一刀切。"""
import os
import tempfile
import unittest
from unittest import mock

from core import engine
from core.enginedict import build_engine_dict


class ChannelDictTest(unittest.TestCase):
    def test_drop_param_keeps_enum_payload(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_engine_channels_")
        try:
            entries = {"Host Layer": "宿主图层", "Grow Bounds": "扩展边界",
                       "Yes": "是", "No": "否"}
            r = build_engine_dict(entries, tmp, drop_keys={"Host Layer", "Grow Bounds"})
            import json
            with open(os.path.join(tmp, "extra_overrides.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.assertNotIn("Host Layer", data)
            self.assertNotIn("Grow Bounds", data)
            self.assertIn("Yes", data)
            self.assertTrue(r.enum_ready)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_request_reload_writes_flag_in_guard_out(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_reload_")
        try:
            with mock.patch.object(engine, "collect_dir", return_value=tmp):
                self.assertTrue(engine.request_reload())
            self.assertTrue(os.path.exists(os.path.join(tmp, "RELOAD_DICT.flag")))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
