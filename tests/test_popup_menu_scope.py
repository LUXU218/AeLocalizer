import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine
from runtime.param_draw_hook import JS, observe_js
OBSERVE = observe_js()
APP=QCoreApplication.instance() or QCoreApplication([])
class PopupMenuScopeTests(unittest.TestCase):
    def test_layer_menu_converts_first_label_only_in_verified_scope(self):
        q = QJSEngine()
        start = JS.index('var LAYER_SCOPES =')
        end = JS.index('function setupLayerPopup(')
        result = q.evaluate('''
var WANT=true, LAYER_POPUP={path_arg:2}, MENU_ACTIVE=0, MENU_ERR=0;
var MAP={Screen:'滤色', Add:'相加', '(-':'分隔线'};
function effectScopeKey(path){return path==='effect' ? 'BCC\x1fmode' : '';}
function readPopupPath(path){return path.name;}
function menuWS(label){return label;}
function mkWS(label){return {obj:'converted:'+label};}
function noteEnum(label){} function noteApplied(label){}
function path(name){return {name:name,isNull:function(){return false;}};}
''' + OBSERVE + JS[start:end] + '''
LAYER_RETURN=42;
function convert(thread, caller, text){
    var output={}, options=0, args=[output,text,options];
    var invocation={threadId:thread,returnAddress:{equals:function(ret){return caller===ret;}}};
    layerLabelCall.call(invocation,args);
    var held=!!invocation.layerLabel;
    layerLabelReturned.call(invocation);
    return [args[1],args[0]===output && args[2]===options,held,invocation.layerLabel===null];
}
var scope={threadId:7};
layerPopupEnter.call(scope,[null,null,path('effect')]);
var first=convert(7,42,'Screen'), wrongSite=convert(7,43,'Add'), otherThread=convert(8,42,'Add');
var inner={threadId:7};layerPopupEnter.call(inner,[null,null,path('settings')]);
var other=convert(7,42,'Add'); layerPopupLeave.call(inner);
var separator=convert(7,42,'(-');
WANT=false;var off=convert(7,42,'Screen');layerPopupLeave.call(scope);
JSON.stringify([first,wrongSite,otherThread,other,separator,off,MENU_ACTIVE,LAYER_HITS,MENU_ERR]);
''')
        self.assertEqual(result.toString(),
                         '[["converted:滤色",true,true,true],["Add",true,false,true],'
                         '["Add",true,false,true],["Add",true,false,true],["(-",true,false,true],'
                         '["Screen",true,false,true],0,1,0]')

    def test_popup_notifications_are_deduplicated_and_do_not_write(self):
        q = QJSEngine()
        start = JS.index('var popupActivityVT =')
        end = JS.index('function watchPopupActivity(')
        result = q.evaluate('''
var WANT=true, sent=[], callbacks=[];
function send(message){sent.push(message.popup_streams);}
function setImmediate(callback){callbacks.push(callback);}
function addr(name){return {toString:function(){return name;},isNull:function(){return !name;},
                            equals:function(other){return name===other.toString();}};}
function stream(id, type, parent, definition){return {toString:function(){return id;},
    readPointer:function(){return addr(type);},add:function(off){return {readPointer:function(){
    return addr(off===32?parent:definition);}};}};}
''' + JS[start:end] + '''
popupActivityVT=addr('popup');popupActivityField=112;
var real=stream('0x100','popup','effect','definition');
popupActivityEnter([real]);popupActivityEnter([real]);
popupActivityEnter([stream('0x200','other','effect','definition')]);
popupActivityEnter([stream('0x300','popup','','definition')]);
callbacks.shift()();
WANT=false;popupActivityEnter([stream('0x400','popup','effect','definition')]);
JSON.stringify([sent,callbacks.length]);
''')
        self.assertEqual(result.toString(), '[[["0x100"]],0]')

    def test_classic_selected_value_requires_the_same_effect_popup_and_paint_callsite(self):
        q = QJSEngine()
        start = JS.index('function paintEnter(')
        end = JS.index('// Only local UI_Menu labels')
        path_start = JS.index('function effectPopupPath(')
        path_end = JS.index('function menuContext(')
        result = q.evaluate('''
var WANT = true, POPUP = {paint_sources: {}, text_offset:0, len_offset:16, cap_offset:24, inline_capacity:7};
var POPUP_SOURCES = {}, SOURCE_ACTIVE = 0, PAINT_ACTIVE = 0;
var P_ERR = 0, P_HITS = 0, P_REPLACED = 0, MAP = {Screen:'滤色'};
function menuOwner(widget) { return widget; }
function menuPath(owner) { return [owner, 'BCC', 'mode']; }
function noteSeen(key) {} function noteEnum(key) {} function noteApplied(key) {}
function address(n) { return {id:n,equals:function(other){return n===other.id;}}; }
function ptr(value) { return value; }
var text = 'Screen', length = text.length;
var value = {
    add:function(offset) {
        if (offset===0) return this;
        return {readU64:function(){return {toNumber:function(){return offset===16?length:7;}};},
                writeU64:function(n){length=n;}};
    },
    readUtf16String:function(n){return text.slice(0,n);},
    writeUtf16String:function(s){text=s;},
    equals:function(other){return other===this;}
};
function draw(thread, caller) {
    var frame = {threadId:thread,returnAddress:address(caller)};
    popupEnter.call(frame, [value]);
    var rendered = text;
    textLeave.call(frame);
    return rendered;
}
''' + OBSERVE + JS[start:end] + JS[path_start:path_end] + '''
var source = {threadId:1};
popupSourceEnter.call(source, ['ADBE Effect Parade'], address(17));
var first = draw(1,17), wrongSite = draw(1,18), wrongThread = draw(2,17);
var other = {threadId:1};
popupSourceEnter.call(other, ['settings'], address(17));
var unrelated = draw(1,17);
popupSourceLeave.call(other);
popupSourceLeave.call(source);
var after = draw(1,17);
JSON.stringify([first, wrongSite, wrongThread, unrelated, after, text, SOURCE_ACTIVE, PAINT_ACTIVE, P_ERR]);
''')
        self.assertEqual(result.toString(), '["滤色","Screen","Screen","Screen","Screen","Screen",0,0,0]')

    def test_first_menu_for_verified_effect_can_open_before_popup_discovery(self):
        q = QJSEngine()
        start = JS.index('function effectPopupPath(')
        end = JS.index('function menuBuildEnter(')
        result = q.evaluate('''
var MENU_KEYS = {};
var POPUP = {menu: {descriptor_stride: 32}};
var owner = {};
function menuOwner(widget) { return widget.typed ? owner : null; }
var names = ['scene', 'ADBE Effect Parade', 'BCC', 'mode'];
function menuPath(stream) { return names; }
function pointer(n) {
    return {n:n, compare:function(p){return n-p.n;},
            sub:function(p){return {compare:function(x){return n-p.n-x.n;},toInt32:function(){return n-p.n;}};},
            add:function(i){return pointer(n+i);}};
}
var ptr = pointer;
var menuFns = {items:function(){return {readPointer:function(){return pointer(4096);},
    add:function(){return {readPointer:function(){return pointer(4160);}};}};}};
function menuWS(p) { return p.n === 4096 ? 'Off' : 'On'; }
''' + JS[start:end] + '''
var widget = {typed:true};
var first = menuContext(widget);
MENU_KEYS['BCC\\x1fmode'] = ['Other|Choices'];
var mismatch = menuContext(widget);
names = ['scene', 'settings', 'BCC', 'mode'];
MENU_KEYS = {};
JSON.stringify([first && first.labels, mismatch, menuContext(widget), menuContext({})]);
''')
        self.assertEqual(result.toString(), '[["Off","On"],null,null,null]')

    def engine(self):
        q=QJSEngine()
        start=JS.index('function menuBuildEnter(')
        end=JS.index('function setupPopupMenu(')
        result=q.evaluate('''
var WANT=true,MENU_ACTIVE=0,MENU_SCOPE={},MENU_HITS=0,MENU_ERR=0,MENU_LAST='';
var MAP={Off:'关'}, menuReturn={id:7};
function source(s){return {add:function(off){return off===16?{readU64:function(){return {toNumber:function(){return s.length;}}}}:{readU64:function(){return {toNumber:function(){return 15;}}}};},readUtf8String:function(){return s;}};}
function callsite(id){return {equals:function(other){return id===other.id;}};}
function args(label,id){return ['menu','output',source(label),{toUInt32:function(){return id;}}];}
function menuContext(widget){if(widget==='error')throw Error('bad path');return widget==='known'?{labels:['Off','On']}:null;}
'''+OBSERVE+JS[start:end]+'''
menuUtf8String=function(s){return {obj:'translated:'+s,data:'owned'};};
''')
        self.assertFalse(result.isError(),result.toString());return q
    def test_only_exact_item_in_qualified_builder_changes_label(self):
        q=self.engine();v=q.evaluate('''
var b={threadId:1};menuBuildEnter.call(b,['known']);
var a=args('Off',1), value=a[3],ctx={threadId:1,returnAddress:callsite(7)};
menuAppendEnter.call(ctx,a);
var result=[a[0]==='menu',a[1]==='output',a[2]==='translated:关',a[3]===value,!!ctx.menuLabel,MENU_ACTIVE===1];
menuAppendLeave.call(ctx);menuBuildLeave.call(b);
result.push(ctx.menuLabel===null,MENU_ACTIVE===0,Object.keys(MENU_SCOPE).length===0,MENU_ERR===0);
JSON.stringify(result);
''');self.assertEqual(v.toString(),'[true,true,true,true,true,true,true,true,true,true]')
    def test_scoped_value_isolated_by_effect_and_parameter(self):
        q = self.engine()
        actual = q.evaluate(r'''
MAP['BCC\x1fmode\x1fOff'] = '仅甲';
function translated(scope) {
    var a=args('Off',1), c={threadId:1,returnAddress:callsite(7)};
    MENU_SCOPE['1']=[{labels:['Off'],scope:scope}];
    menuAppendEnter.call(c,a);
    return a[2];
}
JSON.stringify([translated('BCC\x1fmode'),translated('Other\x1fmode'),
                translated('BCC\x1fother'),translated('')]);
''')
        self.assertEqual(actual.toString(), '["translated:仅甲","translated:关","translated:关","translated:关"]')

    def test_other_callsite_wrong_item_and_unqualified_control_are_untouched(self):
        q=self.engine();v=q.evaluate('''
var b={threadId:1};menuBuildEnter.call(b,['known']);
var wrong=args('Off',2),original=wrong[2];menuAppendEnter.call({threadId:1,returnAddress:callsite(7)},wrong);
var other=args('Off',1),original2=other[2];menuAppendEnter.call({threadId:1,returnAddress:callsite(8)},other);
menuBuildLeave.call(b);var unqualified=args('Off',1),original3=unqualified[2];
menuAppendEnter.call({threadId:1,returnAddress:callsite(7)},unqualified);
JSON.stringify([wrong[2]===original,other[2]===original2,unqualified[2]===original3,MENU_HITS===0,MENU_ACTIVE===0]);
''');self.assertEqual(v.toString(),'[true,true,true,true,true]')
    def test_off_and_failed_owner_leave_no_live_scope(self):
        q=self.engine();v=q.evaluate('''
var a={threadId:1};menuBuildEnter.call(a,['unknown']);menuBuildLeave.call(a);
var b={threadId:1};menuBuildEnter.call(b,['error']);menuBuildLeave.call(b);
WANT=false;var c={threadId:1};menuBuildEnter.call(c,['known']);menuBuildLeave.call(c);
JSON.stringify([MENU_ACTIVE===0,Object.keys(MENU_SCOPE).length===0,MENU_ERR===1]);
''');self.assertEqual(v.toString(),'[true,true,true]')
    def test_nested_unrelated_builder_cannot_borrow_outer_parameter(self):
        q=self.engine();v=q.evaluate('''
var outer={threadId:1},inner={threadId:1};
menuBuildEnter.call(outer,['known']);menuBuildEnter.call(inner,['unknown']);
var a=args('Off',1),original=a[2];menuAppendEnter.call({threadId:1,returnAddress:callsite(7)},a);
menuBuildLeave.call(inner);menuBuildLeave.call(outer);
JSON.stringify([a[2]===original,MENU_ACTIVE===0,Object.keys(MENU_SCOPE).length===0]);
''');self.assertEqual(v.toString(),'[true,true,true]')
    def test_os_menu_changes_only_copied_label_not_position_or_submenu(self):
        q=self.engine();v=q.evaluate('''
var osMenuReturn={id:9};MENU_SCOPE['1']=[{labels:['Off'],osCurrent:{scope:''}}];
osMenuOriginal=function(context,arg){return 'Off';};mkWS=function(s){return {obj:'translated:'+s,buf:'owned'};};
var a=['os-menu','original-label','submenu'],call={threadId:1,returnAddress:callsite(9)};
menuOsAppendEnter.call(call,a);
JSON.stringify([a[0]==='os-menu',a[1]==='translated:关',a[2]==='submenu',!!call.menuLabel]);
''');self.assertEqual(v.toString(),'[true,true,true,true]')
    def test_os_label_outside_current_descriptor_array_is_rejected_before_truncation(self):
        q=self.engine();v=q.evaluate('''
function pointer(n){return {n:n,compare:function(p){return n<p.n?-1:n>p.n?1:0;},equals:function(p){return n===p.n;},sub:function(p){return {toInt32:function(){return (n-p.n)|0;}};}};}
var begin=pointer(4096),end=pointer(4096+272),widget={};
var context={osChecked:true,osCurrent:{widget:widget,begin:begin,end:end,labels:['Off']}};
var menuFns={items:function(){return {readPointer:function(){return begin;},add:function(){return {readPointer:function(){return end;}};}};}};
var POPUP={menu:{descriptor_stride:272}};menuWS=function(){return 'Off';};
String(osMenuOriginal(context,pointer(4096+4294967296)));
''');self.assertEqual(v.toString(),'null')
    def test_custom_flyout_keeps_english_value_and_index(self):
        q=self.engine();v=q.evaluate('''
var flyoutCtorReturn={id:11},FLYOUT_SCOPE={'1':[{label:'Off',index:0}]};
var a=['new-item',{toUInt32:function(){return 0;}},source('Off'),source('Off')];
var value=a[3],index=a[1],call={threadId:1,returnAddress:callsite(11)};
menuFlyoutCtorEnter.call(call,a);
JSON.stringify([a[0]==='new-item',a[1]===index,a[2]==='translated:关',a[3]===value,!!call.menuLabel]);
''');self.assertEqual(v.toString(),'[true,true,true,true,true]')
if __name__=='__main__':unittest.main(verbosity=2)
