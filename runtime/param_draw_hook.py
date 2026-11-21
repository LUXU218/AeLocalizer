# -*- coding: utf-8 -*-
"""Parameter-label draw hook: call-prelude + return-address pair.

We deliberately DO NOT hook the EDB_DrawString export. The effect-name agent
also owns that export in the managed SDK host; separate scripts must not
compete to rewrite the same prologue.

The callsite is NOT hardcoded to one AE build.  ``build_profile`` reads the
installed AfterFXLib.dll from disk (export table + structural code-template
match) and returns the PRE/CALL/RET RVAs, their exact instruction bytes and
the verified 6th-argument stack slot.  The runtime keeps both gates:

  * build evidence: EDB_DrawString's mangled signature hash proves argument 6
    is ``const std::wstring&``; the prelude stores argument 5 at [rsp+0x20]
    so argument 6 is at [rsp+0x28];
  * instruction evidence: PRE/CALL/RET bytes are re-checked at attach time.

Pre-hook (the XMM store immediately before the CALL):
  - Replace only the arg6/wstring stack slot for this invocation.
Post-hook (the native CALL return address):
  - Entering here means the native call has returned; restore the original
    pointer iff the slot still contains our object.

AE2026 labels additionally use the verified dvaui static-text paint sink,
scoped to the source CECStaticTextElement RTTI type. Dropdown selection paints
use their own per-build getter-derived wstring layout. Both change only the
current paint's text and restore it on return; source/paint lifetimes participate
in the stop drain. No parameter values, PF names, or effect-title fields change.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

try:                                    # deployed flat next to guard_uni.py
    from build_profile import (TEXT_LABEL_VERSIONS, BuildMismatch,
                               draw_candidate, text_draw_profile, popup_draw_profile, popup_menu_profile)
except ImportError:                     # imported as runtime.param_draw_hook
    from .build_profile import (TEXT_LABEL_VERSIONS, BuildMismatch,
                                draw_candidate, text_draw_profile, popup_draw_profile, popup_menu_profile)

JS = r"""
var MAP = __MAP__;
var WANT = false, ACTIVE = 0, COUNT = 0, WIRE = 0, MAP_SEQ = 0, MAP_ERR = "";
var CACHE = {}, CACHE_N = 0, PENDING = {};
var PRE_RVA = __PRE_RVA__, CALL_RVA = __CALL_RVA__, RET_RVA = __RET_RVA__;
var PRE_HEX = __PRE_BYTES__, CALL_HEX = __CALL_BYTES__, RET_HEX = __RET_HEX__;
var SLOT = __SLOT__;
var SLOT_OK = 0, SLOT_ERR = 0;
// <<AELOC_OBSERVE_START>>
// Observation for the GUI 补词 page. The managed runtime replaces labels at
// paint time and never renames a parameter, so the old guard's
// all_param_names/renamed_names/rename_miss/enum_missing files had no
// producer. Record what this session actually painted instead: read-only,
// bounded, and already available at the point we were translating.
var SEEN = {}, SEEN_N = 0, SEEN_REV = 0, SEEN_CAP = 8000;
var APPLIED = {}, MISS = {}, ENUM = {}, MISS_N = 0, ENUM_N = 0;
var MISS_CAP = 2000, ENUM_CAP = 5000;
function noteSeen(s) {
    if (!s || s.length > 120 || SEEN_N >= SEEN_CAP) return;
    if (Object.prototype.hasOwnProperty.call(SEEN, s)) return;
    SEEN[s] = 1; SEEN_N++; SEEN_REV++;
}
function noteApplied(s) {
    if (!s) return;
    if (Object.prototype.hasOwnProperty.call(APPLIED, s)) return;
    APPLIED[s] = 1; SEEN_REV++;
}
function noteMiss(s, why) {
    if (!s || MISS_N >= MISS_CAP) return;
    if (Object.prototype.hasOwnProperty.call(MISS, s)) return;
    MISS[s] = String(why || 'unknown'); MISS_N++; SEEN_REV++;
}
function noteEnum(s) {
    if (!s || ENUM_N >= ENUM_CAP) return;
    if (Object.prototype.hasOwnProperty.call(ENUM, s)) return;
    ENUM[s] = 1; ENUM_N++; SEEN_REV++;
}
// <<AELOC_OBSERVE_END>>
var lib = Process.getModuleByName('AfterFXLib.dll');
var PRE_ON = false, POST_ON = false;
// AE 2026+ name labels are dvaui UI_StaticText controls; TEXT carries the
// derived paint sink + std::wstring offset, or null on classic builds.
var TEXT = __TEXT__;
var POPUP = typeof __POPUP__ === 'undefined' ? null : __POPUP__;
var TEXT_SCOPE = {}, SOURCE_ACTIVE = 0, SOURCE_ON = false;
var POPUP_ON = false, P_HITS = 0, P_REPLACED = 0, P_ERR = 0, P_SKIP_CAP = 0, P_EMPTY = 0;
var PAINT_ACTIVE = 0;
var TDRAW_ON = false, T_HITS = 0, T_REPLACED = 0, T_SKIP_CAP = 0;
var T_SKIP_INVALID = 0, T_ERR = 0, T_LAST = "";
var tlib = null;

