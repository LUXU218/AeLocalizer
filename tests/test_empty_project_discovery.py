import sys,unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import paramdiscovery as pd

class EmptyProjectDiscoveryTests(unittest.TestCase):
    def test_complete_empty_project_is_not_an_unsupported_popup_build(self):
        profile=object();cat=mock.Mock();p=SimpleNamespace(pid=123,creation_time=456)
        with mock.patch.object(pd,'Registry',return_value=SimpleNamespace(profile=profile)), \
             mock.patch.object(pd,'profile_with_pool',return_value=profile), \
             mock.patch.object(pd,'ParameterCatalog',return_value=cat), \
             mock.patch.object(pd,'discover_parades',return_value=set()), \
             mock.patch.object(pd,'parade_scan_pending',return_value=False):
            self.assertEqual(pd.parameter_rows(p,None,with_state=True),(profile,[],set(),False))
            self.assertEqual(pd.parameter_rows(p,None),(profile,[],set()))
            cat.rows.assert_not_called()

    def test_same_host_can_discover_effects_after_initial_empty_project(self):
        profile=object();cat=mock.Mock();cat.rows.return_value=[{'address':0x1234}]
        p=SimpleNamespace(pid=123,creation_time=456)
        with mock.patch.object(pd,'Registry',return_value=SimpleNamespace(profile=profile)), \
             mock.patch.object(pd,'profile_with_pool',return_value=profile), \
             mock.patch.object(pd,'ParameterCatalog',return_value=cat), \
             mock.patch.object(pd,'discover_parades',side_effect=[set(),{0x2000}]), \
             mock.patch.object(pd,'parade_scan_pending',return_value=False):
            self.assertEqual(pd.parameter_rows(p,None,with_state=True)[1],[])
            got=pd.parameter_rows(p,None,with_state=True)
            self.assertEqual(got,(profile,[{'address':0x1234}],{0x2000},False))

if __name__=='__main__':unittest.main(verbosity=2)
