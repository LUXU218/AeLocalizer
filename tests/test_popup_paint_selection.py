import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import param_draw_hook as ph
from runtime import build_profile as bp

class PopupPaintSelectionTests(unittest.TestCase):
    def test_six_hosts_share_automatic_popup_activity_and_menu_label_discovery(self):
        root = Path(r'C:\Program Files\Adobe')
        for year in range(2021, 2027):
            with self.subTest(year=year):
                folder = root / ('Adobe After Effects ' + str(year)) / 'Support Files'
                activity = bp.popup_activity_profile(folder / 'TDB.dll')
                self.assertEqual(len(activity['getters']), 2)
                profile = bp.popup_label_profile(folder / 'AfterFXLib.dll')
                self.assertIn(profile['path_arg'], (1, 2))
                self.assertTrue(profile['call_bytes'].startswith('ff15'))
                self.assertEqual(profile['ret_rva'] - profile['call_rva'], 6)

    def setup_hook(self,year):
        script=mock.Mock();script.exports_sync.ready.return_value=True
        session=mock.Mock();session.create_script.return_value=script
        agent=SimpleNamespace(attach=mock.Mock(return_value=session))
        lib=Path(r'C:\Program Files\Adobe')/('Adobe After Effects '+str(year))/'Support Files/AfterFXLib.dll'
        return ph.ParamDrawHook(123,{'Amount':'数量'},str(lib)),agent

    def test_classic_host_uses_existing_popup_path(self):
        hook,agent=self.setup_hook(2022)
        with mock.patch.dict(sys.modules,{'frida':agent}), \
             mock.patch.object(ph,'popup_draw_profile',side_effect=AssertionError('extra popup hook on classic host')):
            self.assertTrue(hook.attach())
        self.assertEqual(hook.popup_paint_profile,{})

    def test_verified_2025_can_translate_during_first_draw_and_menu_open(self):
        hook, agent = self.setup_hook(2025)
        with mock.patch.dict(sys.modules, {'frida': agent}):
            self.assertTrue(hook.attach())
        profile = hook.popup_paint_profile
        self.assertTrue(profile['supplemental'])
        self.assertEqual(profile['menu']['descriptor_stride'], 0x98)
        self.assertEqual(len(profile['paint_sources']), 2)
        self.assertNotIn('builder', profile['menu']['symbols'])

    def test_supported_menu_shapes_do_not_require_a_version_hash(self):
        root = Path(r'C:\Program Files\Adobe')
        with mock.patch.dict(bp.VERIFIED_DVAUI, {}, clear=True):
            for year, stride in ((2025, 0x98), (2026, 0x110)):
                with self.subTest(year=year):
                    dll = root / ('Adobe After Effects ' + str(year)) / 'Support Files/dvaui.dll'
                    self.assertEqual(bp.popup_menu_profile(dll)['descriptor_stride'], stride)

    def test_2026_keeps_the_cache_fix(self):
        hook,agent=self.setup_hook(2026)
        with mock.patch.dict(sys.modules,{'frida':agent}), \
             mock.patch.object(ph,'popup_draw_profile',wraps=ph.popup_draw_profile) as get:
            self.assertTrue(hook.attach());self.assertEqual(get.call_count,1)
        self.assertEqual(hook.popup_paint_profile['known_version'],'26.5.0.89')

    def test_unverified_menu_does_not_block_parameter_hook(self):
        hook, agent = self.setup_hook(2026)
        with mock.patch.dict(sys.modules, {'frida': agent}), \
                mock.patch.object(ph, 'popup_menu_profile',
                                  side_effect=ph.BuildMismatch('popup menu evidence differs')):
            self.assertTrue(hook.attach())
        self.assertTrue(hook.text_profile)
        self.assertEqual(hook.popup_paint_profile, {})
        self.assertEqual(hook.popup_profile_error, 'popup menu evidence differs')

if __name__=='__main__':unittest.main(verbosity=2)