function hexAt(addr, n) {
    try {
        var b = new Uint8Array(addr.readByteArray(n));
        var s = '';
        for (var i = 0; i < b.length; i++) {
            var h = b[i].toString(16);
            s += (h.length < 2 ? '0' + h : h);
        }
        return s;
    } catch (e) {
        return 'ERR:' + e;
    }
}
function checkOriginal() {
    var pre = hexAt(lib.base.add(PRE_RVA), PRE_HEX.length / 2);
    var call = hexAt(lib.base.add(CALL_RVA), CALL_HEX.length / 2);
    var ret = hexAt(lib.base.add(RET_RVA), RET_HEX.length / 2);
    if (pre !== PRE_HEX) return 'pre:' + pre;
    if (call !== CALL_HEX) return 'call:' + call;
    if (ret !== RET_HEX) return 'ret:' + ret;
    return '';
}
function callIntact() {
    return hexAt(lib.base.add(CALL_RVA), CALL_HEX.length / 2) === CALL_HEX;
}
function mkWS(s) {
    if (CACHE[s]) return CACHE[s];
    var cap = Math.max(s.length, 8);
    // PENDING owns both allocations until the matching return hook.
    var buf = Memory.alloc((cap + 1) * 2);
    var obj = Memory.alloc(32);
    buf.writeUtf16String(s);
    obj.writeByteArray(new Uint8Array(32));
    obj.writePointer(buf);
    obj.add(16).writeU64(s.length);
    obj.add(24).writeU64(cap);
    if (CACHE_N > 512) { CACHE = {}; CACHE_N = 0; }
    var rec = {obj: obj, buf: buf};
    CACHE[s] = rec;
    CACHE_N++;
    return rec;
}
function pkey(tid, rsp) { return String(tid) + ':' + rsp.toString(); }
function release(key) {
    var p = PENDING[key];
    if (!p) return;
    delete PENDING[key];
    if (p.counted && ACTIVE > 0) ACTIVE--;
}
function onPre() {
    if (!WANT) return;
    try {
        var rsp = this.context.rsp;
        var slot = rsp.add(SLOT);
        var orig = slot.readPointer();
        var n = orig.add(16).readU64().toNumber();
        var cap = orig.add(24).readU64().toNumber();
        if (n < 1 || n > 200 || cap < n || cap > 4096) return;
        SLOT_OK++;
        var text = (cap <= 7 ? orig : orig.readPointer()).readUtf16String(n);
        var lead = text.match(/^\s*/)[0];
        var trail = text.match(/\s*$/)[0];
        var stripped = text.trim();
        noteSeen(stripped);
        var tr = MAP[stripped];
        if (!tr || !tr.length) return;
        var key = pkey(this.threadId, rsp);
        if (PENDING[key]) return;          // never overwrite a live invocation
        var keep = mkWS(lead + tr + trail);
        var rec = {obj: keep.obj, buf: keep.buf, orig: orig,
                   slot: slot, thread: this.threadId,
                   rsp: rsp.toString(), text: text, counted: true,
                   committed: false};
        // Publish/account BEFORE the slot write. If writePointer throws (or
        // commits then throws), this record still owns its count; only the
        // matching RETURN releases it. A previous invocation is unaffected.
        PENDING[key] = rec;
        ACTIVE++;
        try {
            slot.writePointer(keep.obj);
            rec.committed = true;
            COUNT++;
            noteApplied(stripped);
        } catch (e) {
            rec.write_error = String(e);
            send({error: 'write:' + String(e)});
            // Keep PENDING/count: the native call may still be using the slot
            // or may have thrown before executing; only RETURN may release.
        }
    } catch (e) {
        SLOT_ERR++;
        send({error: 'pre:' + String(e)});
    }
}
function onPost() {
    try {
        var key = pkey(this.threadId, this.context.rsp);
        var p = PENDING[key];
        if (!p) return;
        try {
            if (this.context.rsp.add(SLOT).readPointer().equals(p.obj)) {
                this.context.rsp.add(SLOT).writePointer(p.orig);
            }
        } catch (e) {
            send({error: 'restore:' + String(e)});
        }
        // Release obj+buf even if another agent changed the slot; the native
        // call has already returned and must not keep this allocation alive.
        release(key);
    } catch (e) {
        send({error: 'post:' + String(e)});
    }
}
function findModule(names) {
    for (var i = 0; i < names.length; i++) {
        try { return Process.getModuleByName(names[i]); } catch (e) {}
    }
    var mods = Process.enumerateModules();
    for (var j = 0; j < mods.length; j++) {
        var b = String(mods[j].name).toLowerCase();
        for (var k = 0; k < names.length; k++) {
            if (b === names[k].toLowerCase()) return mods[j];
        }
    }
    return null;
}
function exportAddr(mod, name) {
    try { return mod.getExportByName(name); } catch (e) {}
    try { return Module.getExportByName(mod.name, name); } catch (e) {}
    return null;
}
function inLabelModule(p, n) {
    return p.compare(lib.base) >= 0 && p.add(n).compare(lib.base.add(lib.size)) <= 0;
}
function sourceClassName(type) {
    // Frida readCString(size) decodes the entire fixed-size buffer, including
    // bytes after NUL. Bound the read, then stop on the ASCII terminator.
    var bytes = new Uint8Array(type.add(16).readByteArray(128));
    var name = '';
    for (var i = 0; i < bytes.length; i++) {
        var b = bytes[i];
        if (b === 0) return name;
        if (b < 32 || b > 126) return '';
        name += String.fromCharCode(b);
    }
    return '';
}
function sourceEnter(args) {
    var tid = this.threadId;
    this.aelocSourcePrevious = TEXT_SCOPE[tid];
    this.aelocSourceCounted = false;
    var allowed = false;
    if (WANT) {
        try {
            var vt = ptr(args[0]).readPointer();
            if (inLabelModule(vt.sub(8), 16)) {
                var col = vt.sub(8).readPointer();
                if (inLabelModule(col, 24) && col.readU32() === 1
                        && col.sub(col.add(20).readU32()).equals(lib.base)) {
                    var type = lib.base.add(col.add(12).readU32());
                    if (inLabelModule(type, 144))
                        allowed = sourceClassName(type) === TEXT.source_class;
                }
            }
        } catch (e) { allowed = false; } // Unknown UI sources are never writable.
    }
    TEXT_SCOPE[tid] = allowed;
    if (allowed) { SOURCE_ACTIVE++; this.aelocSourceCounted = true; }
}
function sourceLeave(retval) {
    if (typeof this.aelocSourcePrevious === 'undefined') delete TEXT_SCOPE[this.threadId];
    else TEXT_SCOPE[this.threadId] = this.aelocSourcePrevious;
    if (this.aelocSourceCounted) SOURCE_ACTIVE--;
}
function paintEnter(args, layout, kind) {
    if (!WANT || !layout) return;
    if (kind === 'text' && TEXT_SCOPE[this.threadId] !== true) return;
    if (this.aeloc) {
        if (kind === 'popup') P_ERR++; else T_ERR++;
        T_LAST = kind + '-reentry'; return;
    }
    try {
        var str = ptr(args[0]).add(layout.text_offset);
        var len = str.add(layout.len_offset).readU64().toNumber();
        var cap = str.add(layout.cap_offset).readU64().toNumber();
        if (kind === 'popup' && len === 0 && cap <= 4096) { P_EMPTY++; return; }
        if (len < 1 || len > 256 || cap < len || cap > 4096) {
            if (kind === 'popup') P_ERR++; else T_SKIP_INVALID++;
            return;
        }
        if (kind === 'popup') P_HITS++; else T_HITS++;
        var data = (cap <= layout.inline_capacity) ? str : str.readPointer();
        var text = data.readUtf16String(len), key = text.trim();
        noteSeen(key);
        if (kind === 'popup') noteEnum(key);
        var tr = MAP[key];
        if (!tr || !tr.length) return;
        var out = text.match(/^\s*/)[0] + tr + text.match(/\s*$/)[0];
        if (out.length > cap) {
            if (kind === 'popup') { P_SKIP_CAP++; noteMiss(key, 'capacity'); }
            else { T_SKIP_CAP++; noteMiss(key, 'capacity'); }
            return;
        }
        this.aeloc = {str: str, data: data, len: len, cap: cap, wlen: out.length,
                      orig: text, layout: layout, kind: kind, headerWritten: false};
        PAINT_ACTIVE++;
        data.writeUtf16String(out);
        str.add(layout.len_offset).writeU64(out.length);
        this.aeloc.headerWritten = true;
        if (kind === 'popup') P_REPLACED++; else T_REPLACED++;
        noteApplied(key);
    } catch (e) {
        if (kind === 'popup') P_ERR++; else T_ERR++;
        T_LAST = String(e);
        // Keep ownership until onLeave, including a partial/throwing write.
        // Drain must never unload a script whose native paint is still active.
    }
}
function textEnter(args) { paintEnter.call(this, args, TEXT, 'text'); }
function popupEnter(args) { paintEnter.call(this, args, POPUP, 'popup'); }
function textLeave(retval) {
    var r = this.aeloc;
    this.aeloc = null;
    if (!r) return;
    try {
        var layout = r.layout;
        var len = r.str.add(layout.len_offset).readU64().toNumber();
        var cap = r.str.add(layout.cap_offset).readU64().toNumber();
        var data = (r.cap <= layout.inline_capacity) ? r.str : r.str.readPointer();
        if (len !== (r.headerWritten ? r.wlen : r.len)
                || cap !== r.cap || !data.equals(r.data)) {
            if (r.kind === 'popup') P_ERR++; else T_ERR++;
            T_LAST = r.kind + '-restore-skip';
            return;
        }
        r.data.writeUtf16String(r.orig);
        r.str.add(layout.len_offset).writeU64(r.len);
    } catch (e) {
        if (r.kind === 'popup') P_ERR++; else T_ERR++;
        T_LAST = 'restore:' + String(e);
    } finally { PAINT_ACTIVE--; }
}


