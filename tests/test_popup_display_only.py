import sys, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_popup_codec as codec
from runtime.managed_guard import LanguageEngine

class PopupDisplayOnlyTests(unittest.TestCase):
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
