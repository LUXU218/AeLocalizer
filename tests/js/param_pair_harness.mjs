// Execute the actual ParamDrawHook JS with fake Frida pointer semantics.
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync(process.argv[2], 'utf8');
const extra = process.argv[3] ? JSON.parse(process.argv[3]) : null;
let next = 0x100000;
const heap = new Map();
const allocations = [];
const hooks = new Map();
const hookTypes = new Map();
let probeHooks = 0, objectHooks = 0, leaveTraps = 0;
const sent = [];
const throwAt = new Set();
let commitThenThrow = false;
class Pointer {
  constructor(address) { this.address = Number(address); }
  add(n) { return new Pointer(this.address + Number(n)); }
  equals(p) { return !!p && this.address === p.address; }
  isNull() { return this.address === 0; }
  toString() { return '0x' + this.address.toString(16); }
  readByteArray(n) { return context.Memory.readByteArray(this,n); }
  readPointer() { return new Pointer(heap.get(this.address + '.ptr') || 0); }
  writePointer(p) {
    const doThrow = throwAt.has(this.address);
    if (doThrow && !commitThenThrow) throw new Error('injected write failure');
    heap.set(this.address + '.ptr', p.address);
    if (doThrow) throw new Error('injected write-after-commit');
    return this;
  }
  readU64() { return { toNumber: () => Number(heap.get(this.address + '.u64') || 0) }; }
  writeU64(n) { heap.set(this.address + '.u64', Number(n)); return this; }
  writeByteArray() { return this; }
  writeUtf16String(s) { heap.set(this.address + '.str', s); return this; }
  readUtf16String(n) { return (heap.get(this.address + '.str') || '').slice(0, n); }
}
const context = {
  Process: { getModuleByName: () => ({
    base: new Pointer(0x400000),
    getExportByName: () => new Pointer(0x400000 + 0x8d9d20),
  }) },
  Memory: {
    alloc(n) {
      const p = new Pointer(next); next += 0x1000;
      allocations.push({addr: p.address, ref: new WeakRef(p)});
      return p;
    },
    readByteArray(addr, n) {
      const a = addr.address;
      const lib = {
        [0x400000 + 0xa93ea4]: 'c5fa11442420',
        [0x400000 + 0xa93eb5]: 'e8665ee4ff',
        [0x400000 + 0xa93eba]: '90488b942480000000',
      };
      if (extra) {
        for (const key of ['pre', 'call', 'ret'])
          lib[0x400000 + extra[key + '_rva']] = extra[key + '_bytes'];
      }
      if (lib[a]) {
        const hex = lib[a].slice(0, n * 2);
        return new Uint8Array(hex.match(/../g).map(x => parseInt(x, 16)));
      }
      const buf = heap.get(a + '.bytes');
      if (buf) return new Uint8Array(buf).slice(0, n);
      throw new Error('bad read ' + a.toString(16));
    },
  },
  Interceptor: {
    attach(addr, cb) {
      const key = addr.address - 0x400000;
      const probe = typeof cb === 'function';
      hookTypes.set(key, typeof cb);
      if (probe) {
        probeHooks++;
      } else {
        objectHooks++;
        // Object form always has a non-NULL on_leave iface in Frida 16.6.6.
        leaveTraps++;
      }
      hooks.set(key, probe ? {onEnter: cb, probe: true} : cb);
      return { detach() { hooks.delete(key); } };
    },
    detachAll() { hooks.clear(); },
  },
  rpc: { exports: {} },
  Uint8Array,
  WeakRef,
  send(m) { sent.push(m); },
  console,
};
vm.createContext(context);
vm.runInContext(src, context);
const rpc = context.rpc.exports;
function putWide(str) {
  const addr = 0x700000 + next;
  const buf = new Pointer(0x800000 + next);
  heap.set(buf.address + '.str', str);
  const obj = new Pointer(addr);
  obj.writePointer(buf);
  obj.add(16).writeU64(str.length);
  obj.add(24).writeU64(Math.max(str.length, 8));
  return obj;
}
function slot(rsp) { return new Pointer(rsp + 0x28); }
function initSlot(rsp, obj) { const h = slot(rsp); h.writePointer(obj); return h; }
function callPre(tid, rsp, site = 0xa93ea4) {
  hooks.get(site).onEnter.call(
    {threadId: tid, context: {rsp: new Pointer(rsp)}});
}
function callPost(tid, rsp, site = 0xa93eba) {
  hooks.get(site).onEnter.call(
    {threadId: tid, context: {rsp: new Pointer(rsp)}});
}
function alive(list) { return list.filter(x => x.ref.deref() !== undefined).length; }
function gc() { if (global.gc) global.gc(); }

