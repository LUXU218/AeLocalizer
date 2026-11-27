import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_popup_codec as codec
from runtime.managed_guard import LanguageEngine

class PopupDisplayOnlyTests(unittest.TestCase):
    def test_discovered_renderer_keeps_existing_typed_popup_channel(self):
        from runtime import param_draw_hook, popup_session

        host = {"pid": 123, "ctime": 456}
        for profile, use_display in (
                ({"verified_build": False, "menu": {"descriptor_stride": 184}}, False),
                ({"verified_build": True, "menu": {}}, True),
                ({"verified_build": True, "supplemental": True}, False)):
            with self.subTest(profile=profile):
                engine = LanguageEngine("", "", "")
                draw = Mock(popup_profile_error="", popup_paint_profile=profile)
                popup = Mock()
                with patch.dict(sys.modules, {"param_draw_hook": param_draw_hook, "popup_session": popup_session}), \
                     patch.object(param_draw_hook, "ParamDrawHook", return_value=draw), \
                     patch.object(popup_session, "PopupSession", return_value=popup), \
                     patch.object(engine, "_afterfxlib", return_value=("AE", None, "")), \
                     patch.object(engine, "_map", return_value=({}, "")):
                    engine.attach(host)
                self.assertIs(engine.popup, popup)
                self.assertEqual(popup.display_only, use_display)
                self.assertEqual(draw.watch_popup_activity.called, not use_display)

    def test_display_only_never_seeds_translated_metadata_cache(self):
        raw=b'Screen|Reflect'
        session=codec.PopupCodecTests().scan('utf8',raw)
        session.display_only=True
        writes=[]
        p=SimpleNamespace(read=lambda a,n: raw+b'\0',write_exact=lambda a,b:writes.append(b))
        session._open=lambda:(p,{})
        session._valid=lambda r:True
        for _ in range(3):
            session.apply('on',{'Screen':'滤色','Reflect':'反射'})
            self.assertEqual(session.records[0]['raw'],raw)
            session.apply('off',{'Screen':'滤色','Reflect':'反射'})
        self.assertEqual(writes,[])
    def test_display_only_is_applied_not_write_failure(self):
        self.assertEqual(LanguageEngine._critical_popup_reasons({'display-only':92}),{})

if __name__=='__main__':unittest.main(verbosity=2)
