# -*- coding: utf-8 -*-
"""frida 常驻挂钩：把 AE 设置效果显示名的英文换成就地中文。

原理（2026-11-07 真机验证）：
- 「效果控件」面板标题读的是效果实例的显示名缓存（`TDB_StreamBase` 的
  `+display_off`，AE2025 = +0xF0），不是 FLT_FCSpec 注册表。
- AE 在构造/读取效果实例时调用导出的
  `?SetDisplayName@TDB_StreamBase@@QEAAXAEBV?$basic_string@_W...` 把名字写进缓存。
- ★ 2026-11-07 晚修正：**不能改它的入参**。改参数会让 AE 把中文写进会被序列化
  的实例名（保存工程后被持久化，重进工程仍中文）。改成 onEnter 只记录、onLeave
  再就地把 +0xF0 显示名缓存改成中文 —— 实例名字段保持英文，工程不再被污染。
- 兜底：FLT.dll 里各 traits 的 `FillInDisplayName` 派生实现会绕过 SetDisplayName
  直接填缓存；这些函数 onLeave 后再检查一遍 +0xF0，仍是英文就就地改成中文。
- 只碰实例显示名，不碰注册表 `+0x10`，所以顶部「效果」菜单保持英文。
- 定位用导出符号名，2021–2026 六个版本的 TDB.dll 都有这个导出。

这一层是可选增强：frida 不可用/attach 失败时，调用方应回退到轮询写内存。
"""
import json
import threading

SYM_SET_W = ("?SetDisplayName@TDB_StreamBase@@QEAAXAEBV?$basic_string@_WU?$char_traits@_W@std@@"
             "U?$STLAllocator@_W@allocator@dvacore@@@std@@@Z")

#: ★ 2026-11-07 晚：读取端。面板每次刷新都会调用它取显示名；
#: 返回 const wstring&（无隐藏返回指针），this = args[0]。
#: 在这里把 +display_off 就地改成中文 → 已有实例无需任何内存扫描、
#: 也不需要重建面板，下一次绘制就是中文。
SYM_GET_W = ("?GetDisplayName@TDB_StreamBase@@QEBAAEBV?$basic_string@_WU?$char_traits@_W@std@@"
             "U?$STLAllocator@_W@allocator@dvacore@@@std@@XZ")

#: 真实 NativePointer 安全判空；测试直接执行这段（handler 也用它）。
JS_PANE_GUARD = r"""
function _paneValid(p) {
    return !!p && !p.isNull();
}
"""


#: JS 查词（单独成串，测试可用 QJSEngine 直接执行；依赖 MAP/LOW/REAL/REAL_OK）
JS_LOOKUP = r"""
function _trimName(s) {
    return String(s == null ? '' : s).replace(/^\s+/, '').replace(/\s+$/, '');
}
function _lookupBase(base) {
    if (!base) return null;
    if (Object.prototype.hasOwnProperty.call(MAP, base)) return MAP[base];
    var b = _trimName(base);
    if (b && Object.prototype.hasOwnProperty.call(MAP, b)) return MAP[b];
    if (b && Object.prototype.hasOwnProperty.call(LOW, b.toLowerCase())) return LOW[b.toLowerCase()];
    return null;
}
function lookup(s) {
    // 1) 精确整名优先（含真实带数字的效果名，如 BCC Noise Map 2）
    if (s == null) return null;
    s = String(s);
    if (!s) return null;
    var t = _trimName(s);
    if (Object.prototype.hasOwnProperty.call(MAP, s)) return MAP[s];
    if (t && Object.prototype.hasOwnProperty.call(MAP, t)) return MAP[t];
    if (t && Object.prototype.hasOwnProperty.call(LOW, t.toLowerCase()))
        return LOW[t.toLowerCase()];
    // 2) 真实存在、但词典没收录的整名：绝不拆它的数字后缀
    if (!REAL_OK) return null;
    if (Object.prototype.hasOwnProperty.call(REAL, s)) return null;
    if (t && Object.prototype.hasOwnProperty.call(REAL, t)) return null;
    // 3) 重复实例 " 2"/" 3"：剥空格+数字，基名必须命中词典，再拼回后缀
    var m = /^(.*\S)\s+(\d{1,4})$/.exec(t);
    if (!m) return null;
    var base = m[1];
    var suffix = ' ' + m[2];
    var zh = _lookupBase(base);
    return zh ? (zh + suffix) : null;
}
function zh2en(s) {
    // 中文 -> 英文原名；重复实例的 " 2"/" 3" 后缀同样要能还原。
    if (s == null) return null;
    s = String(s);
    if (!s) return null;
    if (Object.prototype.hasOwnProperty.call(ZH2EN, s)) return ZH2EN[s];
    var t = _trimName(s);
    if (t && Object.prototype.hasOwnProperty.call(ZH2EN, t)) return ZH2EN[t];
    var m = /^(.*\S)\s+(\d{1,4})$/.exec(t);
    if (!m) return null;
    var base = m[1];
    var suffix = ' ' + m[2];
    if (Object.prototype.hasOwnProperty.call(ZH2EN, base))
        return ZH2EN[base] + suffix;
    return null;
}
function recordName(flipped, key, name) {
    // 同一对象先出现基名、后出现带 " 3" 的最终名时，后者覆盖前者；
    // 否则 revert 会拿最早基名写回，丢了重复实例序号。
    if (!key || !name) return false;
    name = String(name);
    var old = flipped[key];
    if (!old) { flipped[key] = name; return true; }
    if (old === name) return false;
    var m = /^(.*\S)\s+(\d{1,4})$/.exec(name);
    if (m) {
        var oldBase = old.replace(/\s+\d{1,4}$/, '');
        if (m[1] === oldBase) { flipped[key] = name; return true; }
    }
    // 旧记录比新名更具体（已有后缀，新的是基名）→ 不降级。
    return false;
}
function zhMatchesRecorded(recorded, currentZh) {
    return !!(recorded && lookup(recorded) === currentZh);
}
function recordedEnglish(flipped, key, currentZh) {
    var en = flipped[key];
    return zhMatchesRecorded(en, currentZh) ? en : null;
}
function finishRestore(flipped, key, ok) {
    // 只有真正写回英文成功才删账；容量不足/对象暂不可写时保留可重试。
    if (ok) { delete flipped[key]; return true; }
    return false;
}
function isEffectName(s) {
    // 只认 AE 注册表真实存在的效果显示名（含重复实例 " 2"/" 3"）；
    // 参数名/分类名即使误入词典也不在 REAL 里，绝不参与 pane 标题改写。
    if (s == null) return false;
    var t = _trimName(s);
    if (!t) return false;
    if (Object.prototype.hasOwnProperty.call(REAL, t)) return true;
    var m = /^(.*\S)\s+(\d{1,4})$/.exec(t);
    if (m && Object.prototype.hasOwnProperty.call(REAL, m[1])) return true;
    return false;
}
var _VT_TYPE = {};
function rttiTypeOf(obj) {
    // 动态 MSVC RTTI：vtable[-8]→COL，COL+0xC→TypeDescriptor RVA，
    // TypeDescriptor+0x10→ASCII 类名。按 vtable 缓存，绝不写死 vtable RVA。
    try {
        var vt = obj.readPointer();
        var key = vt.toString();
        if (Object.prototype.hasOwnProperty.call(_VT_TYPE, key)) return _VT_TYPE[key];
        var mod = Process.findModuleByAddress(vt);
        var col = vt.sub(8).readPointer();
        if (!mod || col.isNull()) { _VT_TYPE[key] = ''; return ''; }
        var td = mod.base.add(col.add(0xC).readU32());
        // TypeDescriptor+0x10 是内联 char[]（不是 char*），直接读字符串。
        var name = td.add(0x10).readUtf8String() || '';
        _VT_TYPE[key] = name;
        return name;
    } catch (e) { return ''; }
}
function isCECTitle(obj) {
    return rttiTypeOf(obj).indexOf('CECTitle') >= 0;
}
"""


