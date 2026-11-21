import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine
from runtime.param_draw_hook import JS, observe_js
OBSERVE = observe_js()
APP=QCoreApplication.instance() or QCoreApplication([])
class PopupMenuScopeTests(unittest.TestCase):
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
var osMenuReturn={id:9};MENU_SCOPE['1']=[{labels:['Off']}];
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
