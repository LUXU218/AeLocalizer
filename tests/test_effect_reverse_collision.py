import sys,unittest,json
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine
from core.live_hook import build_js_source,JS_LOOKUP,JS_TEMPLATE
app=QCoreApplication.instance() or QCoreApplication([])

def display_handler():
    marker='Interceptor.attach(gp, '
    start=JS_TEMPLATE.index(marker)+len(marker);depth=0
    for end in range(start,len(JS_TEMPLATE)):
        depth += (JS_TEMPLATE[end]=='{')-(JS_TEMPLATE[end]=='}')
        if depth==0:return JS_TEMPLATE[start:end+1]

class EffectReverseCollisionTest(unittest.TestCase):
    def test_global_inverse_does_not_guess_one_of_two_originals(self):
        src=build_js_source({'Shine':'发光','Glow':'发光','S_Glow':'S_辉光'},['Shine','Glow','S_Glow'])
        init=src[:src.index('var PANES =')]
        eng=QJSEngine();val=eng.evaluate(init+JS_LOOKUP+"JSON.stringify([zh2en('发光'),zh2en('S_辉光')])")
        self.assertFalse(val.isError(),val.toString());self.assertEqual(json.loads(val.toString()),[None,'S_Glow'])

    def test_display_off_and_on_to_off_use_the_recorded_original(self):
        for paused in (True,False):
            harness=r'''
var MAP={'Shine':'发光','Glow':'发光'},LOW={},ZH2EN={},REAL={'Glow':1,'Shine':1},REAL_OK=true;
__LOOKUP__
var FLIPPED={'0x1':'Glow'},PAUSED=__PAUSED__,current='发光';
var st={getCalls:0,getPatched:0,sample:[],dispOff:[],dispPausedNoEn:0,dispFallbackEn:0};
function isCJK(s){return true} function dispOf(o){return {off:0,w:{s:current}}}
function readW(){return null} function mkWS(s){return 'WS:'+s} function send(){}
var Thread={backtrace:function(){return []}},_lastRA=0;
var obj={isNull:function(){return false},toString:function(){return '0x1'}};
var call={context:{rax:null}},HANDLER=__HANDLER__;
HANDLER.onEnter.call(call,[obj]);PAUSED=true;HANDLER.onLeave.call(call);
String(call.context.rax);
'''.replace('__LOOKUP__',JS_LOOKUP).replace('__HANDLER__',display_handler()).replace('__PAUSED__','true' if paused else 'false')
            eng=QJSEngine();val=eng.evaluate(harness)
            self.assertEqual(val.toString(),'WS:Glow',val.toString())

    def test_project_load_display_reads_do_not_run_stack_diagnostics(self):
        harness=r'''
var PAUSED=false,st={getCalls:0},traceCalls=0,_lastRA=0,RAC={};
var Thread={backtrace:function(){traceCalls++;return []}},Backtracer={ACCURATE:1};
function noteRA(){} function send(){} function isCJK(){return false}
function isEffectName(){return false} function dispOf(){return {w:{s:'Project Item'}}}
var obj={isNull:function(){return false}},handler=__HANDLER__;
for(var i=0;i<64;i++)handler.onEnter.call({context:{}},[obj]);
JSON.stringify([st.getCalls,traceCalls]);
'''.replace('__HANDLER__',display_handler())
        eng=QJSEngine();val=eng.evaluate(harness)
        self.assertFalse(val.isError(),val.toString())
        self.assertEqual(json.loads(val.toString()),[64,0])

if __name__=='__main__':unittest.main()