// Only local UI_Menu labels are translated. Widgets and TDB retain originals.
var MENU_KEYS = {}, MENU_SCOPE = {}, MENU_ACTIVE = 0;
var MENU_HITS = 0, MENU_ERR = 0, MENU_ON = false, MENU_LAST = '';
var menuFns = {}, menuReturn = null, osMenuReturn = null;
var FLYOUT_SCOPE={},flyoutItemReturn=null,flyoutCtorReturn=null;
function menuWS(p) {
    var n=p.add(16).readU64().toNumber(), c=p.add(24).readU64().toNumber();
    if(n>4096 || c<n || c>65536)throw Error('menu string layout');
    return (c<=7?p:p.readPointer()).readUtf16String(n);
}
function menuType(obj) {
    var col=obj.readPointer().sub(8).readPointer();
    if(col.readU32()!==1)throw Error('menu RTTI');
    var base=col.sub(col.add(20).readU32()),h=base.add(col.add(16).readU32());
    var count=h.add(8).readU32(),a=base.add(h.add(12).readU32()), offsets={};
    if(count>128)throw Error('menu hierarchy');
    for(var i=0;i<count;i++){
        var b=base.add(a.add(i*4).readU32());
        if(b.add(12).readS32()===-1)
            offsets[base.add(b.readU32()).add(16).readUtf8String()]=b.add(8).readS32();
    }
    return {name:base.add(col.add(12).readU32()).add(16).readUtf8String(),
            complete:obj.sub(col.add(4).readU32()), offsets:offsets};
}
function menuOwner(widget){
    var t=menuType(widget),off=t.offsets['.?AVUI_Node@ui@dvaui@@'];
    if(t.name!=='.?AVUI_Popup@controls@dvaui@@' || typeof off!=='number')return null;
    var node=t.complete.add(off);
    for(var i=0;i<16&&!node.isNull();i++){
        t=menuType(node);
        if(t.name==='.?AVCECPopupV2@internal@ecp@@'){
            off=t.offsets['.?AVCECStreamV2@internal@ecp@@'];
            return typeof off==='number'?t.complete.add(off):null;
        }
        node=menuFns.parent(node);
    }
    return null;
}
function menuPath(owner){
    var out=Memory.alloc(32),made=false,names=[];
    try{
        menuFns.path(owner,out);made=true;
        var n=menuFns.length(out);if(n>64)throw Error('menu path length');
        for(var i=0;i<n;i++)names.push(menuFns.name(menuFns.match(menuFns.component(out,i))).readUtf8String());
        return names;
    }finally{if(made)menuFns.dtor(out);}
}
function menuContext(widget){
    var owner=menuOwner(widget);if(!owner)return null;
    var names=menuPath(owner);if(names.length<2)return null;
    var key=names[names.length-2]+'\x1f'+names[names.length-1];
    var candidates=MENU_KEYS[key];if(!candidates)return null;
    var items=menuFns.items(widget),begin=items.readPointer(),end=items.add(8).readPointer();
    var stride=POPUP.menu.descriptor_stride;
    if(end.compare(begin)<0 || end.sub(begin).compare(ptr(stride*1024))>0)return null;
    var bytes=end.sub(begin).toInt32();
    if(bytes<0 || bytes%stride || bytes/stride>1024)return null;
    var labels=[];for(var i=0;i<bytes/stride;i++)labels.push(menuWS(begin.add(i*stride)));
    var tuple=labels.join('|');
    if(candidates.indexOf(tuple)<0)return null;
    var allowed={};labels.forEach(function(s){allowed[s]=true;});
    return {labels:labels, key:key, widget:widget, begin:begin, end:end};
}
function menuBuildEnter(args){
    MENU_ACTIVE++;
    var key=String(this.threadId),stack=MENU_SCOPE[key]||[];
    stack.push(null);MENU_SCOPE[key]=stack;this.menuScope=key;
    if(!WANT)return;
    try{
        var context=menuContext(args[0]);
        if(context)stack[stack.length-1]=context;
    }catch(e){MENU_ERR++;MENU_LAST=String(e);}
}
function menuBuildLeave(){
    if(typeof this.menuScope==='undefined')return;
    var stack=MENU_SCOPE[this.menuScope];stack.pop();
    if(!stack.length)delete MENU_SCOPE[this.menuScope];
    MENU_ACTIVE--;this.menuScope=undefined;
}
function menuUtf8String(s){
    var data=Memory.allocUtf8String(s),bytes=0;
    while(data.add(bytes).readU8()!==0)bytes++;
    var out=Memory.alloc(32);out.writeByteArray(new Uint8Array(32));
    if(bytes<=15){out.writeByteArray(data.readByteArray(bytes+1));out.add(24).writeU64(15);}
    else{out.writePointer(data);out.add(24).writeU64(bytes);}
    out.add(16).writeU64(bytes);return {obj:out,data:data};
}
function menuAppendEnter(args){
    var stack=MENU_SCOPE[String(this.threadId)];
    if(!stack || !stack.length || !stack[stack.length-1] || !this.returnAddress.equals(menuReturn))return;
    try{
        var obj=args[2],n=obj.add(16).readU64().toNumber(),c=obj.add(24).readU64().toNumber();
        if(n<1 || n>4096 || c<n || c>65536)return;
        var label=(c<=15?obj:obj.readPointer()).readUtf8String(n);
        var context=stack[stack.length-1],id=args[3].toUInt32();
        if(id<1 || id>context.labels.length || context.labels[id-1]!==label)return;
        noteSeen(label.trim());noteEnum(label.trim());
        var translated=MAP[label.trim()];if(!translated || translated===label)return;
        this.menuLabel=menuUtf8String(translated);
        args[2]=this.menuLabel.obj;MENU_HITS++;noteApplied(label.trim());
    }catch(e){MENU_ERR++;MENU_LAST=String(e);}
}
function menuAppendLeave(){this.menuLabel=null;}
function osMenuOriginal(context,arg){
    // DoPopupMenu pre-show callbacks run after entry and may replace items.
    // Requalify at the first actual append, then use the current descriptor
    // pointer (not menu position, which excludes separators) for each label.
    if(!context.osChecked){
        context.osCurrent=menuContext(context.widget);context.osChecked=true;
    }
    var current=context.osCurrent;if(!current)return null;
    var items=menuFns.items(current.widget);
    if(!items.readPointer().equals(current.begin) || !items.add(8).readPointer().equals(current.end))return null;
    if(arg.compare(current.begin)<0 || arg.compare(current.end)>=0)return null;
    var delta=arg.sub(current.begin).toInt32(),stride=POPUP.menu.descriptor_stride;
    if(delta<0 || delta%stride || delta/stride>=current.labels.length)return null;
    var label=menuWS(arg);
    return label===current.labels[delta/stride]?label:null;
}
function menuOsAppendEnter(args){
    var stack=MENU_SCOPE[String(this.threadId)];
    if(!stack || !stack.length || !stack[stack.length-1] || !this.returnAddress.equals(osMenuReturn))return;
    try{
        var label=osMenuOriginal(stack[stack.length-1],args[1]);if(label===null)return;
        noteSeen(label.trim());noteEnum(label.trim());
        var translated=MAP[label.trim()];if(!translated || translated===label)return;
        this.menuLabel=mkWS(translated);args[1]=this.menuLabel.obj;MENU_HITS++;noteApplied(label.trim());
    }catch(e){MENU_ERR++;MENU_LAST=String(e);}
}
function menuFlyoutItemEnter(args){
    MENU_ACTIVE++;
    var key=String(this.threadId),frames=FLYOUT_SCOPE[key]||[];
    frames.push(null);FLYOUT_SCOPE[key]=frames;this.flyoutScope=key;
    var stack=MENU_SCOPE[key],outer=stack&&stack[stack.length-1];
    if(!outer || !this.returnAddress.equals(flyoutItemReturn) || !outer.widget.equals(args[0]))return;
    try{
        var label=osMenuOriginal(outer,args[2]);if(label===null)return;
        var index=args[2].sub(outer.osCurrent.begin).toInt32()/POPUP.menu.descriptor_stride;
        if(args[3].toUInt32()!==index)return;
        frames[frames.length-1]={label:label,index:index};
    }catch(e){MENU_ERR++;MENU_LAST=String(e);}
}
function menuFlyoutItemLeave(){
    var frames=FLYOUT_SCOPE[this.flyoutScope];frames.pop();
    if(!frames.length)delete FLYOUT_SCOPE[this.flyoutScope];
    MENU_ACTIVE--;this.flyoutScope=undefined;
}
function menuFlyoutCtorEnter(args){
    var frames=FLYOUT_SCOPE[String(this.threadId)],frame=frames&&frames[frames.length-1];
    if(!frame || !this.returnAddress.equals(flyoutCtorReturn) || args[1].toUInt32()!==frame.index)return;
    try{
        var obj=args[2],n=obj.add(16).readU64().toNumber(),c=obj.add(24).readU64().toNumber();
        if(n<1 || n>4096 || c<n || c>65536)return;
        var label=(c<=15?obj:obj.readPointer()).readUtf8String(n);
        if(label!==frame.label)return;
        noteSeen(label.trim());noteEnum(label.trim());
        var translated=MAP[label.trim()];if(!translated || translated===label)return;
        this.menuLabel=menuUtf8String(translated);args[2]=this.menuLabel.obj;MENU_HITS++;noteApplied(label.trim());
    }catch(e){MENU_ERR++;MENU_LAST=String(e);}
}
function setupPopupMenu(){
    if(!POPUP || !POPUP.menu)return;
    var p=POPUP.menu,addresses={};
    Object.keys(p.symbols).forEach(function(key){
        var s=p.symbols[key],mod=Process.getModuleByName(s.module==='afterfxlib'?'AfterFXLib.dll':s.module+'.dll');
        addresses[key]=mod.getExportByName(s.name);
    });
    menuFns.parent=new NativeFunction(addresses.parent,'pointer',['pointer']);
    menuFns.items=new NativeFunction(addresses.items,'pointer',['pointer']);
    menuFns.path=new NativeFunction(addresses.path,'pointer',['pointer','pointer']);
    menuFns.dtor=new NativeFunction(addresses.dtor,'void',['pointer']);
    menuFns.length=new NativeFunction(addresses.length,'uint',['pointer']);
    menuFns.component=new NativeFunction(addresses.component,'pointer',['pointer','uint']);
    menuFns.match=new NativeFunction(addresses.match,'pointer',['pointer']);
    menuFns.name=new NativeFunction(addresses.name,'pointer',['pointer']);
    if(hexAt(addresses.builder,p.builder_bytes.length/2)!==p.builder_bytes)throw Error('menu builder changed');
    menuReturn=addresses.builder.add(p.label_return_offset);
    osMenuReturn=addresses.osbuilder.add(p.os_label_return_offset);
    flyoutItemReturn=addresses.custom.add(p.flyout_item_return_offset);
    flyoutCtorReturn=addresses.flyoutitem.add(p.flyout_ctor_return_offset);
    if(hexAt(addresses.osbuilder,p.os_builder_bytes.length/2)!==p.os_builder_bytes)throw Error('OS menu builder changed');
    Interceptor.attach(addresses.builder,{onEnter:menuBuildEnter,onLeave:menuBuildLeave});
    Interceptor.attach(addresses.osbuilder,{onEnter:menuBuildEnter,onLeave:menuBuildLeave});
    Interceptor.attach(addresses.append,{onEnter:menuAppendEnter,onLeave:menuAppendLeave});
    Interceptor.attach(addresses.osappend,{onEnter:menuOsAppendEnter,onLeave:menuAppendLeave});
    Interceptor.attach(addresses.flyoutitem,{onEnter:menuFlyoutItemEnter,onLeave:menuFlyoutItemLeave});
    Interceptor.attach(addresses.flyoutctor,{onEnter:menuFlyoutCtorEnter,onLeave:menuAppendLeave});
    MENU_ON=true;
}