#: CTopicPane::GetName 的真实 hook handlers（测试直接 QJSEngine 执行本对象）
JS_GETNAME_HOOK = r"""
{
    onEnter: function (a) {
        this.valid = false;
        this.self = null;
        this.sret = null;
        if (INREC || RESTORING) return;
        try {
            // GetName 按值返回，args[1] 是调用方分配的 sret OUTPUT，onEnter
            // 时尚未由原函数构造；这里严禁 readW/取旧名字/取历史 rec。
            // ON/OFF 都在 onLeave 按“最新的 PAUSED + 已构造的返回值”决定。
            if (!PAUSED) st.nameCalls++;
            var self = a[0];
            if (!_paneValid(self)) return;
            if (!isCECTitle(self)) return;
            this.self = self;
            this.valid = true;
            this.sret = a[1];
        } catch (e) {
            this.valid = false;
            st.nameErrors = (st.nameErrors || 0) + 1;
            if (st.nameErr.length < 8) st.nameErr.push(String(e).slice(0, 160));
        }
    },
    onLeave: function () {
        if (INREC || RESTORING) return;
        if (PAUSED) {
            if (!this.valid || !this.sret || this.sret.isNull()) return;
            try {
                // onLeave 才读已由原函数构造好的返回值。
                var _w = readW(this.sret);
                if (!_w || !_w.s) return;
                var _en = restorePaneOnUI(this.self, _w.s);
                if (!_en) _en = zh2en(_w.s);
                if (_en && _en !== _w.s) {
                    var _br = 'none', _err = '';
                    if (_en.length <= _w.cap) {
                        try {
                            _w.data.writeUtf16String(_en);
                            this.sret.add(0x10).writeU64(_en.length);
                            _br = 'sret';
                        } catch (e) {
                            _err = String(e).slice(0, 80);
                        }
                    }
                    // ★ 2026-11-13：即使英文能塞进本次 sret，也必须更新
                    //   CTopicPane 自己的名字缓存；否则下一帧 DrawName 仍读
                    //   旧中文（BCC Unsharp Mask 16 字符 / cap 15 即此现象）。
                    //   这里只在当前有效 pane 上调用，且用 INREC 防递归。
                    if (fSetName && this.self) {
                        try {
                            if (_paneValid(this.self)) {
                                INREC = true;
                                fSetName(this.self, mkWS(_en));
                                st.pauseSetName = (st.pauseSetName || 0) + 1;
                                _br += '+set';
                            } else {
                                _br += '+invalid';
                            }
                        } catch (e) {
                            st.pauseSetErr = (st.pauseSetErr || 0) + 1;
                            _err = String(e).slice(0, 80);
                            _br += '+set-err';
                        } finally {
                            INREC = false;
                        }
                    } else {
                        _br += '+no-fn';
                    }
                    if (!st.pauseName) st.pauseName = [];
                    if (st.pauseName.length < 8) {
                        st.pauseName.push({ raw: String(_w.s).slice(0, 80),
                                            len: _w.s.length, cap: _w.cap,
                                            target: String(_en).slice(0, 80),
                                            branch: _br, err: _err });
                    }
                }
            } catch (e) {
                st.nameErrors = (st.nameErrors || 0) + 1;
                if (st.nameErr.length < 8) st.nameErr.push('pause: ' + String(e).slice(0, 140));
            }
            return;
        }
        if (!this.valid || !_paneValid(this.self)) return;
        try {
            var p = this.sret;
            if (!p || p.isNull()) return;
            var w = readW(p);
            if (!w || !w.s) return;
            if (!isEffectName(w.s)) return;   // 只收真实效果标题，不收参数/分类
            var _k = this.self.toString();
            if (PANES.indexOf(_k) < 0 && PANES.length < 24) PANES.push(_k);
            var zh = lookup(w.s);
            if (!zh) {
                if (st.nameMiss.length < 60) st.nameMiss.push(w.s);
                return;
            }
            if (zh.length > w.cap) return;
            w.data.writeUtf16String(zh);
            p.add(0x10).writeU64(zh.length);
            st.namePatched++;
            if (st.nameSample.length < 40) st.nameSample.push(w.s + ' -> ' + zh);
            var _sk = this.self.toString() + '|' + zh;
            if (!SETNAMED[_sk]) {
                if (Object.keys(SETNAMED).length > 200) SETNAMED = {};
                SETNAMED[_sk] = 1;
                PANEMAP[this.self.toString()] = { en: w.s, zh: zh };
                try {
                    if (fSetName) {
                        INREC = true;
                        fSetName(this.self, mkWS(zh));
                        INREC = false;
                    }
                } catch (e) {
                    INREC = false;
                    st.nameErrors = (st.nameErrors || 0) + 1;
                    if (st.nameErr.length < 8) st.nameErr.push('SetName: ' + String(e).slice(0, 140));
                }
            }
        } catch (e) {
            st.nameErrors = (st.nameErrors || 0) + 1;
            if (st.nameErr.length < 8) st.nameErr.push('onLeave: ' + String(e).slice(0, 140));
        }
    }
}
"""