const results = {};
results.probe_hooks = probeHooks;
results.object_hooks = objectHooks;
results.leave_traps = leaveTraps;
results.pre_callback_type = hookTypes.get(0xa93ea4);
results.post_callback_type = hookTypes.get(0xa93eba);
const map = {Amount: '强度', Radius: '半径', Brightness: '亮度'};
rpc.mode(true, 1);
rpc.setmap(map);
const objA = putWide('Amount');
const hA = initSlot(0x900000, objA);
callPre(1, 0x900000);
results.changed = !hA.readPointer().equals(objA);
results.active1 = rpc.stats().active;
results.pending1 = rpc.stats().pending;
const allocBeforeMap = allocations.slice();
rpc.setmap({...map, Amount: '另一个强度'});
gc();
results.aliveAfterMap = alive(allocBeforeMap);
results.activeMap = rpc.stats().active;

// B: publish/account succeeds, slot write throws before commit. A must remain.
commitThenThrow = false;
const objB = putWide('Radius');
initSlot(0x910000, objB);
throwAt.add(0x910028);
callPre(2, 0x910000);
results.beforeBPost = rpc.stats();
callPost(2, 0x910000);
results.afterBPost = rpc.stats();
results.bSlot = slot(0x910000).readPointer().equals(objB);
results.aStillPending = rpc.stats().pending;
results.aActive = rpc.stats().active;
callPost(2, 0x920000);            // wrong RSP: must not release
results.afterWrongRsp = rpc.stats();
results.activeWrong = rpc.stats().active;
callPost(1, 0x900000);            // A late return still releases its own record
results.afterALate = rpc.stats();
results.restored = hA.readPointer().equals(objA);

// C: write commits then throws; its own RETURN must release it, not A.
throwAt.clear();
commitThenThrow = true;
const objC = putWide('Brightness');
initSlot(0x930000, objC);
throwAt.add(0x930028);
callPre(3, 0x930000);
results.afterC = rpc.stats();
callPost(3, 0x930000);
results.afterCPost = rpc.stats();
results.cSlot = slot(0x930000).readPointer().equals(objC);

// OFF: no new replacement; a pending RETURN still cleans up.
throwAt.clear();
commitThenThrow = false;
rpc.mode(false, 2);
const objD = putWide('Amount');
initSlot(0x940000, objD);
callPre(4, 0x940000);
results.offPointer = slot(0x940000).readPointer().equals(objD);
callPost(4, 0x940000);
results.activeEnd = rpc.stats().active;
results.pendingEnd = rpc.stats().pending;

let extraOk = true;
if (extra) {
  rpc.mode(true, 3);
  const obj = putWide('Amount');
  const at = initSlot(0x950000, obj);
  callPre(5, 0x950000, extra.pre_rva);
  callPost(5, 0x950000);
  const pending = rpc.stats().pending === 1 && !at.readPointer().equals(obj);
  callPost(5, 0x950000, extra.ret_rva);
  extraOk = pending && at.readPointer().equals(obj) && rpc.stats().pending === 0;
}

results.ok = extraOk && results.probe_hooks === (extra ? 4 : 2) && results.object_hooks === 0
  && results.leave_traps === 0
  && results.pre_callback_type === 'function'
  && results.post_callback_type === 'function'
  && results.changed && results.active1 === 1 && results.pending1 === 1
  && results.aliveAfterMap === 2 && results.restored
  && results.beforeBPost.pending === 2
  && results.afterBPost.pending === 1 && results.afterBPost.active === 1
  && results.bSlot && results.aStillPending === 1 && results.aActive === 1
  && results.afterWrongRsp.pending === 1
  && results.afterALate.pending === 0
  && results.afterC.pending === 1 && results.afterCPost.pending === 0
  && results.cSlot && results.offPointer
  && results.activeEnd === 0 && results.pendingEnd === 0;
console.log(JSON.stringify(results));
if (!results.ok) process.exitCode = 1;
