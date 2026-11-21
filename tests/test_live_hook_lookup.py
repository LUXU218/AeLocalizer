# -*- coding: utf-8 -*-
"""实际执行 core.live_hook.JS_LOOKUP（QJSEngine），不是测 Python 对应函数。

覆盖：
  · 精确整名优先；
  · 重复实例 " 2"/" 3" 剥后缀查基名并拼回；
  · REAL 里真实存在的 "BCC Noise Map 2" 不被误拆；
  · REAL 为空时不做后缀拆分（安全）。
"""
import json
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine

    _app = QCoreApplication.instance() or QCoreApplication([])
    HAS_QML = True
except Exception:  # pragma: no cover
    HAS_QML = False


def _js_raw(mapping, real, expr):
    src = r"""
var MAP = __MAP__;
var REAL = __REAL__;
var REAL_OK = __OK__;
var LOW = {};
var ZH2EN = {};
for (var k in MAP) {
    var t = k.replace(/^\s+/, '').replace(/\s+$/, '');
    if (t && !Object.prototype.hasOwnProperty.call(LOW, t.toLowerCase())) LOW[t.toLowerCase()] = MAP[k];
    if (!Object.prototype.hasOwnProperty.call(LOW, k.toLowerCase())) LOW[k.toLowerCase()] = MAP[k];
    var v = MAP[k];
    if (v && !Object.prototype.hasOwnProperty.call(ZH2EN, v)) ZH2EN[v] = k;
}
__LOOKUP__
__EXPR__
"""
    src = (src.replace("__MAP__", json.dumps(mapping, ensure_ascii=False))
              .replace("__REAL__", json.dumps({x: 1 for x in real}, ensure_ascii=False))
              .replace("__OK__", "true" if real else "false")
              .replace("__LOOKUP__", __import__("core.live_hook", fromlist=["JS_LOOKUP"]).JS_LOOKUP)
              .replace("__EXPR__", expr))
    eng = QJSEngine()
    val = eng.evaluate(src)
    if val.isNull() or val.isUndefined():
        return None
    return val.toString()


def _js(mapping, real, expr):
    return _js_raw(mapping, real, "lookup(%s)" % json.dumps(expr, ensure_ascii=False))


@unittest.skipUnless(HAS_QML, "QtQml/QJSEngine 不可用")
class LiveHookUiBoundaryTest(unittest.TestCase):
    def test_rpc_never_calls_native_ui_or_walks_panes(self):
        """RPC 线程只能排队；原生 SetName/UpdateNow/InvalTopic 只在 UI 钩子。"""
        import pathlib
        p = (pathlib.Path(__file__).resolve().parent.parent
             / "core" / "live_hook.py")
        src = p.read_text(encoding="utf-8")
        a = src.index("setpaused: function")
        b = src.index("ra: function", a)
        rpc = src[a:b]
        for bad in ("fSetName(", "fUpdateNow(", "fInvalTopic("):
            self.assertNotIn(bad, rpc)
        self.assertNotIn("for (var i = 0; i < PANES.length", rpc)
        self.assertIn("PENDING_INVAL", rpc)
        g = src.index("JS_GETNAME_HOOK")
        ge = src.index('"""', src.index('r"""', g) + 4)
        self.assertIn("restorePaneOnUI", src[g:ge])
        self.assertIn("function restorePaneOnUI", src)


def _extract_js_function(src, name):
    start = src.index("function " + name)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unclosed function " + name)


