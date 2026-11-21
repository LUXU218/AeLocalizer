import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const cells=new Map(),hooks=new Map();let failLength=false,next=0x10000;
class P {
 constructor(n){this.n=Number(n)} add(n){return new P(this.n+Number(n))}
 sub(n){return new P(this.n-Number(n))} compare(p){return Math.sign(this.n-p.n)}
 equals(p){return this.n===p.n} isNull(){return this.n===0} toString(){return '0x'+this.n.toString(16)}
 readByteArray(n){return ctx.Memory.readByteArray(this,n)}
 readPointer(){return new P(cells.get(this.n+'.ptr')||0)} writePointer(p){cells.set(this.n+'.ptr',p.n)}
 readU64(){return {toNumber:()=>cells.get(this.n+'.u64')||0}}
 readU32(){return cells.get(this.n+'.u32')||0} readCString(n){return (cells.get(this.n+'.cstr')||'')+(n?'trailing bytes':'')}
 writeU64(n){if(failLength){failLength=false;throw Error('length write failed')}cells.set(this.n+'.u64',Number(n))}
 readUtf16String(n){return (cells.get(this.n+'.s')||'').slice(0,n)}
 writeUtf16String(s){cells.set(this.n+'.s',s)}
}
const exports={static:200,popup:300,popupbutton:400,source:500};
const lib={base:new P(0x400000),size:0x100000,getExportByName:n=>new P(exports[n])};
const ctx={ptr:x=>x instanceof P?x:new P(x),Process:{getModuleByName:()=>lib},
 Module:{getExportByName:(_,n)=>lib.getExportByName(n)},
 Memory:{readByteArray:(p,n)=>{
   const s=cells.get(p.n+'.cstr');if(s!==undefined){const out=new Uint8Array(n).fill(0xa5);out.set(Buffer.from(s));out[s.length]=0;return out;}
   return new Uint8Array([p.n===500?0xdd:({16:0xaa,32:0xbb,48:0xcc})[p.n-0x400000]]).slice(0,n);
 }},
 Interceptor:{attach:(p,cb)=>hooks.set(p.n,cb),detachAll:()=>{}},rpc:{exports:{}},send:()=>{}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
const api=ctx.rpc.exports;
assert.ok(hooks.has(300),'cached popup display needs its own paint hook');
function value(text,off=0x100,cap=7){const a=new P(next);next+=0x1000;const s=a.add(off);s.add(16).writeU64(text.length);s.add(24).writeU64(cap);const d=cap<=7?s:new P(a.n+0x800);if(cap>7)s.writePointer(d);d.writeUtf16String(text);return {a,s,d,text};}
function control(type){
 const obj=new P(next);next+=0x1000;const vt=new P(type==='parameter'?0x401000:0x401100);
 const col=new P(type==='parameter'?0x402000:0x402100),td=new P(type==='parameter'?0x403000:0x403100);
 obj.writePointer(vt);vt.sub(8).writePointer(col);cells.set(col.n+'.u32',1);
 cells.set(col.n+20+'.u32',col.n-lib.base.n);cells.set(col.n+12+'.u32',td.n-lib.base.n);
 cells.set(td.n+16+'.cstr',type==='parameter'?'.?AVCECStaticTextElement@internal@ecp@@':'.?AVCECStaticTextWhichAcceptsFocus@internal@ecp@@');return obj;
}
function enter(h,v,type='parameter'){const call={threadId:1};if(h===hooks.get(200)){call.source={threadId:1};hooks.get(500).onEnter.call(call.source,[control(type)])}h.onEnter.call(call,[v.a]);return call}
function leave(h,c){h.onLeave.call(c,new P(0));if(c.source)hooks.get(500).onLeave.call(c.source,new P(0))}
api.mode(true,1);
const h=hooks.get(200),p=hooks.get(300);
const title=value('S_Glow');let c=enter(h,title,'title');assert.equal(title.d.readUtf16String(20),'S_Glow','effect title must stay under its own switch');leave(h,c);
const collision=value('Threshold',0x100,16);c=enter(h,collision);assert.equal(collision.d.readUtf16String(20),'阈值','an effect with the same name must not suppress a parameter');leave(h,c);
const sameTitle=value('Threshold',0x100,16);c=enter(h,sameTitle,'title');assert.equal(sameTitle.d.readUtf16String(20),'Threshold');leave(h,c);
const unknown=value('Amount');h.onEnter.call({threadId:2},[unknown.a]);assert.equal(unknown.d.readUtf16String(20),'Amount','unidentified source must not be writable');
const v=value('Amount');c=enter(h,v);assert.equal(v.d.readUtf16String(20),'数量');assert.equal(api.stats().active,2);assert.equal(api.drain(),2);
api.mode(false,2);leave(h,c);assert.equal(v.d.readUtf16String(20),'Amount');assert.equal(api.stats().active,0);assert.equal(api.drain(),0);
const off=value('Screen',0x180);c=enter(p,off);assert.equal(off.d.readUtf16String(20),'Screen');leave(p,c);
api.mode(true,3);const on=value('Screen',0x180);c=enter(p,on);assert.equal(on.d.readUtf16String(20),'滤色');leave(p,c);assert.equal(on.d.readUtf16String(20),'Screen');
const empty=value('',0x180),errBefore=api.stats().popup_err;c=enter(p,empty);leave(p,c);assert.equal(api.stats().popup_err,errBefore,'empty initial dropdown is a valid no-op');
const fault=value('Amount');failLength=true;c=enter(h,fault);leave(h,c);assert.equal(fault.d.readUtf16String(20),'Amount','failed length update must restore original bytes');assert.equal(api.stats().active,0);assert.equal(api.drain(),0);
const moved=value('Amount');c=enter(h,moved);moved.s.add(24).writeU64(12);moved.d.writeUtf16String('foreign');leave(h,c);assert.equal(moved.d.readUtf16String(20),'foreign','changed owner must not be overwritten');
console.log(JSON.stringify({ok:true,popup_replaced:api.stats().popup_replaced,active:api.stats().active}));