try {
    var bad = checkOriginal();
    if (bad) throw new Error('callsite bytes mismatch ' + bad);
    // Use the ProbeListener overload: attach(target, function). On Frida
    // 16.6.6 the object form {onEnter} still installs a non-NULL on_leave
    // iface, so gum_interceptor traps the caller return address even though
    // we omitted JS onLeave. Both targets are mid-function instructions, so
    // that return trap is invalid and leaks the invocation counter. The
    // function form creates JSProbeListener with on_leave == NULL; our own
    // RET_RVA probe performs restoration/drain.
    Interceptor.attach(lib.base.add(PRE_RVA), onPre);
    PRE_ON = true;
    Interceptor.attach(lib.base.add(RET_RVA), onPost);
    POST_ON = true;
    if (!callIntact()) throw new Error('CALL stolen/relocated by interceptor');
    // AE 2026+ name labels: attach the dvaui static-text paint sink. The
    // export name (not an RVA) is resolved against the loaded module, so the
    // same script works for every build listed in the derived profile.
    if (TEXT) {
        tlib = findModule([TEXT.module || 'dvaui.dll']);
        if (!tlib) throw new Error('dvaui module missing');
        var taddr = exportAddr(tlib, TEXT.draw_export);
        if (!taddr) throw new Error('dvaui draw export missing');
        var source = exportAddr(tlib, TEXT.source_draw_export);
        if (!source) throw new Error('text source wrapper missing');
        if (hexAt(source, TEXT.source_draw_bytes.length / 2) !== TEXT.source_draw_bytes)
            throw new Error('text source wrapper bytes changed');
        Interceptor.attach(source, {onEnter: sourceEnter, onLeave: sourceLeave});
        SOURCE_ON = true;
        Interceptor.attach(taddr, {onEnter: textEnter, onLeave: textLeave});
        TDRAW_ON = true;
    }
    if (POPUP) {
        var plib = findModule([POPUP.module || 'dvaui.dll']);
        if (!plib) throw new Error('popup paint module missing');
        POPUP.draw_exports.forEach(function (name) {
            var addr = exportAddr(plib, name);
            if (!addr) throw new Error('popup paint export missing');
            var expected = (POPUP.draw_bytes || {})[name];
            if (expected && hexAt(addr, expected.length / 2) !== expected)
                throw new Error('popup paint entry bytes changed');
            Interceptor.attach(addr, {onEnter: popupEnter, onLeave: textLeave});
        });
        setupPopupMenu();
        POPUP_ON = true;
    }
} catch (e) {
    try { Interceptor.detachAll(); } catch (x) {}
    PRE_ON = false; POST_ON = false; POPUP_ON = false; TDRAW_ON = false;
    send({error: 'attach:' + String(e)});
}
rpc.exports = {
    ready: function () {
        return !!(PRE_ON && POST_ON) && callIntact()
            && (TEXT ? TDRAW_ON && SOURCE_ON : true) && (POPUP ? POPUP_ON && MENU_ON : true);
    },
    setmap: function (m) {
        try {
            MAP = m || {};
            MAP_SEQ++;
            MAP_ERR = "";
            CACHE = {}; CACHE_N = 0;       // PENDING is intentionally untouched
            // A dictionary hot-swap is exactly when the 补词 list must change:
            // keep the 检查 cache, reset what was translated / still missing.
            APPLIED = {}; MISS = {}; MISS_N = 0; SEEN_REV++;
            return true;
        } catch (e) {
            MAP_ERR = String(e);
            return false;
        }
    },
    setpopupkeys: function (keys) { MENU_KEYS = keys || {}; return true; },
    mode: function (on, wire) {
        if (typeof wire !== 'number' || wire < WIRE) return false;
        WIRE = wire; WANT = !!on; return true;
    },
    stats: function () {
        var keys = Object.keys(MAP || {});
        var sample = {};
        ['Amount', 'Radius', 'Brightness'].forEach(function (k) {
            if (Object.prototype.hasOwnProperty.call(MAP, k)) sample[k] = MAP[k];
        });
        return {on: WANT, wire: WIRE, active: ACTIVE + PAINT_ACTIVE + SOURCE_ACTIVE + MENU_ACTIVE, pending: Object.keys(PENDING).length + PAINT_ACTIVE + SOURCE_ACTIVE + MENU_ACTIVE,
                replaced: COUNT, cache: CACHE_N, map_count: keys.length,
                map_seq: MAP_SEQ, map_err: MAP_ERR, map_sample: sample,
                pre_hooked: PRE_ON, post_hooked: POST_ON,
                call_intact: callIntact(), arg_slot: SLOT,
                slot_ok: SLOT_OK, slot_err: SLOT_ERR,
                text_enabled: TDRAW_ON, text_hits: T_HITS,
                text_replaced: T_REPLACED, text_skip_capacity: T_SKIP_CAP,
                text_skip_invalid: T_SKIP_INVALID, text_err: T_ERR,
                text_last: T_LAST, text_source_scoped: SOURCE_ON, popup_enabled: POPUP_ON,
                popup_hits: P_HITS, popup_replaced: P_REPLACED,
                popup_err: P_ERR, popup_skip_capacity: P_SKIP_CAP, popup_empty: P_EMPTY, popup_display_only: MENU_ON,
                menu_replaced: MENU_HITS, menu_err: MENU_ERR, menu_last: MENU_LAST,
                // Revision only: stats() feeds the 1 Hz status.json, so the
                // observation payload must never ride along (see observe()).
                seen_rev: SEEN_REV};
    },
    // Copy-heavy payloads stay out of stats() so the 1 Hz control loop never
    // suffers. The GUI asks for this only when it is about to write the
    // capture file, and gets a stable revision so it can skip unchanged data.
    observe: function () {
        return {rev: SEEN_REV, seen: SEEN, applied: APPLIED,
                miss: MISS, enum_seen: ENUM};
    },
    drain: function () { return Object.keys(PENDING).length + PAINT_ACTIVE + SOURCE_ACTIVE + MENU_ACTIVE; }
};
"""


def observe_js() -> str:
    """The observation helper block of JS (markers included).

    Test harnesses execute extracted subsets of the production script; they
    must inject this block so the extracted functions keep the same
    dependencies they have in the real runtime.
    """
    start = JS.index("// <<AELOC_OBSERVE_START>>")
    end = JS.index("// <<AELOC_OBSERVE_END>>") + len("// <<AELOC_OBSERVE_END>>")
    # 结尾必须补换行：末行是 // 注释，直接拼下一个函数会被吞掉。
    return JS[start:end] + "\n"


class ParamDrawHook:
    def __init__(self, pid: int, mapping: dict, afterfxlib_path: str,
                 timeout: float = 5.0, *, adopted=None):
        self.pid = int(pid)
        self.mapping = dict(mapping or {})
        self.afterfxlib_path = afterfxlib_path
        self.timeout = timeout
        self.adopted = adopted
        self.session = None
        self.script = None
        self.profile = {}
        self.text_profile = {}
        self.popup_paint_profile = {}
        self._lock = threading.RLock()
        self._gen = 0
        self._wire = 0
        self.last_error = ""
        self.error_count = 0

    def _hash(self):
        h = hashlib.sha256()
        with open(self.afterfxlib_path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    def _ae_year(self) -> int:
        """AE 年份（2026 这种）。用于"同一年的任意构建都自适应"。

        先看安装路径（...\\Adobe After Effects 2026\\...），再看已知版本号
        （26.5.0.89 → 2026）；都拿不到就返回 0 = 按经典路径处理。
        """
        if self.adopted:
            from core.adapter import year_of
            return int(year_of(self.adopted.version) or 0)
        m = re.search(r"After Effects (\d{4})", str(self.afterfxlib_path), re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        known = str((self.profile or {}).get("known_version") or "")
        head = known.split(".")[0]
        if head.isdigit() and int(head) >= 18:
            return 2000 + int(head)      # 26 → 2026、25 → 2025
        return 0

    def attach(self):
        sha = self._hash()
        try:
            if self.adopted:
                if not self.adopted.enabled or sha != self.adopted.parameter_draw_sha:
                    raise BuildMismatch("local AfterFXLib fingerprint changed")
                profile = draw_candidate(self.afterfxlib_path, sha, allow_unknown=True)
                profile["local_version"] = self.adopted.version
                profile["verified_build"] = True
            else:
                profile = draw_candidate(self.afterfxlib_path, sha)
        except BuildMismatch as exc:
            raise RuntimeError("AfterFXLib build not verified: %s" % exc)
        self.profile = profile
        # 2026+ Effect Controls labels are drawn by dvaui UI_StaticText, not
        # by EDB_DrawString.
        #
        # ★★ 2026-11-19 用户指出：**同一年份有多个构建号，不能写死白名单**。
        #   旧逻辑只在 known_version ∈ TEXT_LABEL_VERSIONS 时安装这条额外钩子，
        #   于是 26.6.x 之类的新构建直接没有参数名汉化。
        #   现在改成**按结构自适应**：只要 dvaui.dll 在，就试着解析出
        #   UI_StaticText::Draw 与 GetTextString（导出唯一性 + 序言字节 + 偏移合理
        #   + 已知构建的历史值自检）；结构对不上就安静退回经典路径。
        #   对非 2026 构建这条 sink 本来就不会被调用（实测 AE2025 12 秒 0 次），
        #   多挂一个空钩子无副作用，换来的是"构建号变了也不用改代码"。
        self.text_profile = {}
        dvaui_path = os.path.join(
            os.path.dirname(os.path.abspath(self.afterfxlib_path)),
            "dvaui.dll")
        # 65→2026+ 才有这条文本绘制 sink；**按年份**判断，不认具体构建号
        year = self._ae_year()
        if year >= 2026 and os.path.isfile(dvaui_path):
            try:
                self.text_profile = text_draw_profile(dvaui_path, allow_unknown=True)
            except (BuildMismatch, OSError):
                self.text_profile = {}
        # Only the 2026 cached control needs this paint supplement. Classic
        # hosts retain typed popup definitions and their native rendering path.
        self.popup_paint_profile = {}
        if self.text_profile:
            self.popup_paint_profile = popup_draw_profile(dvaui_path)
            self.popup_paint_profile["menu"] = popup_menu_profile(dvaui_path)
        import frida

        src = (JS.replace("__MAP__", json.dumps(self.mapping, ensure_ascii=False))
               .replace("__POPUP__", json.dumps(self.popup_paint_profile or None, ensure_ascii=False))
               .replace("__TEXT__",
                        json.dumps(self.text_profile or None,
                                   ensure_ascii=False))
               .replace("__PRE_RVA__", hex(profile["pre_rva"]))
               .replace("__CALL_RVA__", hex(profile["call_rva"]))
               .replace("__RET_RVA__", hex(profile["ret_rva"]))
               .replace("__PRE_BYTES__", json.dumps(profile["pre_bytes"]))
               .replace("__CALL_BYTES__", json.dumps(profile["call_bytes"]))
               .replace("__RET_HEX__", json.dumps(profile["ret_bytes"]))
               .replace("__SLOT__", str(int(profile["arg_slot"]))))
        self.session = frida.attach(self.pid)
        self.script = self.session.create_script(src)
        self.script.on("message", self._on_message)
        try:
            self.script.load()
            if not self.script.exports_sync.ready():
                raise RuntimeError("pre/post hooks not ready or CALL changed")
        except Exception:
            try:
                self.script.unload()
            except Exception:
                pass
            try:
                self.session.detach()
            except Exception:
                pass
            self.script = None
            self.session = None
            raise
        return True

    def _on_message(self, message, data):
        if message.get("type") == "error":
            self.last_error = str(message.get("description") or "")[:200]
        else:
            payload = message.get("payload") or {}
            if isinstance(payload, dict) and payload.get("error"):
                self.last_error = str(payload.get("error"))[:200]
                self.error_count += 1
            # Normal script messages must not erase a write/attach error.

    def set_map(self, mapping):
        with self._lock:
            self.mapping = dict(mapping or {})
            if self.script is None:
                return False
            return bool(self.script.exports_sync.setmap(self.mapping))

    def set_popup_records(self, records, encoding):
        if not self.popup_paint_profile:
            return True
        keys = {}
        for rec in records:
            try:
                key = str(rec["effectMatchName"]) + "\x1f" + str(rec["matchName"])
                original = rec["original"].decode(encoding)
                if original not in keys.setdefault(key, []):
                    keys[key].append(original)
            except (KeyError, UnicodeError):
                continue
        with self._lock:
            return bool(self.script and self.script.exports_sync.setpopupkeys(keys))

    def mode(self, on: bool, gen: int | None = None):
        with self._lock:
            if gen is None:
                gen = self._gen + 1
            if int(gen) < self._gen:
                return False
            if int(gen) == self._gen:
                if self.script is None:
                    return False
                return bool(self.script.exports_sync.mode(bool(on), self._wire))
            self._gen = int(gen)
            self._wire += 1
            if self.script is None:
                return False
            return bool(self.script.exports_sync.mode(bool(on), self._wire))

    def stats(self):
        with self._lock:
            if self.script is None:
                return {}
            st = dict(self.script.exports_sync.stats() or {})
            st["profile"] = dict(getattr(self, "profile", None) or {})
            st["text_profile"] = dict(getattr(self, "text_profile", None) or {})
            st["popup_paint_profile"] = dict(getattr(self, "popup_paint_profile", None) or {})
            return st

    def observe(self):
        """Read-only observation of what this session actually painted.

        Returns {"rev", "seen", "applied", "miss", "enum_seen"} or {} when the
        script is gone. The GUI 补词 page needs the list of parameter labels the
        runtime really met; the managed runtime replaces text at paint time and
        never renames a parameter, so no other channel can produce it.
        """
        with self._lock:
            if self.script is None:
                return {}
            try:
                return dict(self.script.exports_sync.observe() or {})
            except Exception as exc:
                self.last_error = "observe:%s" % exc
                return {}

    def build(self):
        """Verified build evidence for diagnostics (read-only)."""
        return dict(getattr(self, "profile", None) or {})

    def drain(self, timeout: float | None = None):
        end = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < end:
            try:
                st = self.stats()
            except Exception as exc:
                self.last_error = "drain-stats:%s" % exc
                return False
            # Missing/empty stats is NOT drained. PENDING is authoritative;
            # also require active==0 so mismatch stays fail-closed.
            if (not isinstance(st, dict) or "pending" not in st
                    or "active" not in st):
                self.last_error = "drain-stats-incomplete"
                return False
            if not st.get("pending") and not st.get("active"):
                return True
            time.sleep(0.02)
        return False

    def abandon(self):
        """Host is confirmed dead: detach without RPC/native calls."""
        try:
            if self.script is not None:
                try:
                    self.script.unload()
                except Exception:
                    pass
        finally:
            if self.session is not None:
                try:
                    self.session.detach()
                except Exception:
                    pass
            self.script = None
            self.session = None

    def close(self, progress=None) -> bool:
        """OFF, wait ACTIVE==0, only then unload. False means kept attached.

        progress(stage, ok) is called BEFORE each blocking RPC so a real STOP
        hang is visible on disk. It must never perform RPC/UI work.
        """
        def note(stage, ok=None):
            if progress is not None:
                try:
                    progress(stage, ok)
                except Exception:
                    pass

        with self._lock:
            if self.script is None:
                return True
            note("disable-rpc:begin")
            try:
                self._wire += 1
                if not self.script.exports_sync.mode(False, self._wire):
                    self.last_error = "close-mode-rejected"
                    note("disable-rpc:false", False)
                    return False
            except Exception as exc:
                self.last_error = "close-mode:%s" % exc
                note("disable-rpc:exception", False)
                return False
            note("disable-rpc:done", True)
            note("draw-drain:begin")
            if not self.drain(2.0):
                if not self.last_error:
                    self.last_error = "close-drain-timeout"
                note("draw-drain:false", False)
                return False
            note("draw-drain:done", True)
            note("script-unload:begin")
            try:
                self.script.unload()
            except Exception as exc:
                self.last_error = "close-unload:%s" % exc
                note("script-unload:exception", False)
                return False
            note("script-unload:done", True)
            if self.session is not None:
                note("session-detach:begin")
                try:
                    self.session.detach()
                except Exception as exc:
                    self.last_error = "close-detach:%s" % exc
                    note("session-detach:exception", False)
                    return False
                note("session-detach:done", True)
            self.script = None
            self.session = None
            return True