JS_TEMPLATE = r"""
function findExport(module, name) {
    var mod = Process.findModuleByName(module);
    return mod ? mod.findExportByName(name) : null;
}
var MAP = MAP_JSON;
// LOW：去空格 + 小写 的兜底表（AE 会传来 ' BCC+Day for Night'、'mocha' 这类名字）
var LOW = {};
for (var k in MAP) {
    var t = k.replace(/^\s+/, '').replace(/\s+$/, '');
    if (t && !Object.prototype.hasOwnProperty.call(LOW, t.toLowerCase())) LOW[t.toLowerCase()] = MAP[k];
    if (!Object.prototype.hasOwnProperty.call(LOW, k.toLowerCase())) LOW[k.toLowerCase()] = MAP[k];
}
// REAL：AE 注册表里真实存在的完整显示名。用于区分「重复实例后缀」和
// 「名字本身就叫 BCC Noise Map 2」——后者若不在词典，绝不能拆成基名。
var REAL = REAL_JSON;
var REAL_OK = REAL_OK_JSON;
// ZH2EN：中文 -> 英文原名（工程文件里带进来的中文也要能反查回来）
var ZH2EN = {}, AMBIGUOUS_ZH = {};
for (var _zk in MAP) {
    var _zv = MAP[_zk], _ze = _zk.replace(/^\s+|\s+$/g, '');
    if (!_zv || !_ze || AMBIGUOUS_ZH[_zv]) continue;
    if (Object.prototype.hasOwnProperty.call(ZH2EN, _zv) && ZH2EN[_zv] !== _ze) {
        delete ZH2EN[_zv];
        AMBIGUOUS_ZH[_zv] = true;
    } else {
        ZH2EN[_zv] = _ze;
    }
}
var PANES = [];     // 见过的 CTopicPane 实例（最多 24 个），关通道时用它触发重绘
var PANEMAP = {};   // pane 地址 -> {en, zh}：我们推给它的中文，关通道时按这里写回英文
var PAUSED = false;   // 关通道时置 true：钩子仍挂着，但一律不改名字
var st = { calls: 0, hit: 0, patched: 0, skip_cap: 0,
           nameCalls: 0, namePatched: 0, nameSample: [], nameMiss: [],
           nameErrors: 0, nameErr: [],
           fillCalls: 0, fillPatched: 0,
           getCalls: 0, getPatched: 0,
           edbCalls: 0, edbCandidates: 0, edbTitles: 0,
           edbPatched: 0, edbReadFail: 0, edbWriteFail: 0,
           edbBt: [], edbBtErr: '', titleSetName: 0,
           pauseName: [], pauseSetName: 0, pauseSetErr: 0,
           dispOff: [], dispPausedNoEn: 0, dispFallbackEn: 0,
           edbOff: [], edbOffMiss: [],
           miss: [], caps: [], sample: [] };

// 被我们改成中文的对象：addr -> 英文原名。关通道时用它原地还原。

var FLIPPED = {};

// ---- 诊断：面板是在哪个函数里调 GetDisplayName 的（找「重建效果控件」入口）----
var RAC = {};
function noteRA(ra) {
    try {
        var m = Process.findModuleByAddress(ra);
        var key = m ? (m.name + '+0x' + ra.sub(m.base).toString(16)) : ra.toString();
        RAC[key] = (RAC[key] || 0) + 1;
    } catch (e) {}
}
var _lastRA = 0;


function isCJK(s) {
    // ★ 2026-11-07 晚：原来只看第一个字符 —— 效果名都以 "S_" / "BCC+" /
    //   "CC " 开头（ASCII），于是「S_修复帧」被判成英文，整条还原链路
    //   一条都认不出来（真机 revert 恒为 0 的根因）。
    if (!s) return false;
    for (var i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c >= 0x2E80 && c <= 0x9FFF) return true;
    }
    return false;
}

PANE_GUARD_JS
LOOKUP_JS

// std::wstring 布局（MSVC）: _Bx[16] | size@0x10 | cap@0x18
function readW(p) {
    var size = p.add(0x10).readU64().toNumber();
    var cap = p.add(0x18).readU64().toNumber();
    if (size < 0 || size > 512 || cap < 0 || cap > 4096) return null;
    var data = (cap < 8) ? p : p.readPointer();
    return { s: data.readUtf16String(size) || '', size: size, cap: cap, data: data };
}

function asciiOk(s) {
    if (!s || s.length < 2 || s.length > 64) return false;
    for (var i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

// 显示名缓存偏移：按版本可能是 0xF0 / 0xE8 / 0xE0
var OFF = [0xF0, 0xE8, 0xE0];
function dispOf(obj) {
    var any = null;
    for (var i = 0; i < OFF.length; i++) {
        var w = readW(obj.add(OFF[i]));
        if (!w || !w.s) continue;
        if (asciiOk(w.s)) return { w: w, off: OFF[i] };
        // ★ 2026-11-07 晚：工程文件带来的中文显示名不是 ASCII，以前这里
        //   直接返回 null → 中文实例永远进不了 FLIPPED → 关通道还原不了。
        if (!any) any = { w: w, off: OFF[i] };
    }
    return any;
}

function putDisp(obj, off, w, zh) {
    var n = zh.length;
    if (n > w.cap) return false;
    w.data.writeUtf16String(zh);
    obj.add(off).add(0x10).writeU64(n);
    return true;
}
// 入参 wstring（const std::wstring&）→ 就地改中文。
// ★ 2026-11-07 20:30：面板拿到 SetDisplayName 的入参后**立刻**用它画标题，
//   所以只有改入参才能「加效果瞬间就是中文」；改 +0xF0 缓存要等面板重建
//   （切图层）才生效，用户明确不接受。
function putArg(p, w, zh) {
    var n = zh.length;
    if (n > w.cap) return false;
    w.data.writeUtf16String(zh);
    p.add(0x10).writeU64(n);
    return true;
}
// 构造一个临时 std::wstring 并返回其地址（给返回值替换用）。
// ★ 2026-11-07 20:40：面板绘制标题时会用 GetDisplayName 的返回值 —— 如果它
//   每次都调，那直接把返回值换成我们的中文 wstring 就能「立刻中文」，
//   根本不需要等面板重建。按中文串缓存，避免每帧分配。
var _WSC = {};
function mkWS(zh) {
    if (_WSC[zh]) return _WSC[zh];
    var n = zh.length;
    var cap = 7;
    if (n > cap) cap = n;
    var buf = Memory.alloc(0x40 + (cap + 1) * 2);
    var data = (n < 8) ? buf : buf.add(0x40);
    data.writeUtf16String(zh);
    if (n >= 8) buf.writePointer(data);
    buf.add(0x10).writeU64(n);
    buf.add(0x18).writeU64(cap);
    _WSC[zh] = buf;
    return buf;
}



// ---- 主路径：SetDisplayName(wstring) ----
// onEnter 只记录（不改入参，避免 AE 把中文写进会被保存的实例名）；
// onLeave 时 AE 已把英文写进 +display_off，就地改成中文。
var sp = findExport('TDB.dll', SYM_JSON);
var fSet = sp;          // 原函数地址：还原时用 NativeFunction 调它重新分配缓冲
if (!sp) {
    send({ k: 'err', msg: 'SetDisplayName not exported' });
} else {
    Interceptor.attach(sp, {
        onEnter: function (a) { if (PAUSED) return;
            st.calls++;
            this.o = a[0];
            this.zh = null;
            this.en = '';
            var w = readW(a[1]);
            if (!w || !w.s || !isEffectName(w.s)) return;
            var zh = lookup(w.s);
            if (!zh) {
                if (st.miss.length < 60) st.miss.push(w.s);
                return;
            }
            st.hit++;
            this.en = w.s;
            this.zh = zh;
            // ★ 只在 onLeave 写 +display_off 缓存；TDB 传入的实例名是会被
            //   序列化进工程的数据，onEnter 绝不改写（不调用 putArg）。
        },
        onLeave: function () { if (PAUSED) return;
            if (!this.zh) return;
            var d = dispOf(this.o);
            if (!d) return;
            if (d.w.s !== this.en) return;   // 缓存里不是刚写的英文 → 不碰
            if (!putDisp(this.o, d.off, d.w, this.zh)) {
                st.skip_cap++;
                if (st.caps.length < 60) st.caps.push(this.en + '|cap=' + d.w.cap + '|need=' + this.zh.length);
                return;
            }
            st.patched++;
            // 记下来，关通道时按这批对象原地写回英文（不需要扫描）
            var _k = this.o.toString();
            recordName(FLIPPED, _k, this.en);
            if (st.sample.length < 40) st.sample.push(this.en + ' -> ' + this.zh);
            try { send({ k: 'patched' }); } catch (e) {}
        }
    });
}

// ---- 兜底：FLT.dll 各 traits 的 FillInDisplayName（绕过 SetDisplayName 的直接填充）----
try {
    var fills = Process.getModuleByName('FLT.dll').enumerateExports();
    for (var i = 0; i < fills.length; i++) {
        var name = fills[i].name || '';
        if (name.indexOf('FillInDisplayName') < 0) continue;
        try {
            Interceptor.attach(fills[i].address, {
                onEnter: function (a) { if (PAUSED) return; this.o = a[0]; },
                onLeave: function () { if (PAUSED) return;
                    st.fillCalls++;
                    if (this.o.isNull()) return;
                    var d = dispOf(this.o);
                    if (!d || !isEffectName(d.w.s)) return;
                    var zh = lookup(d.w.s);
                    if (!zh) return;
                    if (putDisp(this.o, d.off, d.w, zh)) {
                        recordName(FLIPPED, this.o.toString(), d.w.s);
                        st.fillPatched++;
                        try { send({ k: 'patched' }); } catch (e) {}
                    }
                }
            });
        } catch (e) { /* 空函数或不可拦截，跳过 */ }
    }
} catch (e) { /* FLT.dll 未加载 */ }

// ---- 终极路径：直接 hook 绘制函数 EDB_DrawString ----
// ★ 2026-11-07 晚：面板缓存/重绘都不可靠。AE 画任何文字最终都走
//   AfterFXLib 的 EDB_DrawString(drawbot, color&, font*, x, y, const wstring&, ...)
//   —— wstring 是第 4 个整型参数（r9）。在这里把文本换成目标语言，
//   下一帧就是新文字，不需要 SetName/InvalTopic/重绘。
//   用 CTopicPane::DrawName 的进出设立「正在画效果名」标志，保证只翻
//   效果控件面板的标题，绝不碰顶部菜单（用户要求菜单保持英文）。
var HOOKED_GETNAME = {};
function hookSlotE8(fn) {
    if (!fn || fn.isNull()) return;
    var k = fn.toString();
    if (HOOKED_GETNAME[k]) return;
    HOOKED_GETNAME[k] = 1;
    try {
        Interceptor.attach(fn, {
            onEnter: function (a) {
                st.slotCalls = (st.slotCalls || 0) + 1;
                this.sret = a[1];       // rdx = 返回 wstring 的地址
            },
            onLeave: function () {
                var p = this.sret;
                if (!p || p.isNull()) return;
                var w = readW(p);
                if (!w || !w.s) return;
                var t = null;
                if (WANT_ZH) {
                    if (!isEffectName(w.s)) return;
                    t = lookup(w.s);
                } else {
                    t = zh2en(w.s);
                    if (!t || !isEffectName(t)) return;
                }
                if (!t || t === w.s) return;
                if (t.length > w.cap) return;
                w.data.writeUtf16String(t);
                p.add(0x10).writeU64(t.length);
                st.slotPatched = (st.slotPatched || 0) + 1;
            }
        });
    } catch (e) { HOOKED_GETNAME[k] = 2; }
}

var WANT_ZH = false;
var IN_NAME = 0;
var DNHIT = 0;   // DrawName 被调用的次数（三种类层次合计）
var _NAME_SYM = '?DrawName@CTopicPane@@UEAAXPEAVDrawbot@drawbot@dvaui@@@Z';
var _DS_SYM = '?EDB_DrawString@@YAXPEAVDrawbot@drawbot@dvaui@@AEBVColorRGBA@23@'
            + 'PEBUFontInterface@23@MMAEBV?$basic_string@_WU?$char_traits@_W@std@@'
            + 'U?$STLAllocator@_W@allocator@dvacore@@@std@@W4TextAlignment@23@'
            + 'W4TextTruncation@23@M@Z';
function _onDrawName(a) {
    IN_NAME++;
    DNHIT++;
    // ★ 反编译确认（AfterFXLib 2025，DrawName @0x180abd2e0）：
    //   它每帧都走 `mov rax,[rdi]; call [rax+0xe8]` 取名字再画，
    //   所以只要 hook 住这个真实槽位函数并改它的返回值，面板每帧
    //   都是目标语言 —— 不依赖任何重绘/失效调用。
    try {
        var _vt = a[0].readPointer();
        hookSlotE8(_vt.add(0xe8).readPointer());
        // ★ 每帧绘制都经过这里 → 顺手收集 pane 地址。关通道还原后
        //   对它们调 InvalTopic，面板会重画，重画时走上面的 slotE8
        //   钩子（PAUSED → 用 ZH2EN 换回英文）→ 面板立刻变英文，
        //   不需要用户切图层。
        if (a[0] && !a[0].isNull()) {
            var _pk = a[0].toString();
            if (PANES.indexOf(_pk) < 0 && PANES.length < 24) PANES.push(_pk);
        }
    } catch (e) {}
}
function _onLeaveDrawName() { if (IN_NAME > 0) IN_NAME--; }
// ★ 2026-11-08：类层次有 CTopicPane / CTLTopicPane / CTLStream 三种 DrawName
//   导出（实测 AfterFXLib 2025 三个都在）。只挂基类会漏 —— 25.5 的面板行是
//   CTLTopicPane 实例，所以 PANES 一直是 0、关通道后没法触发重绘。
// ★ 2026-11-08：不要再挂 CTLStream::DrawName —— 它被每个流每帧调用，
//   挂上去会把 AE 绘制拖到极慢，进而让我们的同步 RPC 全部卡死
//   （用户实测：程序还开着但日志完全不动、新效果也不汉化）。
var _NAME_SYMS_ALL = [_NAME_SYM,
    '?DrawName@CTLTopicPane@@UEAAXPEAVDrawbot@drawbot@dvaui@@@Z'];
for (var _ni = 0; _ni < _NAME_SYMS_ALL.length; _ni++) {
    try {
        var _dn = findExport('AfterFXLib.dll', _NAME_SYMS_ALL[_ni]);
        if (_dn) {
            Interceptor.attach(_dn, { onEnter: _onDrawName,
                                      onLeave: _onLeaveDrawName });
        } else {
            send({ k: 'warn', msg: 'DrawName not exported: ' + _NAME_SYMS_ALL[_ni] });
        }
    } catch (e) {}
}
try {
    var _ds = findExport('AfterFXLib.dll', _DS_SYM);
    if (_ds) {
        // ★ 2026-11-09 真机证据（round9 probe）：EDB_DrawString 的 wstring 是
        //   第 6 个参数（Win64 args[5] / 栈 [rsp+0x30]），不是 args[3]（float）。
        //   效果控件标题实际由 AfterFXLib+0xa90c3f 这条绘制路径发出；参数名是
        //   +0xa93eba，顶部菜单/其它文本又不是这个返回地址。只在标题调用点
        //   改绘制字符串，避免碰菜单和参数行。
        var UI_TITLE_RET = UI_TITLE_RET_JSON;   // 0 = 未验证版本，不做标题绘制替换
        Interceptor.attach(_ds, {
            onEnter: function (a) {
                st.edbCalls = (st.edbCalls || 0) + 1;
                var w = null;
                try { w = readW(a[5]); } catch (e) { st.edbReadFail++; return; }
                if (!w || !w.s) { st.edbReadFail++; return; }
                var t = null;
                if (WANT_ZH) {
                    if (!isEffectName(w.s)) return;
                    t = lookup(w.s);
                } else {
                    t = zh2en(w.s);
                    if (!t || !isEffectName(t)) return;
                }
                if (!t || t === w.s) return;
                st.edbCandidates = (st.edbCandidates || 0) + 1;
                var titleCall = false;
                var frames = [];
                try {
                    var bt = Thread.backtrace(this.context, Backtracer.ACCURATE);
                    var want = '0x' + Number(UI_TITLE_RET).toString(16);
                    for (var bi = 0; bi < bt.length; bi++) {
                        var m = Process.findModuleByAddress(bt[bi]);
                        if (!m) continue;
                        var off = '0x' + bt[bi].sub(m.base).toString(16);
                        if (frames.length < 6) frames.push(m.name + '+' + off);
                        if (m.name.toLowerCase() === 'afterfxlib.dll' && off === want) {
                            titleCall = true;
                            break;
                        }
                    }
                    if (!titleCall && st.edbBt.length < 10) st.edbBt.push(frames);
                } catch (e) { st.edbBtErr = String(e).slice(0, 120); }
                if (!titleCall) {
                    if (!WANT_ZH) {
                        if (!st.edbOffMiss) st.edbOffMiss = [];
                        if (st.edbOffMiss.length < 8) {
                            st.edbOffMiss.push({ raw: String(w.s).slice(0, 80),
                                                 target: String(t).slice(0, 80),
                                                 frames: frames.slice(0, 4) });
                        }
                    }
                    return;
                }
                st.edbTitles = (st.edbTitles || 0) + 1;
                if (!WANT_ZH) {
                    if (!st.edbOff) st.edbOff = [];
                    if (st.edbOff.length < 8) {
                        st.edbOff.push({ raw: String(w.s).slice(0, 80),
                                         target: String(t).slice(0, 80) });
                    }
                }
                try {
                    // args[5] 在调用者栈 [entry_rsp+0x30]；改指针让 EDB 画中文/英文。
                    this.context.rsp.add(0x30).writePointer(mkWS(t));
                    st.edbPatched = (st.edbPatched || 0) + 1;
                } catch (e) { st.edbWriteFail = (st.edbWriteFail || 0) + 1; }
            }
        });
    } else {
        send({ k: 'warn', msg: 'EDB_DrawString not exported' });
    }
} catch (e) {}

// ---- 绘制端：CTopicPane::GetName 返回 wstring（按值，sret 指针在 rdx）----
// ★ 2026-11-07 21:10：效果控件面板的每一行标题是 CTopicPane（Topic 列表）自己
//   的 GetName() 返回值画的 —— 之前只 hook 了 TDB_StreamBase::GetDisplayName，
//   面板把名字拷进自己的 topic 后就不再读，所以改缓存/改返回值都刷不动。
//   这里直接把 GetName 的返回值换成中文，面板下一次绘制就是中文。
var fInvalTopic = null;
var lastInval = 0;
// ★ 2026-11-07 晚：CTopicPane 自己缓存了一份名字（DrawName 用它画），
//   GetName 只在重建时被调用 —— 所以只改 GetName 返回值只生效一次。
//   正确做法是改名后调 CTopicPane::SetName 更新它自己的缓存，再 UpdateNow 重绘。
var fSetName = null;
var fUpdateNow = null;

// ★ 只能在 AE UI 线程（GetName/SetName 钩子）调用。RPC 线程直接遍历
//   PANEMAP/PANES 调这些原生 UI 函数会在 pane 已释放时崩溃。
function restorePaneOnUI(obj, currentName) {
    // 历史账本只有在“当前显示文本 == 当时推的中文/英文”时才可信；
    // 地址复用后的旧 rec 直接丢弃。
    if (!obj || !_paneValid(obj) || !isCECTitle(obj)) return "";
    var k = obj.toString();
    var rec = PANEMAP[k];
    if (!rec) return "";
    delete PANEMAP[k];
    if (currentName && (currentName === rec.zh || currentName === rec.en)) {
        return rec.en || "";
    }
    return "";
}


var INREC = false;          // 防 SetName→GetName 递归
var SETNAMED = {};          // pane 地址+名字 → 已推送过
var PENDING_INVAL = false;  // RPC 只记录意图；原生 UI 动作一律不在 RPC 线程做
var RESTORING = false;      // 防 restorePaneOnUI 重入
try {
    var _sn = findExport('AfterFXLib.dll',
        '?SetName@CTopicPane@@UEAAXAEBV?$basic_string@_WU?$char_traits@_W@std@@'
        + 'U?$STLAllocator@_W@allocator@dvacore@@@std@@@Z');
    if (_sn) {
        fSetName = new NativeFunction(_sn, 'void', ['pointer', 'pointer']);
        // CECTitle 的 SetName 是真实效果控件标题缓存来源；在源头改入参，
        // 不碰 CTLGroupEffect（时间线）和参数/分类 pane。
        Interceptor.attach(_sn, {
            onEnter: function (a) {
                if (!WANT_ZH || INREC) return;
                try {
                    var self = a[0];
                    if (!_paneValid(self) || !isCECTitle(self)) return;
                    var w = readW(a[1]);
                    if (!w || !w.s || !isEffectName(w.s)) return;
                    var zh = lookup(w.s);
                    if (!zh || zh === w.s) return;
                    if (putArg(a[1], w, zh)) {
                        var _pk = self.toString();
                        PANEMAP[_pk] = { en: w.s, zh: zh };
                        if (PANES.indexOf(_pk) < 0 && PANES.length < 24) PANES.push(_pk);
                        this.changed = true;
                        st.titleSetName = (st.titleSetName || 0) + 1;
                    }
                } catch (e) {
                    st.nameErrors = (st.nameErrors || 0) + 1;
                    if (st.nameErr.length < 8) st.nameErr.push('SetNameHook: ' + String(e).slice(0, 140));
                }
            }
        });
    }
    var _un = findExport('AfterFXLib.dll',
        '?UpdateNow@CTopicPane@@QEAAXXZ');
    if (_un) fUpdateNow = new NativeFunction(_un, 'void', ['pointer']);
} catch (e) {}
try {
    var _ia = findExport('AfterFXLib.dll', '?InvalTopic@CTopicPane@@UEAAXXZ');
    if (_ia) fInvalTopic = new NativeFunction(_ia, 'void', ['pointer']);
} catch (e) {}

var SYM_TNAME = '?GetName@CTopicPane@@UEAA?AV?$basic_string@_WU?$char_traits@_W@std@@'
              + 'U?$STLAllocator@_W@allocator@dvacore@@@std@@XZ';
var tg = findExport('AfterFXLib.dll', SYM_TNAME);
if (tg) {
    Interceptor.attach(tg, GETNAME_HOOK_JS);
} else {
    send({ k: 'warn', msg: 'CTopicPane::GetName not exported' });
}

// ---- 读取端：面板刷新时读显示名 → 顺手把英文缓存改成中文 ----
var _lastSend = 0;
var gp = findExport('TDB.dll', SYM_GET_JSON);
if (gp) {
    Interceptor.attach(gp, {
        onLeave: function (ret) {
            if (PAUSED) {
                // ★ 关通道：把中文换成英文返回（不改内存、不需要还原/重绘）
                var _en = this.en;
                if (!_en && this.en0) {
                    // ON 的 onEnter 已把本帧显示改成中文，随后才收到 OFF：
                    // 不能只依赖 this.en（它只在 PAUSED 的 onEnter 算），
                    // 必须用 ON 前保存的原英文兜底。
                    _en = this.en0;
                    st.dispFallbackEn = (st.dispFallbackEn || 0) + 1;
                }
                if (!_en && this.zh) {
                    _en = zh2en(this.zh);
                    if (_en) st.dispFallbackEn = (st.dispFallbackEn || 0) + 1;
                }
                if (_en) {
                    try { this.context.rax = mkWS(_en); } catch (e) {}
                } else {
                    st.dispPausedNoEn = (st.dispPausedNoEn || 0) + 1;
                }
                if (!st.dispOff) st.dispOff = [];
                if (st.dispOff.length < 8) {
                    var _ret = '';
                    try {
                        if (ret && !ret.isNull()) {
                            var _rw = readW(ret);
                            if (_rw && _rw.s) _ret = String(_rw.s).slice(0, 80);
                        }
                    } catch (e) {}
                    st.dispOff.push({
                        enter: this.paused0 === true,
                        en: !!this.en,
                        e0: this.en0 ? String(this.en0).slice(0, 80) : '',
                        zh: this.zh ? String(this.zh).slice(0, 80) : '',
                        ret: _ret
                    });
                }
                return;
            }
            // 面板如果直接用返回值绘制，这里换成中文串 → 立刻变中文。
            if (this.zh) {
                try { this.context.rax = mkWS(this.zh); } catch (e) {}
            }
        },
        onEnter: function (a) {
            this.paused0 = PAUSED;
            this.en0 = null;
            if (PAUSED) {
                // 面板每帧都来读显示名（实测 4 秒 1500 次），关通道时直接
                // 把中文换成英文返回，下一帧标题就是英文 —— 不依赖还原、
                // 不依赖重绘、不依赖 InvalTopic。
                try {
                    var o0 = a[0];
                    if (!o0.isNull()) {
                        var d0 = dispOf(o0);
                        if (d0 && isCJK(d0.w.s)) {
                            var e0 = recordedEnglish(FLIPPED, o0.toString(), d0.w.s) || zh2en(d0.w.s);
                            if (e0 && isEffectName(e0)) {
                                this.en = e0;
                                st.pauseFix = (st.pauseFix || 0) + 1;
                            }
                        }
                    }
                } catch (e) {}
                return;
            }
            st.getCalls++;
            // Project loading also reads non-effect names here. Keep this hot
            // callback free of stack unwinding/module diagnostics: with the
            // EDB interceptor installed those diagnostics stalled AE2025's
            // subsequent script dispatch in the native reopen A/B test.
            var o = a[0];
            if (o.isNull()) return;
            var d = dispOf(o);
            if (!d) return;
            var key = o.toString();
            if (isCJK(d.w.s)) {
                // 已经是中文：可能是工程文件带来的（不是我们改的）。
                // 记进 FLIPPED，关通道时才能按对象原地写回英文。
                var _e = recordedEnglish(FLIPPED, key, d.w.s) || zh2en(d.w.s);
                if (_e && isEffectName(_e)) {
                    // ★ 2026-11-13：ON 进入时缓存已是中文（不是我们本轮改的）。
                    //   若 onEnter 后、onLeave 前收到 OFF，paused onLeave
                    //   没有 this.en/this.zh 可用；这里保存原英文兜底。
                    this.en0 = _e;
                    recordName(FLIPPED, key, _e);
                }
                return;
            }
            if (!isEffectName(d.w.s)) return;
            // ★ 2026-11-13：在 putDisp 改中文之前保存原英文；若本轮 ON
            //   onEnter 之后、onLeave 之前收到 OFF，paused 分支用它兜底。
            this.en0 = d.w.s;
            var zh = lookup(d.w.s);
            if (!zh) return;
            this.zh = zh;
            if (putDisp(o, d.off, d.w, zh)) {
                st.getPatched++;
                recordName(FLIPPED, key, d.w.s);
                if (st.sample.length < 40) st.sample.push('get ' + d.w.s + ' -> ' + zh);
                // 让宿主轻推一次重绘（400ms 节流，避免每帧都推）
                var _now = Date.now();
                if (_now - _lastSend > 400) {
                    _lastSend = _now;
                    try { send({ k: 'patched' }); } catch (e) {}
                }
            }
        }
    });
} else {
    send({ k: 'warn', msg: 'GetDisplayName not exported' });
}

// ★ 模块/窗口可能晚于 attach 就绪：只有 TDB SetDisplayName 已解析才宣告 ready，
//   否则 Python ensure 必须重试，不能把空脚本当成已挂上。
if (sp && tg && _sn && fSetName) {
    try { send({ k: 'ready' }); } catch (e) {}
} else {
    try {
        send({ k: 'err', msg: 'SetDisplayName/GetName/SetName not ready' });
    } catch (e) {}
}

rpc.exports = {
    stats: function () {
        return Object.assign({}, st, { dn: DNHIT, panes: PANES.length });
    },
    setreal: function (r) {
        // 启动期注册表可能后增长：不重挂会话，只热更新 REAL 白名单。
        REAL = r || {};
        REAL_OK = false;
        for (var _k in REAL) { REAL_OK = true; break; }
        return REAL_OK;
    },
    setwant: function (b) {
        WANT_ZH = !!b;
        return WANT_ZH;
    },
    setpaused: function (b) {
        // ★ RPC 线程绝不调用 fSetName/fUpdateNow/fInvalTopic，也不遍历
        //   PANEMAP/PANES 的原生地址：这些是 AE UI 对象，pane 释放后调用
        //   会直接崩溃。只设置状态；真正恢复在 GetName 钩子的 UI 线程做。
        PAUSED = !!b;
        WANT_ZH = !PAUSED;
        PENDING_INVAL = true;
        return PANES.length;
    },
    invalidate: function () {
        // 只排队；下一次 UI 线程 GetName 只对当前 pane 做一次原生失效。
        // 不在此处遍历 PANES，避免把长期/已释放地址从 RPC 线程传给原生 UI。
        PENDING_INVAL = true;
        return PANES.length;
    },
    ra: function () { return RAC; },
    revertAddrs: function (arr) {
        // RPC 线程不再拿历史 stream 地址调 AE 的 SetDisplayName；旧地址可能
        // 已释放。英文恢复交给 PAUSED 的 GetDisplayName/GetName 读返回端。
        st.revertQueued = (st.revertQueued || 0) + ((arr || []).length);
        return { total: (arr || []).length, written: 0,
                 skipped: (arr || []).length, pending: true, hasFn: false,
                 cn: 0, en: 0, err: 'deferred-to-ui-reader', sample: [] };
    },
    revert: function () {
        // RPC 线程不遍历历史 FLIPPED 地址、不调 SetDisplayName。旧地址可能
        // 已销毁；英文恢复由 PAUSED 的显示名读取钩子按当前对象返回。
        st.revertQueued = (st.revertQueued || 0) + Object.keys(FLIPPED).length;
        return 0;
    }
};
"""