class ReusedPaneTest(unittest.TestCase):
    def test_reused_pane_uses_current_text_not_stale_rec(self):
        """地址复用：旧 PANEMAP.en 绝不能写到当前英文/中文/自定义标题。"""
        import pathlib
        from core.live_hook import JS_GETNAME_HOOK, JS_PANE_GUARD
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "core" / "live_hook.py").read_text(encoding="utf-8")
        restore = _extract_js_function(src, "restorePaneOnUI")
        harness = r"""
var PAUSED = true, INREC = false, PENDING_INVAL = false, RESTORING = false;
var PANES = [], SETNAMED = {}, PANEMAP = {'0x1': {en: 'S_RepairFrames', zh: 'S_修复帧'}};
var lastInval = 0, fSetName = null, fUpdateNow = null, fInvalTopic = null;
function refreshPaneOnUI(obj) { if (fUpdateNow) fUpdateNow(obj); }
var st = { nameCalls: 0, namePatched: 0, nameSample: [], nameMiss: [],
           nameErrors: 0, nameErr: [] };
var current = '', cap = 64, setters = [], phase = 'enter';
// onEnter 时 sret 是调用方分配的残留输出，只有 onLeave 才是原函数构造值。
function readW(p) {
    return { s: (phase === 'enter' ? 'S_修复帧' : current),
             cap: cap, data: { writeUtf16String: function(s) { current = s; } } };
}
function mkWS(s) { return s; }
function isEffectName(s) { return true; }
function isCECTitle(o) { return true; }
function lookup(s) { return null; }
function zh2en(s) {
    return {'S_修复帧': 'S_RepairFrames', 'BCC+辉光': 'BCC+Glow'}[s] || '';
}
var Memory = { writeUtf16String: function (data, s) { current = s; } };
__GUARD__
__FN__
var HANDLER = __HOOK__;
function run(name, capv, pausedEnter, pausedLeave) {
    current = name; cap = capv || 64;
    PAUSED = (pausedEnter === undefined ? true : !!pausedEnter);
    var p = { isNull: function () { return false; },
              toString: function () { return '0x1'; },
              add: function () { return { writeU64: function () {} }; } };
    var ctx = {};
    phase = 'enter';
    HANDLER.onEnter.call(ctx, [p, p]);
    phase = 'leave';
    PAUSED = (pausedLeave === undefined ? true : !!pausedLeave);
    HANDLER.onLeave.call(ctx);
    return current;
}
var results = [
    run('BCC+Glow', 64),
    run('BCC+辉光', 64),
    run('S_修复帧', 64),
    run('My Custom Effect', 64),
    run('BCC+辉光', 64, false, true),   // ON 进入、OFF 离开
    run('BCC+辉光', 64, true, false)    // OFF 进入、ON 离开
];
fSetName = function (obj, s) { setters.push(s); };
PANEMAP['0x1'] = { en: 'OLD ENGLISH', zh: '旧中文' };
var longCase = run('BCC+辉光', 2);
String(results.join('|') + '|' + longCase + '|' + setters.join(',') +
       '|' + JSON.stringify(PANEMAP));
"""
        harness = (harness.replace("__GUARD__", JS_PANE_GUARD)
                   .replace("__FN__", restore)
                   .replace("__HOOK__", JS_GETNAME_HOOK))
        eng = QJSEngine()
        val = eng.evaluate(harness)
        out = val.toString()
        parts = out.split("|")
        self.assertEqual(parts[:6], ["BCC+Glow", "BCC+Glow",
                                     "S_RepairFrames", "My Custom Effect",
                                     "BCC+Glow", "BCC+辉光"])
        self.assertEqual(parts[6], "BCC+辉光")   # fake setter 不更新返回缓存
        # setter 只能是当前中文反查出的 BCC+Glow，不能是 OLD ENGLISH/旧 rec。
        self.assertEqual(parts[7], "BCC+Glow")
        self.assertNotIn("OLD ENGLISH", parts[7])
        self.assertEqual(parts[8], "{}")


