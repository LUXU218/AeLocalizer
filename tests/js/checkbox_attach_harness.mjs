// Execute the production agent with a small synthetic loaded module.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const template = fs.readFileSync(process.argv[2], 'utf8');
const regular = {pre_rva: 0x100, call_rva: 0x110, ret_rva: 0x115,
  pre_bytes: '90', call_bytes: 'e800000000', ret_bytes: '90'};
const checkbox = {pre_rva: 0x250, call_rva: 0x260, ret_rva: 0x265,
  pre_bytes: '90', call_bytes: 'e89b000000', ret_bytes: '90', kind: 'checkbox',
  checkbox_scope: {entry_rva: 0x230, entry_bytes: '90', vtable_rva: 0x600,
    owners: [{rva: 0x400, bytes: '90'}],
    calls: [{rva: 0x420, bytes: 'e80bfeffff', owner_rva: 0x400}]}};

function load({onlyCheckbox = false, failAt, corruptAt} = {}) {
  const bytes = Buffer.alloc(0x700);
  function put(rva, hex) { Buffer.from(hex, 'hex').copy(bytes, rva); }
  for (const site of [regular, checkbox])
    for (const part of ['pre', 'call', 'ret'])
      put(site[part + '_rva'], site[part + '_bytes']);
  const scope = checkbox.checkbox_scope;
  put(scope.entry_rva, scope.entry_bytes);
  for (const row of [...scope.owners, ...scope.calls]) put(row.rva, row.bytes);
  if (corruptAt !== undefined) bytes[corruptAt] = 0xcc;
  const installed = new Set();
  let globalDetach = 0;
  let flushCount = 0;
  class Pointer {
    constructor(rva) { this.rva = rva; }
    add(n) { return new Pointer(this.rva + n); }
    equals(other) { return this.rva === other.rva; }
    toString() { return this.rva.toString(16); }
    readByteArray(n) { return bytes.subarray(this.rva, this.rva + n); }
    readS32() { return bytes.readInt32LE(this.rva); }
  }
  const lib = {base: new Pointer(0), path: 'AfterFXLib.dll',
    getExportByName: () => new Pointer(0x300)};
  const context = {
    Process: {getModuleByName: () => lib},
    Interceptor: {
      attach(address) {
        if (address.rva === failAt) throw Error('simulated attach failure');
        installed.add(address.rva);
        return {detach() { installed.delete(address.rva); }};
      },
      detachAll() { globalDetach++; installed.clear(); },
      flush() { flushCount++; },
    },
    rpc: {exports: {}}, send() {},
  };
  const src = template
    .replace('__MAP__', '{}')
    .replaceAll('__CHECKBOX_PROFILE__', JSON.stringify(onlyCheckbox
      ? {path: lib.path, draw_export: 'edb'} : null))
    .replaceAll('__EXTRA_SITES__', JSON.stringify([checkbox]))
    .replace('__TEXT__', 'null').replace('__SLOT__', '40')
    .replace('__PRE_RVA__', regular.pre_rva).replace('__CALL_RVA__', regular.call_rva)
    .replace('__RET_RVA__', regular.ret_rva)
    .replace('__PRE_BYTES__', JSON.stringify(regular.pre_bytes))
    .replace('__CALL_BYTES__', JSON.stringify(regular.call_bytes))
    .replace('__RET_HEX__', JSON.stringify(regular.ret_bytes));
  vm.runInNewContext(src, context);
  return {installed: [...installed], globalDetach, flushCount, rpc: context.rpc.exports};
}

const failedProbe = load({failAt: checkbox.ret_rva});
assert.equal(failedProbe.rpc.ready(), true);
assert.match(failedProbe.rpc.stats().checkbox_error, /simulated attach failure/);
assert.deepEqual(failedProbe.installed, [regular.pre_rva, regular.ret_rva]);
assert.equal(failedProbe.globalDetach, 0);
assert.equal(failedProbe.flushCount, 1);

const scopeMismatch = load({corruptAt: checkbox.checkbox_scope.entry_rva});
assert.equal(scopeMismatch.rpc.ready(), true);
assert.match(scopeMismatch.rpc.stats().checkbox_error, /caption helper changed/);
assert.deepEqual(scopeMismatch.installed, [regular.pre_rva, regular.ret_rva]);

const onlyFailed = load({onlyCheckbox: true, failAt: checkbox.ret_rva});
assert.equal(onlyFailed.rpc.ready(), false);
assert.match(onlyFailed.rpc.stats().checkbox_error, /simulated attach failure/);
assert.deepEqual(onlyFailed.installed, []);

const onlyReady = load({onlyCheckbox: true});
assert.equal(onlyReady.rpc.ready(), true);
assert.equal(onlyReady.rpc.stats().checkbox_error, '');
