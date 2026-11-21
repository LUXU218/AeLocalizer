import sys,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from core import adapter,livenames

class RegistryRootPrecedenceTest(unittest.TestCase):
    def test_live_calibration_does_not_replace_verified_effect_group_type(self):
        stale=replace(adapter.PROFILES['18.2.0.37'],root_vtable=0x978F8,display_cache=0x140)
        correct=replace(stale,root_vtable=0x8F0E0)
        base=0x100000
        getters={base+stale.name_get:bytes.fromhex('488d4110c3'),
                 base+stale.match_get:bytes.fromhex('488d4140c3'),
                 base+stale.category_get:bytes.fromhex('488d4160c3')}
        p=SimpleNamespace(image='AfterFX.exe',modules=lambda:[{'name':'FLT.dll','base':base,'path':'FLT.dll'}],
                          read=lambda a,n:getters[a],u64=lambda a:1234)
        with mock.patch.object(livenames,'file_version',return_value=stale.version), \
             mock.patch.object(livenames,'module_digest',return_value=stale.flt_sha), \
             mock.patch.object(livenames,'_with_derived_root',return_value=correct):
            reg=livenames.Registry(p,profile=stale)
        self.assertEqual(reg.profile.root_vtable,0x8F0E0)
        self.assertEqual(reg.profile.display_cache,0x140)

if __name__=='__main__':unittest.main()
