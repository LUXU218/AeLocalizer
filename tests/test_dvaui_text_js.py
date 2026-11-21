# -*- coding: utf-8 -*-
"""Behavioral tests for the dvaui text hook JS (textEnter/textLeave).

The real JS is extracted from runtime/param_draw_hook.py and executed under
node against a fake wstring/heap model (no Frida, no AE).  Covers inline/heap
replacement+restore, capacity/invalid skips, no-map, WANT-off, write faults,
re-entry guard and the restore-time pointer/len/cap recheck.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from runtime import param_draw_hook as pdh

SIM = r"""
var T_HITS=0,T_REPLACED=0,T_SKIP_CAP=0,T_SKIP_INVALID=0,T_ERR=0,T_LAST="";
var WANT=true,TDRAW_ON=true,PAINT_ACTIVE=0,SOURCE_ACTIVE=0;
var TEXT_SCOPE={undefined:true},EXCLUDED={},POPUP=null;
var TEXT=__TEXT__;
var MAP=__MAP__;
__FUNCS__
var MEM = new Map();
function buf(addr, n){
  var b = MEM.get(addr);
  if (!b || b.length < n) { b = new Uint8Array(n); MEM.set(addr, b); }
  return b;
}
function U64(v){ this.v = v; }
U64.prototype.toNumber = function(){ return this.v; };
function Pointer(addr){ this.addr = addr; }
Pointer.prototype.add = function(d){ return new Pointer(this.addr + d); };
Pointer.prototype.equals = function(o){ return !!o && this.addr === o.addr; };
Pointer.prototype.readU64 = function(){
  var b = buf(this.addr, 8), x = 0n;
  for (var i = 7; i >= 0; i--) { x = (x << 8n) | BigInt(b[i] || 0); }
  return new U64(Number(BigInt.asUintN(64, x)));
};
Pointer.prototype.writeU64 = function(v){
  var b = buf(this.addr, 8), x = BigInt(typeof v === "object" ? v.v : v);
  for (var i = 0; i < 8; i++) { b[i] = Number((x >> BigInt(8 * i)) & 255n); }
};
Pointer.prototype.readPointer = function(){ return new Pointer(this.readU64().toNumber()); };
Pointer.prototype.writePointer = function(p){
  this.writeU64(p && p.addr !== undefined ? p.addr : p);
};
Pointer.prototype.readUtf16String = function(len){
  var b = buf(this.addr, (len + 1) * 2), s = "";
  for (var i = 0; i < len; i++) { s += String.fromCharCode(b[2 * i] | (b[2 * i + 1] << 8)); }
  return s;
};
Pointer.prototype.writeUtf16String = function(s){
  var b = buf(this.addr, (s.length + 1) * 2);
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i); b[2 * i] = c & 255; b[2 * i + 1] = (c >> 8) & 255;
  }
  b[2 * s.length] = 0; b[2 * s.length + 1] = 0;
};
function ptr(x){ return x instanceof Pointer ? x : new Pointer(x); }
var PARAMS = 0x100000;
function wstring(strAddr, text, cap, heapPtr){
  var data = strAddr;
  if (heapPtr) { new Pointer(strAddr).writePointer(heapPtr); data = heapPtr; }
  new Pointer(data).writeUtf16String(text);
  new Pointer(strAddr + 0x10).writeU64(text.length);
  new Pointer(strAddr + 0x18).writeU64(cap);
  return {str: strAddr, data: data};
}
var results = {};
function reset(){
  WANT = true; TDRAW_ON = true;
  T_HITS = 0; T_REPLACED = 0; T_SKIP_CAP = 0; T_SKIP_INVALID = 0; T_ERR = 0; T_LAST = "";
  MEM = new Map();
}
function caseRun(name, fn){
  reset();
  var r = results[name] = {ok: false};
  try { fn(r); r.ok = true; } catch (e) { r.thrown = String(e); }
  r.hits = T_HITS; r.replaced = T_REPLACED;
  r.skip_cap = T_SKIP_CAP; r.skip_invalid = T_SKIP_INVALID;
  r.err = T_ERR; r.last = T_LAST;
}
function u64at(addr){ return new Pointer(addr).readU64().toNumber(); }
caseRun("inline_replace_restore", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  r.after_enter = new Pointer(w.data).readUtf16String(1);
  r.len_after_enter = u64at(w.str + 0x10);
  textLeave.call(ctx, 0);
  r.after_leave = new Pointer(w.data).readUtf16String(3);
  r.len_after_leave = u64at(w.str + 0x10);
});
caseRun("heap_replace_restore", function(r){
  var w = wstring(PARAMS + 0x100, "Amount", 10, 0x300000), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  r.after_enter = new Pointer(w.data).readUtf16String(2);
  r.len_after_enter = u64at(w.str + 0x10);
  textLeave.call(ctx, 0);
  r.after_leave = new Pointer(w.data).readUtf16String(6);
  r.len_after_leave = u64at(w.str + 0x10);
});
caseRun("capacity_skip", function(r){
  var w = wstring(PARAMS + 0x100, "Bar", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  r.after_enter = new Pointer(w.data).readUtf16String(3);
  r.len_after_enter = u64at(w.str + 0x10);
  textLeave.call(ctx, 0);
});
caseRun("invalid_len", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0);
  new Pointer(w.str + 0x10).writeU64(0);
  textEnter.call({}, [PARAMS]);
});
caseRun("cap_lt_len", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0);
  new Pointer(w.str + 0x18).writeU64(2);
  textEnter.call({}, [PARAMS]);
});
caseRun("no_map", function(r){
  var w = wstring(PARAMS + 0x100, "Unmapped", 10, 0x600000), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  r.unchanged = new Pointer(w.data).readUtf16String(8);
  textLeave.call(ctx, 0);
  r.after_leave = new Pointer(w.data).readUtf16String(8);
});
caseRun("want_off", function(r){
  WANT = false;
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  textLeave.call(ctx, 0);
  r.unchanged = new Pointer(w.data).readUtf16String(3);
});
caseRun("write_throw_restore", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  var orig = Pointer.prototype.writeUtf16String, n = 0;
  Pointer.prototype.writeUtf16String = function(s){
    n++;
    if (n === 1) { throw new Error("simulated write fault"); }
    return orig.call(this, s);
  };
  try { textEnter.call(ctx, [PARAMS]); } finally {
    Pointer.prototype.writeUtf16String = orig;
  }
  r.after_enter = new Pointer(w.data).readUtf16String(3);
  textLeave.call(ctx, 0);
  r.after_leave = new Pointer(w.data).readUtf16String(3);
  r.len_after_leave = u64at(w.str + 0x10);
});
caseRun("restore_after_success_write_fault", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  var orig = Pointer.prototype.writeUtf16String, n = 0;
  Pointer.prototype.writeUtf16String = function(s){
    n++;
    if (n === 1) { throw new Error("simulated restore fault"); }
    return orig.call(this, s);
  };
  try { textLeave.call(ctx, 0); } finally {
    Pointer.prototype.writeUtf16String = orig;
  }
  r.err_after_leave = T_ERR;
  r.last_after_leave = T_LAST;
});
caseRun("reentry_guard", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  textEnter.call(ctx, [PARAMS]);
  r.replaced_after_second = T_REPLACED;
  r.err_after_second = T_ERR;
  r.last_after_second = T_LAST;
  textLeave.call(ctx, 0);
  r.after_leave = new Pointer(w.data).readUtf16String(3);
  r.len_after_leave = u64at(w.str + 0x10);
});
caseRun("restore_skip_on_len_change", function(r){
  var w = wstring(PARAMS + 0x100, "Foo", 3, 0), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  new Pointer(w.str + 0x10).writeU64(2);
  var orig = Pointer.prototype.writeUtf16String, n = 0;
  Pointer.prototype.writeUtf16String = function(s){ n++; return orig.call(this, s); };
  try { textLeave.call(ctx, 0); } finally {
    Pointer.prototype.writeUtf16String = orig;
  }
  r.restore_writes = n;
  r.err_after_leave = T_ERR;
  r.last_after_leave = T_LAST;
});
caseRun("restore_skip_on_ptr_change", function(r){
  var w = wstring(PARAMS + 0x100, "Amount", 10, 0x300000), ctx = {};
  textEnter.call(ctx, [PARAMS]);
  new Pointer(w.str).writePointer(0x400000);
  var orig = Pointer.prototype.writeUtf16String, n = 0;
  Pointer.prototype.writeUtf16String = function(s){ n++; return orig.call(this, s); };
  try { textLeave.call(ctx, 0); } finally {
    Pointer.prototype.writeUtf16String = orig;
  }
  r.restore_writes = n;
  r.err_after_leave = T_ERR;
  r.last_after_leave = T_LAST;
});
console.log(JSON.stringify(results));
"""


def _extract(js, name):
    start = js.index("function " + name)
    i = js.index("{", start)
    depth = 0
    for j in range(i, len(js)):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                return js[start:j + 1]
    raise ValueError("unbalanced " + name)


class DvauiTextJsTest(unittest.TestCase):
    def _run(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        text = {"text_offset": 0x100, "len_offset": 0x10, "cap_offset": 0x18,
                "inline_capacity": 7, "module": "dvaui.dll"}
        # paintEnter 现在还会把见到的标签记进补词采集（noteSeen/noteEnum/
        # noteMiss/noteApplied）。这些函数在生产脚本里是全局的，抽取子集运行
        # 时必须一起带上，否则 ReferenceError。
        funcs = "\n".join([pdh.observe_js(),
                           _extract(pdh.JS, "paintEnter"),
                           _extract(pdh.JS, "textEnter"),
                           _extract(pdh.JS, "textLeave")])
        body = (SIM.replace("__FUNCS__", funcs)
                   .replace("__TEXT__", json.dumps(text))
                   .replace("__MAP__", json.dumps(
                       {"Foo": "中", "Bar": "很长的中文", "Amount": "强度"})))
        with tempfile.TemporaryDirectory(prefix="aeloc_textjs_") as td:
            path = os.path.join(td, "sim.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            proc = subprocess.run([node, path], capture_output=True,
                                  text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0,
                         (proc.stderr or "")[-2000:] + (proc.stdout or "")[-500:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_inline_and_heap_replace_restore(self):
        out = self._run()
        a = out["inline_replace_restore"]
        self.assertTrue(a["ok"], a)
        self.assertEqual(a["after_enter"], "中")
        self.assertEqual(a["len_after_enter"], 1)
        self.assertEqual(a["after_leave"], "Foo")
        self.assertEqual(a["len_after_leave"], 3)
        b = out["heap_replace_restore"]
        self.assertTrue(b["ok"], b)
        self.assertEqual(b["after_enter"], "强度")
        self.assertEqual(b["len_after_enter"], 2)
        self.assertEqual(b["after_leave"], "Amount")
        self.assertEqual(b["len_after_leave"], 6)

    def test_skips(self):
        out = self._run()
        c = out["capacity_skip"]
        self.assertTrue(c["ok"], c)
        self.assertEqual(c["after_enter"], "Bar")
        self.assertEqual(c["replaced"], 0)
        self.assertGreaterEqual(c["skip_cap"], 1)
        self.assertGreaterEqual(out["invalid_len"]["skip_invalid"], 1)
        self.assertGreaterEqual(out["cap_lt_len"]["skip_invalid"], 1)
        n = out["no_map"]
        self.assertTrue(n["ok"], n)
        self.assertEqual(n["unchanged"], "Unmapped")
        self.assertEqual(n["replaced"], 0)
        w = out["want_off"]
        self.assertEqual(w["unchanged"], "Foo")
        self.assertEqual(w["hits"], 0)

    def test_write_fault_recovers(self):
        out = self._run()
        w = out["write_throw_restore"]
        self.assertTrue(w["ok"], w)
        self.assertEqual(w["after_enter"], "Foo")
        self.assertGreaterEqual(w["err"], 1)
        self.assertEqual(w["after_leave"], "Foo")
        self.assertEqual(w["len_after_leave"], 3)

    def test_reentry_guard(self):
        out = self._run()
        r = out["reentry_guard"]
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["replaced_after_second"], 1)
        self.assertGreaterEqual(r["err_after_second"], 1)
        self.assertEqual(r["last_after_second"], "text-reentry")
        self.assertEqual(r["after_leave"], "Foo")

    def test_restore_skipped_when_header_changed(self):
        out = self._run()
        a = out["restore_skip_on_len_change"]
        self.assertTrue(a["ok"], a)
        self.assertEqual(a["restore_writes"], 0)
        self.assertGreaterEqual(a["err_after_leave"], 1)
        self.assertEqual(a["last_after_leave"], "text-restore-skip")
        b = out["restore_skip_on_ptr_change"]
        self.assertTrue(b["ok"], b)
        self.assertEqual(b["restore_writes"], 0)
        self.assertGreaterEqual(b["err_after_leave"], 1)
        self.assertEqual(b["last_after_leave"], "text-restore-skip")

    def test_restore_after_success_write_fault(self):
        out = self._run()
        r = out["restore_after_success_write_fault"]
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["err_after_leave"], 1)
        self.assertTrue(str(r["last_after_leave"]).startswith("restore:"),
                        r["last_after_leave"])


if __name__ == "__main__":
    unittest.main()
