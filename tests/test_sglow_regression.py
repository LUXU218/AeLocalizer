import base64
from pathlib import Path
import tempfile
import unittest

from tests.sglow_regression import FIXTURE_SHA256, check_record, template


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII="
)


class SGlowRegressionTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        (self.base / "session.json").write_text("{}", encoding="utf-8")
        self.record = template()
        self.record.update(
            package_sha256="A" * 64, ae_version="24.1.0.78", session="test-ae-session",
            fixture_sha256_before=FIXTURE_SHA256, fixture_sha256_after=FIXTURE_SHA256,
            configuration={"effect": True, "param_enum": True, "source": "session.json"},
            requests={
                "on": {"mode": "on", "effect": True, "param_enum": True,
                       "intent": "start-1", "source": "session.json"},
                "off": {"mode": "stop", "effect": False, "param_enum": False,
                        "intent": "stop-2", "source": "session.json"},
            },
            workspace={"closed": True, "source": "session.json"},
            dictionary={"source": "session.json", "mappings": {
                "S_Glow": "S_辉光", "S_Mosaic": "S_马赛克", "Brightness": "亮度", "Screen": "滤色",
                "Pixel Frequency": "像素频率", "Luma": "亮度",
            }},
        )
        for phase, request in (("on", "on"), ("closed", "on"), ("off", "off")):
            self.record["acknowledgements"][phase].update(self.record["requests"][request],
                                                         session="test-ae-session")
        glow_en = ("AELOC_LANGUAGE_1", "S_Glow", "Brightness", "2.000", "Screen")
        mosaic_en = ("AELOC_LANGUAGE_0", "S_Mosaic", "Pixel Frequency", "40.00", "Luma")
        glow_zh = ("AELOC_LANGUAGE_1", "S_辉光", "亮度", "2.000", "滤色")
        mosaic_zh = ("AELOC_LANGUAGE_0", "S_Mosaic", "像素频率", "40.00", "亮度")
        for row, reading in zip(self.record["native"], (
            glow_en, mosaic_en, glow_zh, mosaic_zh, glow_zh, glow_zh, glow_en, mosaic_en, glow_en,
        ), strict=True):
            filename = f"{row['stage']}.png"
            (self.base / filename).write_bytes(PNG)
            row.update(zip(("layer", "title", "parameter", "value", "dropdown"), reading, strict=True))
            row["screenshot"] = filename

    def check(self):
        return check_record(self.record, self.base)

    def test_four_actions_pass_and_stop_can_keep_preferences(self):
        result = self.check()
        self.assertEqual(result["verdict"], "pass", result)
        self.assertEqual(len(result["screenshot_sha256"]), 9)

    def test_chinese_parameter_and_ready_ack_do_not_prove_effect_title(self):
        self.record["native"][2]["title"] = "S_Glow"
        result = self.check()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(len(result["failures"]), 1)
        self.assertIn("native.enabled_layer1.title", result["failures"][0])

    def test_language_receipt_without_effect_receipt_leaves_run_incomplete(self):
        self.record["acknowledgements"]["on"]["effect"] = None
        result = self.check()
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["missing"], ["acknowledgements.on.effect"])

    def test_close_needs_matching_session_intent_and_window_evidence(self):
        self.record["acknowledgements"]["closed"].update(session="different-session", intent="old-intent")
        self.record["workspace"]["closed"] = False
        result = self.check()
        self.assertEqual(result["verdict"], "fail")
        for key in ("acknowledgements.closed.session", "acknowledgements.closed.intent", "workspace.closed"):
            self.assertTrue(any(item.startswith(key) for item in result["failures"]))

    def test_switch_back_is_required_and_mosaic_cannot_take_glow_title(self):
        del self.record["native"][4]
        self.record["native"][3]["title"] = "S_辉光"
        result = self.check()
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("native.returned_layer1.screenshot", result["missing"])
        self.assertIn("native.switched_layer2.title", result["failures"][0])

    def test_stop_requires_original_display_and_unchanged_value(self):
        self.record["native"][-1].update(title="S_辉光", value="2.001", dropdown="滤色")
        result = self.check()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(len(result["failures"]), 3)

    def test_invalid_or_reused_capture_is_rejected(self):
        self.record["native"][1]["screenshot"] = self.record["native"][0]["screenshot"]
        (self.base / "closed_layer1.png").write_bytes(b"truncated")
        result = self.check()
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("separate capture" in item for item in result["failures"]))
        self.assertTrue(any("invalid or missing PNG" in item for item in result["failures"]))
