# -*- coding: utf-8 -*-
"""popup 原文持久键必须包含 AE/插件身份，不能跨版本复用。"""
import os
import tempfile
import unittest

from runtime.popup_session import PopupSession


class PopupIdentityTest(unittest.TestCase):
    def test_identity_namespaces_do_not_cross_reuse(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_popupid_")
        try:
            a = PopupSession(1, tmp, "", identity="pluginA")
            a.baseline[0x10] = {"raw": b"Off|On", "last": b"\xb9\xd8|On",
                                "capacity": 7}
            a.baseline_key[0x10] = ("BCC", "p", "shaA")
            a._sync_persist()
            self.assertIn(a._pkey("BCC", "p", "shaA"), a.persist)
            b = PopupSession(1, tmp, "", identity="pluginB")
            self.assertNotIn(b._pkey("BCC", "p", "shaA"), b.persist)
            # unrelated plugin selection change doesn't alter A's plugin key.
            c = PopupSession(1, tmp, "", identity="pluginA")
            self.assertIn(c._pkey("BCC", "p", "shaA"), c.persist)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
