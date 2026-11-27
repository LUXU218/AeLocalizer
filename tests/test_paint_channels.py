import json, subprocess, sys, unittest, tempfile, shutil
from unittest import mock
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime import build_profile as bp, param_draw_hook as ph
from tests.ae_module_fixtures import is_ae_26_3_0_87

class PaintTests(unittest.TestCase):
    def test_popup_layouts_come_from_six_verified_getters(self):
        for year,offset in [(2021,0x158),(2022,0x1a0),(2023,0x1c0),
                            (2024,0x1c0),(2025,0x178),(2026,0x180)]:
            p=Path(r'C:\Program Files\Adobe')/f'Adobe After Effects {year}'/'Support Files/dvaui.dll'
            local = year == 2026 and is_ae_26_3_0_87(p)
            if local:
                with self.assertRaises(bp.BuildMismatch):
                    bp.popup_draw_profile(p)
            profile = bp.popup_draw_profile(p, allow_unknown=local)
            self.assertEqual(profile['verified_build'], not local)
            self.assertEqual(profile['text_offset'],offset)
            self.assertEqual(len(profile['draw_exports']),2)
        with mock.patch.dict(bp.VERIFIED_DVAUI,{},clear=True):
            with self.assertRaises(bp.BuildMismatch):bp.popup_draw_profile(p)

    def test_real_js_paint_restoration_channels_and_drain(self):
        src=ph.JS
        layout={'text_offset':0x100,'len_offset':16,'cap_offset':24,
                'inline_capacity':7,'draw_export':'static','source_draw_export':'source',
                'source_draw_bytes':'dd','source_class':'.?AVCECStaticTextElement@internal@ecp@@'}
        popup=dict(layout,text_offset=0x180,draw_exports=['popup','popupbutton'])
        values={'__MAP__':{'Amount':'数量','Screen':'滤色','S_Glow':'S_辉光','Threshold':'阈值'},
                '__TEXT__':layout,'__POPUP__':popup,
                '__TEXT_EXCLUDED__':['S_Glow','Threshold'], '__PRE_RVA__':16,
                '__CALL_RVA__':32,'__RET_RVA__':48,'__PRE_BYTES__':'aa',
                '__CALL_BYTES__':'bb','__RET_HEX__':'cc','__SLOT__':40}
        for k,v in values.items():src=src.replace(k,json.dumps(v,ensure_ascii=False))
        node=shutil.which('node')
        if not node:self.skipTest('node unavailable')
        harness=Path(__file__).resolve().parent/'js'/'paint_channels.mjs'
        with tempfile.TemporaryDirectory(prefix='aeloc_paint_js_') as td:
            candidate=Path(td)/'candidate.js'
            candidate.write_text(src,encoding='utf-8')
            p=subprocess.run([node,str(harness),str(candidate)],
                             capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(p.returncode,0,p.stderr+p.stdout)

if __name__=='__main__':
    from unittest import mock
    unittest.main(verbosity=2)
