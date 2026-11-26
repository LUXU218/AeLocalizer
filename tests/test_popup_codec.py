import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime.popup_session import PopupSession
from runtime.managed_guard import LanguageEngine
from tests.test_popup_session_rows import _P,_flt,_rows

class PopupCodecTests(unittest.TestCase):
    def scan(self,encoding,raw):
        s=PopupSession(1,tempfile.mkdtemp(prefix='aeloc_codec_'),'')
        s._open=lambda:(_P(),_flt());s._close=lambda:None
        profile=SimpleNamespace(version='verified',flt_sha='f',tdb_sha='t',encoding=encoding)
        with mock.patch('core.paramdiscovery.parameter_rows',return_value=(profile,_rows()[:1],set(),False)), \
             mock.patch('core.proc.virtual_query',return_value=SimpleNamespace(Type=0x20000,Protect=4)), \
             mock.patch('core.popup_fields.read_cstring',return_value=raw):
            s.scan()
        return s

    def test_utf8_profile_is_used_for_scan_and_write(self):
        raw=b'Screen|Reflect';s=self.scan('utf8',raw)
        self.assertEqual(s.encoding,'utf8')
        writes=[]
        p=SimpleNamespace(read=lambda addr,size:raw+b'\0',write_exact=lambda addr,b:writes.append(b))
        s._open=lambda:(p,_flt());s._valid=lambda rec:True
        got=s.apply('on',{'Screen':'滤色','Reflect':'反射'})
        self.assertEqual(got['changed'],1)
        self.assertEqual(writes,['滤色|反射'.encode('utf8')+b'\0'])

    def test_utf8_string_does_not_decode_as_gbk_mojibake(self):
        s=self.scan('utf8','任意|蒙版'.encode('utf8'))
        self.assertEqual(s.records[0]['text'],'任意|蒙版')

    def test_utf8_capacity_is_counted_in_its_own_bytes(self):
        raw=b'Off|On';s=self.scan('utf8',raw);writes=[]
        p=SimpleNamespace(read=lambda addr,size:raw+b'\0',write_exact=lambda addr,b:writes.append(b))
        s._open=lambda:(p,_flt());s._valid=lambda rec:True
        got=s.apply('on',{'Off':'关','On':'开'})
        self.assertEqual(writes,[])
        self.assertEqual(got['reasons'], {'capacity-unverified': 1})
        self.assertEqual(LanguageEngine._critical_popup_reasons(got['reasons']), {})

    def test_hot_dictionary_replaces_and_clears_only_our_own_classic_labels(self):
        raw = b'Screen|Reflect'
        session = self.scan('utf8', raw)
        writes = [raw + b'\0']
        process = SimpleNamespace(read=lambda addr, size: writes[-1],
                                  write_exact=lambda addr, payload: writes.append(payload))
        session._open = lambda: (process, _flt())
        session._valid = lambda rec: True

        self.assertEqual(session.apply('on', {'Screen': '滤色'})['changed'], 1)
        self.assertTrue(session.needs_restore)
        self.assertEqual(session.apply('on', {'Screen': '屏幕'})['changed'], 1)
        self.assertEqual(session.apply('on', {})['changed'], 1)
        self.assertFalse(session.needs_restore)
        self.assertEqual(writes, [raw + b'\0', '滤色|Reflect'.encode() + b'\0',
                                  '屏幕|Reflect'.encode() + b'\0', raw + b'\0'])

    def test_failed_write_keeps_restore_obligation_until_original_is_verified(self):
        raw = b'Screen|Reflect'
        session = self.scan('utf8', raw)
        current = raw + b'\0'

        def commit_then_raise(address, payload):
            nonlocal current
            current = payload
            raise RuntimeError("read-back failed")

        process = SimpleNamespace(read=lambda addr, size: current[:size],
                                  write_exact=commit_then_raise)
        session._open = lambda: (process, _flt())
        session._valid = lambda rec: True
        self.assertFalse(session.needs_restore)
        self.assertEqual(session.apply('on', {'Screen': '滤色'})['reasons'], {'write': 1})
        self.assertTrue(session.needs_restore)
        self.assertEqual(session.apply('off', {})['reasons'], {'expected-bytes': 1})
        self.assertTrue(session.needs_restore)
        current = raw + b'\0'
        self.assertEqual(session.apply('off', {})['reasons'], {'off-already': 1})
        self.assertFalse(session.needs_restore)

if __name__=='__main__':unittest.main(verbosity=2)
