# -*- coding: utf-8 -*-
"""aejsx 名字列表解析：脚本输出的 "E\t名字" / "P\t名字" 必须去掉前缀。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import aejsx  # noqa: E402


class TestProjectNames(unittest.TestCase):
    def test_strips_tags_and_dedups(self):
        raw = "E\tCC Ball Action\nP\tBall Size\nP\tBall Size\nP\tRotation Axis\nERR x\n\n"
        old = aejsx.run_script
        aejsx.run_script = lambda *a, **k: (True, raw)
        try:
            names = aejsx.project_names("C:/fake/AfterFX.exe")
        finally:
            aejsx.run_script = old
        self.assertEqual(names, ["CC Ball Action", "Ball Size", "Rotation Axis"])

    def test_leading_space_kept(self):
        raw = "E\t BCC+Ambient Light\n"
        old = aejsx.run_script
        aejsx.run_script = lambda *a, **k: (True, raw)
        try:
            names = aejsx.project_names("C:/fake/AfterFX.exe")
        finally:
            aejsx.run_script = old
        # 名字本身可能有前导空格(BCC 就是), 但外层空白要去掉
        self.assertEqual(names, ["BCC+Ambient Light"])

    def test_failure_returns_empty(self):
        old = aejsx.run_script
        aejsx.run_script = lambda *a, **k: (False, "超时")
        try:
            self.assertEqual(aejsx.project_names("C:/fake/AfterFX.exe"), [])
        finally:
            aejsx.run_script = old


if __name__ == "__main__":
    unittest.main()