class PausedTitleNativeCacheTest(unittest.TestCase):
    def test_paused_restore_always_updates_pane_title_cache(self):
        """OFF 时即使 sret 能原地写英文，也要调 SetName 更新 pane 自身缓存。

        红：旧代码只在英文长于 cap 时调 fSetName，短名只改本次 sret，
        BCC Unsharp Mask（16）> 中文 cap 15 时 drawer 仍读旧中文。
        """
        import pathlib
        from core.live_hook import JS_GETNAME_HOOK, JS_PANE_GUARD
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "core" / "live_hook.py").read_text(encoding="utf-8")
        restore = _extract_js_function(src, "restorePaneOnUI")
        harness = r"""
var PAUSED = true, INREC = false, PENDING_INVAL = false, RESTORING = false;
var PANES = [], SETNAMED = {}, PANEMAP = {'0x1': {en: 'BCC Unsharp Mask', zh: 'BCC 钝化蒙版'}};
var lastInval = 0, fSetName = null, fUpdateNow = null, fInvalTopic = null;
var st = { nameCalls: 0, namePatched: 0, nameSample: [], nameMiss: [],
           nameErrors: 0, nameErr: [], pauseName: [], pauseSetName: 0,
           pauseSetErr: 0 };
var current = '', cap = 64, setters = [], sretWrites = 0;
function readW(p) { return { s: current, cap: cap, data: { writeUtf16String: function(s) { sretWrites++; current = s; } } }; }
function mkWS(s) { return s; }
function isEffectName(s) { return true; }
function isCECTitle(o) { return true; }
function _paneValid(o) { return true; }
function lookup(s) { return null; }
function zh2en(s) { return {'BCC 钝化蒙版': 'BCC Unsharp Mask'}[s] || ''; }
var Memory = { writeUtf16String: function (d, s) { sretWrites++; current = s; } };
__GUARD__
__FN__
var HANDLER = __HOOK__;
fSetName = function (o, s) { setters.push(s); };
function run(name, capv) {
    current = name; cap = capv; PAUSED = true;
    var p = { isNull: function () { return false; },
              toString: function () { return '0x1'; },
              add: function () { return { writeU64: function () {} }; } };
    var c = {};
    HANDLER.onEnter.call(c, [p, p]);
    HANDLER.onLeave.call(c);
    return current;
}
var shortTitle = run('BCC 钝化蒙版', 64);
PANEMAP['0x1'] = { en: 'BCC Unsharp Mask', zh: 'BCC 钝化蒙版' };
var longTitle = run('BCC 钝化蒙版', 2);
var RESULT = String(shortTitle + '|' + longTitle + '|' + setters.join(',') + '|' +
       sretWrites + '|' + st.pauseSetName + '|' + st.pauseName.length + '|' +
       (st.pauseName[0] ? st.pauseName[0].branch : 'NO0') + '|' +
       (st.pauseName[1] ? st.pauseName[1].branch : 'NO1') + '|' +
       JSON.stringify(st.nameErr));
RESULT
"""
        harness = (harness.replace("__GUARD__", JS_PANE_GUARD)
                   .replace("__FN__", restore)
                   .replace("__HOOK__", JS_GETNAME_HOOK))
        eng = QJSEngine()
        val = eng.evaluate(harness)
        parts = val.toString().split("|")
        self.assertEqual(parts[0], "BCC Unsharp Mask")
        # cap 2 时本轮 sret 不改；真实 pane 缓存由 SetName 更新。
        self.assertEqual(parts[1], "BCC 钝化蒙版")
        self.assertEqual(parts[2], "BCC Unsharp Mask,BCC Unsharp Mask")
        self.assertEqual(parts[3], "1")
        self.assertEqual(parts[4], "2")
        self.assertEqual(parts[5], "2")
        self.assertEqual(parts[6], "sret+set")
        self.assertEqual(parts[7], "none+set")

    def test_get_display_name_saves_pre_on_english_for_off_race(self):
        """ON onEnter 改中文后、onLeave 前收到 OFF 时，用保存的原英文兜底。"""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "core" / "live_hook.py").read_text(encoding="utf-8")
        self.assertIn("this.en0 = d.w.s;", src)
        self.assertIn("_en = this.en0;", src)
        self.assertLess(src.index("this.en0 = d.w.s;"),
                        src.index("putDisp(o, d.off, d.w, zh)"))
        self.assertIn("st.dispOff.push", src)
        for field in ("pauseName", "pauseSetName", "dispOff", "edbOff", "edbOffMiss"):
            self.assertIn(field, src)


