import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import param_draw_hook as ph

class PopupPaintSelectionTests(unittest.TestCase):
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

    def test_2026_keeps_the_cache_fix(self):
        hook,agent=self.setup_hook(2026)
        with mock.patch.dict(sys.modules,{'frida':agent}), \
             mock.patch.object(ph,'popup_draw_profile',wraps=ph.popup_draw_profile) as get:
            self.assertTrue(hook.attach());self.assertEqual(get.call_count,1)
        self.assertEqual(hook.popup_paint_profile['known_version'],'26.5.0.89')

if __name__=='__main__':unittest.main(verbosity=2)