def build_js_source(mapping: dict, real_names=(), ui_title_ret=None) -> str:
    """构造实际注入的 JS，并读回确认所有占位符已替换。"""
    real = {str(n): 1 for n in (real_names or ()) if n}
    src = (JS_TEMPLATE
           .replace("MAP_JSON", json.dumps(mapping, ensure_ascii=False))
           .replace("REAL_JSON", json.dumps(real, ensure_ascii=False))
           .replace("REAL_OK_JSON", "true" if real else "false")
           .replace("UI_TITLE_RET_JSON", str(int(ui_title_ret or 0)))
           .replace("PANE_GUARD_JS", JS_PANE_GUARD)
           .replace("GETNAME_HOOK_JS", JS_GETNAME_HOOK)
           .replace("LOOKUP_JS", JS_LOOKUP)
           .replace("SYM_JSON", json.dumps(SYM_SET_W))
           .replace("SYM_GET_JSON", json.dumps(SYM_GET_W)))
    for token in ("MAP_JSON", "REAL_JSON", "REAL_OK_JSON", "UI_TITLE_RET_JSON",
                  "PANE_GUARD_JS", "GETNAME_HOOK_JS", "LOOKUP_JS",
                  "SYM_JSON", "SYM_GET_JSON"):
        if token in src:
            raise RuntimeError("JS 模板占位符未替换: %s" % token)
    return src