class PausedDisplayNameHandlerTest(unittest.TestCase):
    @staticmethod
    def _extract_display_handler(src):
        marker = "Interceptor.attach(gp, {"
        start = src.index(marker) + len("Interceptor.attach(gp, ")
        if src[start] != "{":
            raise AssertionError("display hook object not found")
        depth = 0
        for j in range(start, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[start:j + 1]
        raise AssertionError("unclosed display hook object")

    def test_already_cjk_on_enter_then_off_returns_original_english(self):
        """真实 GetDisplayName handler：ON 进入时已是中文，随后 OFF，onLeave
        必须用 onEnter 保存的原英文返回，而不是放弃并留下中文。"""
        from core.live_hook import JS_TEMPLATE
        handler = self._extract_display_handler(JS_TEMPLATE)
        harness = r"""
var PAUSED = false;
var current = 'BCC 钝化蒙版';
var st = { getCalls: 0, getPatched: 0, sample: [],
           dispOff: [], dispPausedNoEn: 0, dispFallbackEn: 0 };
var FLIPPED = {};
function isCJK(s) { return /[\u4e00-\u9fff]/.test(s); }
function zh2en(s) { return s === 'BCC 钝化蒙版' ? 'BCC Unsharp Mask' : ''; }
function isEffectName(s) { return true; }
function recordName(t, k, v) { t[k] = v; }
function lookup(s) { return null; }
function putDisp() { return true; }
function mkWS(s) { return 'WS:' + s; }
function send() {}
function readW(p) { return { s: '', cap: 0, data: 0 }; }
function noteRA() {}
var _lastRA = 0;
var Thread = { backtrace: function () { return []; } };
function dispOf(o) { return { off: 0, w: { s: current } }; }
var HANDLER = __HANDLER__;
var obj = { isNull: function () { return false; },
            toString: function () { return '0x1'; } };
var ctx = { context: { rax: null } };
HANDLER.onEnter.call(ctx, [obj]);
PAUSED = true;
HANDLER.onLeave.call(ctx);
String(ctx.context.rax + '|' + st.dispFallbackEn + '|' +
       st.dispPausedNoEn + '|' + st.dispOff[0].e0);
"""
        from core.live_hook import JS_LOOKUP
        helpers = (_extract_js_function(JS_LOOKUP, "zhMatchesRecorded") + "\n"
                   + _extract_js_function(JS_LOOKUP, "recordedEnglish"))
        harness = helpers + harness.replace("__HANDLER__", handler)
        eng = QJSEngine()
        val = eng.evaluate(harness)
        parts = val.toString().split("|")
        self.assertEqual(parts[0], "WS:BCC Unsharp Mask")
        self.assertEqual(parts[1], "1")
        self.assertEqual(parts[2], "0")
        self.assertEqual(parts[3], "BCC Unsharp Mask")


class RestorePaneBoundaryTest(unittest.TestCase):
    def test_restore_pane_does_not_call_native_or_reenter(self):
        """执行真实 restorePaneOnUI 源码：SetName/UpdateNow 都不能被调用。"""
        import pathlib
        p = (pathlib.Path(__file__).resolve().parent.parent
             / "core" / "live_hook.py")
        src = p.read_text(encoding="utf-8")
        start = src.index("function restorePaneOnUI")
        i = src.index("{", start)
        depth = 0
        end = i
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        fn = src[start:end]
        harness = r"""
var PANEMAP = {'0x1': {en: 'CC English'}};
var INREC = false, RESTORING = false;
var setters = 0, updates = 0;
function _paneValid(o) { return !!o; }
function isCECTitle(o) { return !!o; }
function mkWS(s) { return s; }
var fSetName = function () { setters++; };
var fUpdateNow = function () { updates++; };
__FN__
var obj = { toString: function () { return '0x1'; } };
restorePaneOnUI(obj);
String(setters + ':' + updates + ':' + Object.keys(PANEMAP).length);
""".replace("__FN__", fn)
        eng = QJSEngine()
        val = eng.evaluate(harness)
        self.assertEqual(val.toString(), "0:0:0", val.toString())


class LookupJsTest(unittest.TestCase):
    def test_exact_and_suffix(self):
        m = {"CC Light Rays": "CC 光线"}
        real = {"CC Light Rays", "CC Ball Action"}
        self.assertEqual(_js(m, real, "CC Light Rays"), "CC 光线")
        self.assertEqual(_js(m, real, "CC Light Rays 2"), "CC 光线 2")
        self.assertEqual(_js(m, real, "CC Light Rays 3"), "CC 光线 3")
        self.assertEqual(_js_raw(m, real, "zh2en('CC 光线 2')"),
                         "CC Light Rays 2")
        # 同一对象先基名、后完整 " 3"：账本必须升级，不能降级。
        src = ("var F={}; recordName(F,'a','CC Light Rays');"
               "recordName(F,'a','CC Light Rays 3'); F['a']")
        self.assertEqual(_js_raw(m, real, src), "CC Light Rays 3")
        self.assertEqual(_js_raw(
            m, real,
            "zhMatchesRecorded('CC Light Rays 3','CC 光线 3')"), "true")
        self.assertEqual(_js_raw(m, real, "isEffectName('CC Light Rays 3')"), "true")
        self.assertEqual(_js_raw(m, real, "isEffectName('Grid Spacing')"), "false")
        # 写回失败不得删账；成功后才删。
        self.assertEqual(_js_raw(
            m, real,
            "var F={'a':'CC Light Rays 3'};"
            "finishRestore(F,'a',false); String(F['a'] !== undefined)"),
            "true")
        self.assertEqual(_js_raw(
            m, real,
            "var F={'a':'CC Light Rays 3'};"
            "finishRestore(F,'a',true); String(F['a'] === undefined)"),
            "true")

    def test_rtti_reads_inline_typedescriptor_string(self):
        """真实 rttiTypeOf：TypeDescriptor+0x10 是内联 char[]，不是指针。"""
        from core.live_hook import JS_LOOKUP

        src = r"""
var MAP = {}, LOW = {}, ZH2EN = {}, REAL = {}, REAL_OK = false;
var MEM = {}, STR = {};
MEM[0xFF8] = 0x2000; MEM[0x200C] = 0x3000;
STR[0x103010] = '.?AVCECTitle@@';
MEM[0x10F8] = 0x2100; MEM[0x210C] = 0x3100;
STR[0x103110] = '.?AVCTLGroupEffect@@';
function P(addr) {
    return {
        addr: addr,
        isNull: function () { return this.addr === 0; },
        add: function (n) { return P(this.addr + n); },
        sub: function (n) { return P(this.addr - n); },
        readPointer: function () { return P(MEM[this.addr] || 0); },
        readU32: function () { return MEM[this.addr] || 0; },
        readUtf8String: function () { return STR[this.addr] || ''; },
        toString: function () { return '0x' + this.addr.toString(16); }
    };
}
var Process = { findModuleByAddress: function (p) { return { base: P(0x100000) }; } };
__LOOKUP__
var a = P(0x5000); MEM[0x5000] = 0x1000;
var b = P(0x5008); MEM[0x5008] = 0x1100;
String(rttiTypeOf(a) === '.?AVCECTitle@@'
       && isCECTitle(a) === true
       && rttiTypeOf(b) === '.?AVCTLGroupEffect@@'
       && isCECTitle(b) === false);
"""
        src = src.replace("__LOOKUP__", JS_LOOKUP)
        eng = QJSEngine()
        val = eng.evaluate(src)
        self.assertEqual(val.toString(), "true", val.toString())

    def test_build_js_source_replaces_version_gate_placeholder(self):
        from core.live_hook import build_js_source

        src = build_js_source({"CC Light Rays": "CC 光线"},
                              {"CC Light Rays"}, 0xa90c3f)
        self.assertIn("var UI_TITLE_RET = %d;" % 0xa90c3f, src)
        self.assertNotIn("UI_TITLE_RET_JSON", src)
        self.assertNotIn("GETNAME_HOOK_JS", src)
        self.assertIn("CECTitle", src)
        self.assertNotIn("0x19c52a8", src)   # 不写死 CECTitle vtable
        self.assertNotIn("0x198d640", src)

    def test_real_getname_handler_uses_isnull_not_notEqual(self):
        """直接执行真实 JS_GETNAME_HOOK：假 NativePointer 只有 isNull。"""
        from core.live_hook import JS_GETNAME_HOOK, JS_PANE_GUARD

        src = r"""
var PAUSED = false, INREC = false, RESTORING = false, PENDING_INVAL = false;
var PANES = [], SETNAMED = {}, PANEMAP = {};
var lastInval = 0, fSetName = null, fUpdateNow = null, fInvalTopic = null;
function restorePaneOnUI(obj) { return ""; }
function refreshPaneOnUI(obj) { if (fUpdateNow) fUpdateNow(obj); }
var st = { nameCalls: 0, namePatched: 0, nameSample: [], nameMiss: [],
           nameErrors: 0, nameErr: [] };
function lookup(s) { return null; }
function readW(p) { return { s: 'CC Light Rays', cap: 64, data: null }; }
function mkWS(s) { return null; }
function isEffectName(s) { return true; }
function isCECTitle(obj) { return true; }   // RTTI 由单独构建测试覆盖
var Memory = { writeUtf16String: function () {} };
__GUARD__
var HANDLER = __HOOK__;
var ctx = {};
var okPtr = { isNull: function () { return false; },
              toString: function () { return '0x1'; } };
HANDLER.onEnter.call(ctx, [okPtr, okPtr]);
HANDLER.onLeave.call(ctx);
var ok1 = (st.nameCalls === 1 && st.nameErrors === 0
           && PANES.length === 1 && ctx.valid === true);
var badCtx = {};
var badPtr = { isNull: function () { throw new Error('fake-pointer'); } };
HANDLER.onEnter.call(badCtx, [badPtr, null]);
HANDLER.onLeave.call(badCtx);
var ok2 = (badCtx.valid === false && st.nameErrors === 1
           && st.nameErr.length === 1);
String(ok1 && ok2);
"""
        src = (src.replace("__GUARD__", JS_PANE_GUARD)
                  .replace("__HOOK__", JS_GETNAME_HOOK))
        eng = QJSEngine()
        val = eng.evaluate(src)
        self.assertEqual(val.toString(), "true", val.toString())

    def test_real_numeric_name_not_split(self):
        m = {"BCC Noise Map": "BCC 噪波图"}
        real = {"BCC Noise Map 2", "BCC Noise Map"}
        self.assertIsNone(_js(m, real, "BCC Noise Map 2"))
        # 精确整名在词典里时仍优先
        m2 = {"BCC Noise Map 2": "BCC 噪波图 2"}
        self.assertEqual(_js(m2, real, "BCC Noise Map 2"), "BCC 噪波图 2")

    def test_no_real_set_disables_suffix(self):
        m = {"S_Glow": "S_发光"}
        self.assertIsNone(_js(m, set(), "S_Glow 2"))
        self.assertEqual(_js(m, {"S_Glow"}, "S_Glow 2"), "S_发光 2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