class DisplayHook:
    """常驻 frida 会话；AE 重启后自动重挂。线程安全、失败不抛。"""

    READY_TIMEOUT = 2.0

    def __init__(self):
        # ★★ 2026-11-09：两把锁，职责必须分开。
        #   _op_lock：串行化 ensure/pause/resume/revert/stats 等会调
        #             exports_sync 的阻塞 RPC。持有它意味着「可能在等 AE」。
        #   _state_lock：只保护几毫秒就能读完的状态快照。running()/paused()/
        #             pid()/panes() 只拿它，绝不拿 _op_lock —— 否则 UI 线程
        #             状态查询会跟着一个卡住的 RPC 一起等几十秒（父代理已用
        #             真实 DisplayHook + 阻塞 setpaused 复现 0.516s）。
        #   RLock 允许 resume/pause 失败时在 _op_lock 内调用 _stop_locked。
        self._op_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._session = None
        self._script = None
        self._pid = 0
        self._map_sig = None
        self._paused = False
        self._panes = 0
        self._ready_event = threading.Event()
        self.last_error = ""
        self.last_error_count = 0
        self.last_stats = {}
        #: 每次真正写入中文后回调（用于轻推一次重绘），由调用方设置
        self.on_patch = None

    # ---------- 状态快照（绝不阻塞在 RPC 上） ----------
    def paused(self) -> bool:
        with self._state_lock:
            return self._paused

    def running(self) -> bool:
        with self._state_lock:
            # 只有 session 不算「挂着」：脚本可能已经 unload/失效。
            return (self._session is not None and self._script is not None
                    and self._pid > 0)

    def pid(self) -> int:
        with self._state_lock:
            return self._pid

    def panes(self) -> int:
        with self._state_lock:
            return int(self._panes or 0)

    # ---------- 生命周期 ----------
    def ensure(self, pid: int, mapping: dict, real_names=None,
               ui_title_ret=None) -> bool:
        """确保对 pid 挂着；pid 变了或没挂就重挂。返回是否已挂上。

        real_names: AE 注册表里真实存在的完整显示名集合。JS 查词用它区分
        「重复实例后缀」和「本身就叫 BCC Noise Map 2 的真实效果」。
        ui_title_ret: 仅当前验证过的 25.5.0.4 传 0xa90c3f；其它版本传 0，
        JS 不做标题绘制替换，避免把固定 callsite 误用到别的 AE。
        """
        try:
            pid = int(pid)
        except Exception:
            return False
        if not pid or not mapping:
            return self.running()
        sig = len(mapping)
        with self._op_lock:
            with self._state_lock:
                if (self._session is not None and self._script is not None
                        and self._pid == pid):
                    return True
            self._stop_locked()
            return self._start_locked(pid, mapping, sig, real_names or (),
                                      ui_title_ret)

    def stop(self) -> None:
        with self._op_lock:
            self._stop_locked()

    def pause(self) -> bool:
        """停用钩子（不卸载 frida 会话）：回调立即 return，重新开通道只要 resume。

        幂等：已经暂停就不再重复 revert/重绘（否则关通道期间每拍都跑一遍
        rpc + 失效重绘，日志和界面都会「一直循环」）。
        失败时丢弃会话，保证「关」之后绝不会还有一个活着的钩子。
        """
        with self._op_lock:
            with self._state_lock:
                sc = self._script
                already = self._paused
            if sc is None:
                return True
            if already:
                return True
            try:
                panes = int(sc.exports_sync.setpaused(True) or 0)
            except Exception as exc:
                self.last_error = "pause 失败: %s" % exc
                try:
                    self._stop_locked()
                except Exception:
                    pass
                return False
            with self._state_lock:
                self._panes = panes
                self._paused = True
            return True

    def resume(self) -> bool:
        """恢复钩子。返回是否真的恢复成功。

        ★ 2026-11-08：以前这里 `if sc is None or not self._paused: return`，
          而脚本一旦失效（AE 侧 unload / 频道抖动），setpaused 抛异常又被
          `except: pass` 吞掉 → 界面看着「开」，JS 里 PAUSED 却一直是 true，
          表现就是「第二遍开通道完全不汉化」。现在失败就丢掉会话，
          由 ensure() 重挂。
        ★ 2026-11-09：不再用 Python 的 _paused 当早退条件 —— 它可能和 JS
          里的 PAUSED 不一致；每次 ON 都实际发一次 setpaused(False) 校正。
        """
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None:
                with self._state_lock:
                    self._paused = False
                return False
            try:
                sc.exports_sync.setpaused(False)
            except Exception as exc:
                self.last_error = "resume 失败: %s" % exc
                try:
                    self._stop_locked()
                except Exception:
                    pass
                with self._state_lock:
                    self._paused = False
                return False
            with self._state_lock:
                self._paused = False
            return True

    def revert_now(self) -> int:
        """让 JS 把改过的对象原地写回英文（瞬时，不扫描）。返回条数。"""
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None:
                return 0
            try:
                return int(sc.exports_sync.revert() or 0)
            except Exception:
                return 0

    def revert_addrs(self, addrs):
        """按对象地址把中文显示名强还原成英文（不依赖 FLIPPED 账本）。

        工程文件里带进来的中文实例从未经过钩子的写入路径，账本里没有它们，
        只能靠发现链给出的地址逐个读 +display_off 再反查英文写回。
        """
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None or not addrs:
                return {}
            try:
                # ★ frida 把 JS 的 camelCase RPC 名在 Python 侧映射成 snake_case，
                #   之前一直用 revertAddrs 调 → 永远 "unable to find method"。
                fn = getattr(sc.exports_sync, "revert_addrs", None)
                if fn is None:
                    fn = getattr(sc.exports_sync, "revertAddrs", None)
                if fn is None:
                    return {"err": "no revert_addrs export"}
                return dict(fn([int(a) for a in list(addrs)[:4096]]) or {})
            except Exception as exc:
                return {"err": "%s: %s" % (type(exc).__name__, exc)}

    def set_real_names(self, names) -> bool:
        """热更新 JS 的 REAL 效果名白名单（注册表启动期增长时）。"""
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None:
                return False
            try:
                real = {str(n): 1 for n in (names or ()) if n}
                sc.exports_sync.setreal(real)
                return True
            except Exception as exc:
                self.last_error = "setreal: %s" % exc
                return False

    def invalidate(self) -> int:
        """只让已收集的真实 CECTitle pane 失效重绘（ON/OFF 后都可用）。"""
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None:
                return 0
            try:
                return int(sc.exports_sync.invalidate() or 0)
            except Exception as exc:
                self.last_error = "invalidate: %s" % exc
                return 0

    def stats(self) -> dict:
        with self._op_lock:
            with self._state_lock:
                sc = self._script
            if sc is None:
                return {}
            try:
                st = dict(sc.exports_sync.stats() or {})
            except Exception:
                with self._state_lock:
                    return self.last_stats
            st["script_error"] = self.last_error
            st["script_errors"] = self.last_error_count
            with self._state_lock:
                self.last_stats = st
            return st

    def _stop_locked(self) -> None:
        # 调用方持有 _op_lock。先把状态摘掉：即使下面的 revert/unload/detach
        # 卡住，running()/paused()/pid() 也能立即返回「已停」，UI 不会等。
        with self._state_lock:
            sc, se = self._script, self._session
            self._script = None
            self._session = None
            self._pid = 0
            self._map_sig = None
            self._paused = False
            self._panes = 0
        if sc is not None:
            # ★ 关通道：先让 JS 把改过的对象原地写回英文（瞬时，无需扫描）
            try:
                n = sc.exports_sync.revert()
                if n:
                    self.last_error = ""
                    with self._state_lock:
                        self.last_stats = {"reverted": n}
            except Exception:
                pass
            try:
                sc.unload()
            except Exception:
                pass
        if se is not None:
            try:
                se.detach()
            except Exception:
                pass

    def _start_locked(self, pid: int, mapping: dict, sig: int,
                      real_names=(), ui_title_ret=None) -> bool:
        try:
            import frida
        except Exception as exc:
            self.last_error = "frida 不可用: %s" % exc
            return False
        try:
            session = frida.attach(pid)
        except Exception as exc:
            self.last_error = "attach 失败: %s" % exc
            return False
        try:
            src = build_js_source(mapping, real_names, ui_title_ret)
            self._ready_event.clear()
            script = session.create_script(src)
            script.on("message", self._on_message)
            script.load()
            if not self._ready_event.wait(self.READY_TIMEOUT):
                _prev = self.last_error
                self.last_error = ("脚本未就绪(TDB SetDisplayName/GetName/"
                                   "SetName 未解析)，下一拍重挂"
                                   + ("；" + _prev if _prev else ""))[:400]
                try:
                    script.unload()
                except Exception:
                    pass
                try:
                    session.detach()
                except Exception:
                    pass
                return False
        except Exception as exc:
            self.last_error = "脚本加载失败: %s" % exc
            try:
                session.detach()
            except Exception:
                pass
            return False
        with self._state_lock:
            self._session = session
            self._script = script
            self._pid = pid
            self._map_sig = sig
            self.last_error = ""
        # 绘制端默认按中文画（ensure() 只在通道打开时被调用）
        try:
            script.exports_sync.setwant(True)
        except Exception:
            pass
        return True

    def _on_message(self, message, data) -> None:
        # 只在真正写入中文时通知（用于轻推重绘）；frida 侧异常必须留痕，
        # 否则 GetName/PANES 出错会表现成「统计在涨、界面永远不动」。
        try:
            if message.get("type") == "error":
                desc = str(message.get("description") or message.get("stack") or "")
                self.last_error = ("script error: " + desc)[:400]
                self.last_error_count = int(getattr(self, "last_error_count", 0)) + 1
                return
            if message.get("type") == "send":
                payload = message.get("payload") or {}
                if payload.get("k") == "ready":
                    self._ready_event.set()
                    self.last_error = ""
                    return
                if payload.get("k") == "err":
                    self.last_error = str(payload.get("msg") or "script error")[:300]
                    return
                if payload.get("k") == "ra":
                    cb = getattr(self, "on_ra", None)
                    if cb:
                        try:
                            cb(payload.get("d") or {})
                        except Exception as exc:
                            self.last_error = "on_ra: %s" % exc
                    return
                if payload.get("k") == "patched" and self.on_patch:
                    try:
                        self.on_patch()
                    except Exception as exc:
                        self.last_error = "on_patch: %s" % exc
        except Exception as exc:
            self.last_error = "on_message: %s" % exc
