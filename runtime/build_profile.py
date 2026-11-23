# -*- coding: utf-8 -*-
"""Offline, build-adaptive profiles for AfterFXLib.dll and flt.dll.

The managed runtime must work on every installed AE build (2021-2026 and
future compatible builds) without hardcoding one binary's SHA/RVA.  This
module reads the DLL from disk *only* (no Frida attach, no process memory,
no writes) and derives:

  * the EDB_DrawString parameter-label call site (PRE/CALL/RET RVAs and the
    exact instruction bytes used as the runtime integrity gate);
  * the popup parameter vtable RVA (MSVC RTTI class match).

The derivation is structural and fail-closed:

  * EDB_DrawString is resolved through the export table, and its mangled
    signature hash proves the 6th positional argument is
    ``const std::wstring&`` (Win64 stack slot [rsp+0x28]).
  * Every direct ``call EDB_DrawString`` candidate is scored against the
    2025.5.0.4 call-site neighbourhood (a code template, not an address).
    Close matches require explicit evidence of a topic-pane draw function.
    When both pane implementations are present their function bodies must
    also be verified as equivalent.
    Both require the XMM store that homes argument 5 at [rsp+0x20]
    (the same prelude that guarantees argument 6 is at [rsp+0x28]).
  * The popup vtable is found by class name in the MSVC RTTI type
    descriptors, then resolved to its vtable through the Complete Object
    Locator.  The 2025 build's derived value must equal the historically
    verified 0xCDC10 (self-check).

Anything we cannot prove raises BuildMismatch with a diagnostic string; the
runtime then refuses to write instead of guessing offsets.
"""
from __future__ import annotations

import difflib
import bisect
import hashlib
import os
import re
import struct

DRAW_EXPORT = b"EDB_DrawString"
# sha256 of the mangled export name:
# void EDB_DrawString(Drawbot*, ColorRGBA const&, FontInterface const*,
#                     float, float, std::wstring const&,
#                     TextAlignment, TextTruncation, float)
EDB_SIGNATURE_SHA256 = (
    "95bb916b50c14d4d653acc81e4f856b20c90557beb3cd5ef2e522607a34e896a")
# 6th positional argument (const std::wstring&) lives at [rsp+0x28] on Win64;
# argument 5 (float) is homed at [rsp+0x20] by the prelude we verify.
DRAW_ARG_SLOT = 0x28
DRAW_ARG5_SLOT = 0x20
# 2025.5.0.4 verified call-site neighbourhood used as a code template.
DRAW_ANCHOR = bytes.fromhex(
    "4c24640fb7442456662b44245298c5c857f6c5ca2af0488b03488d542468488bcbff90e8"
    "000000488bd8488bd7488d4c2450e8b474e4ff0fbf542452c5e057dbc5e22adac5fa1174"
    "2440c744243801000000c74424300000000048895c2428c5fa114424204c8bc7488d5424"
    "58488bcee8665ee4ff90488b9424800000004883fa077614488d145502000000488b4c24"
    "68ff154c16dc0090488b8c24880000004833cce8730181004c8d9c24a0000000498b5b20"
    "498b7328c4c1782873f0498be35fc3cccccccccccccccc48895c241848896c2420565741"
    "564881ecb0000000488b0554ca4a014833c448898424a0000000488bfa488bf132db488b"
    "02488bcaff500883c09483f8070f872e0100004898488d15a3c056ff8b8c82c440a90048"
    "03caffe14533c04533c9ba9f04000048")
ANCHOR_BEFORE = 0x70
ANCHOR_AFTER = 0xC0
MIN_SCORE = 0.70          # unique structural match against the template
MIN_MARGIN = 0.20         # runner-up must be clearly worse
MAX_PRE_GAP = 0x30        # prelude store must be close to the call

# vmovss/vmovd/movss stores of an XMM argument to [rsp+0x20].  All forms are
# full instructions; the matched bytes are the runtime integrity gate.
_XMM_REGS = (0x44, 0x4C, 0x54, 0x5C, 0x64, 0x6C, 0x74, 0x7C)
ARG5_STORE_PATTERNS = tuple(
    bytes([0xC5, 0xFA, 0x11, reg, 0x24, 0x20]) for reg in _XMM_REGS
) + tuple(
    bytes([0xF3, 0x0F, 0x11, reg, 0x24, 0x20]) for reg in _XMM_REGS
)

# MSVC RTTI class of the typed popup parameter wrapper.  This name is stable
# across the supported AE builds; the vtable address is derived per binary.
POPUP_CLASS = (
    ".?AV?$TDB_DynamicEnglishDisplayName@V?$TDB_FloatStream@"
    "UTDB_ParamPopupStreamTraits@@@@@@")
# Field of that class that stores the owning std::string pointer.  Verified
# on 18.2.0.37/22.6.0.64/23.0.0.59/24.1.0.78/25.5.0.4/26.5.0.89 by the
# live vtable + pointer + original-bytes checks; an unknown layout can only
# emit diagnostics because every write still re-checks these three facts.
POPUP_FIELD_OFFSET = 0x70
# Historical verification cross-check: on 25.5.0.4 the derived vtable must
# be this RVA, otherwise the RTTI resolver is not trustworthy.
KNOWN_POPUP_VT = {
    "02c93a0bd5937f0c77c162e2c7163eb2da910305e92d57812587731b08a29da2": 0xCDC10,
}

# MSVC RTTI class of the "effect parade" root object (TDB parameter tree).
# Its vtable lives in flt.dll; the parameter catalog needs this RVA to find
# parade roots, but several built-in adapter profiles legitimately left it 0.
# Derive it per binary instead of hardcoding one build's address.
PARADE_CLASS = ".?AVFLT_FCSeqSpec@@"
# Historical verification cross-check: on 25.5.0.4 the derived parade-root
# vtable must be this RVA (it also matches the host-verified local adapter
# profile), otherwise the RTTI resolver is not trustworthy.
KNOWN_PARADE_VT = {
    # 本机已被产品侧验证过 root_vtable 的两个构建，用作派生结果的独立自检。
    "4ef3515ced17639c582e2d304967c8df129508f9b1b9f78f337beef2033d904a": 0x978F8,  # AE2022 22.6.0.64
    "02c93a0bd5937f0c77c162e2c7163eb2da910305e92d57812587731b08a29da2": 0xC5A70,  # AE2025 25.5.0.4
    "9a1afe7700229cd0bc39dd37a146b1d3871fc84a8e6f991a7ef56390f21378d3": 0xBF110,  # AE2026 26.5.0.89（与原内置值一致）
}

# --- dvaui static-text channel (AE 2026 Effect Controls name labels) ------
# On 26.5.0.89 the parameter-name labels are dvaui UI_StaticText controls
# (AfterFXLib CECStreamV2::CreateNameControls writes the name through
# SetValue@UI_StaticText).  The paint sink is a stable dvaui export; the text
# is a std::wstring inside DrawStaticTextParameters whose offset is derived
# from the exported, trivially-shaped GetTextString getter:
#     lea rax,[rcx+disp32]; ret
# Historical values: 0xE0 on 2021/2022, 0x100 on 2023-2026.
DVAUI_DRAW_EXPORT = (
    b"?Draw@UI_StaticText@controls@dvaui@@SAXAEBVDrawStaticTextParameters@23@"
    b"PEAVDrawbot@drawbot@3@@Z")
DVAUI_GETTEXT_PREFIX = (
    b"?GetTextString@DrawStaticTextParameters@controls@dvaui@@")
TEXT_LEN_OFFSET = 0x10
TEXT_CAP_OFFSET = 0x18
TEXT_INLINE_CAPACITY = 7
KNOWN_TEXT_OFFSET = {
    "18.2.0.37": 0xE0, "22.6.0.64": 0xE0, "23.0.0.59": 0x100,
    "24.1.0.78": 0x100, "25.5.0.4": 0x100, "26.5.0.89": 0x100,
}
# Draw entry prologue stores RBX at [rax+0x10] (2021-2023) or [rax+0x18]
# (2024-2026): an independent structural fingerprint of the same function.
KNOWN_TEXT_STORE = {
    "18.2.0.37": 0x10, "22.6.0.64": 0x10, "23.0.0.59": 0x10,
    "24.1.0.78": 0x18, "25.5.0.4": 0x18, "26.5.0.89": 0x18,
}
# Only builds whose Effect Controls labels are known to be UI_StaticText
# (instead of the classic EDB_DrawString path) get the extra text hook.
TEXT_LABEL_VERSIONS = ("26.5.0.89",)

VERIFIED_DVAUI = {
    "85f374a85bb04d87f30974e626f51b0e3392cdc2921976a1a25d8eb3a8cb7816": "18.2.0.37",
    "b5f84491f3ac40e7a4dde7bb0d53023d7751af3758d7fb6657373621a64cbcb8": "22.6.0.64",
    "2feacf9acc558cedb2acdc25038ba583ba5077834a8e356d86fda2a7cf1e3141": "23.0.0.59",
    "73b072f4d49ddfcf4512e47fac7c09a282798a9a7f772b673e24e43a0852e929": "24.1.0.78",
    "4483c41bdf3afe4b9d0202d94a61cae5bf4f6e24630a7ef129eea5944dd376d2": "25.5.0.4",
    "7aca3697f789ff65152b13ec5392f05637656dd24d553b15484d68a539dedea8": "26.5.0.89",
}

VERIFIED_AFTERFXLIB = {
    "c9e7748d50ca95c7a0d796d81896783b330a611f66ef4859278c13584e40518b": "18.2.0.37",
    "39da9061545051634f0f1cc8a3917e353c1b6122c142838a947634640884de35": "22.6.0.64",
    "719445d3d82a5e108ba2c3cfe234ff19038e0f95e09c19897b5bfe8cef73c5e3": "23.0.0.59",
    "c3b7da8d8854338133e754cb06072b4377714a4856eded37e01ef5d3517f46b8": "24.1.0.78",
    "88b9f8cf15ac440c318369cb9319f58c6ad46fbed6e4466b9355da3cb2ec7972": "25.5.0.4",
    "e0b699f4d85df5e799d3aba0dfd2e79d5caee20f253b6413037aed253f17e790": "26.5.0.89",
}

VERIFIED_FLT = {
    "dc92cf02585508fb579916643adc607d0f963ecffd1aad04ba629ee96198ffda": "18.2.0.37",
    "4ef3515ced17639c582e2d304967c8df129508f9b1b9f78f337beef2033d904a": "22.6.0.64",
    "2a2370db244d70b223ced4bdb3e07ff43d2fdb57c02dc80aefa89baf08dd80eb": "23.0.0.59",
    "ad9c3d0595407e646224d2f17b8ba705b73c356720f5aa7a8ada6e1a14a743f9": "24.1.0.78",
    "02c93a0bd5937f0c77c162e2c7163eb2da910305e92d57812587731b08a29da2": "25.5.0.4",
    "9a1afe7700229cd0bc39dd37a146b1d3871fc84a8e6f991a7ef56390f21378d3": "26.5.0.89",
}


class BuildMismatch(RuntimeError):
    """The installed binary cannot be proven compatible; never write."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def known_version(kind, sha):
    if kind == "afterfxlib":
        table = VERIFIED_AFTERFXLIB
    elif kind == "dvaui":
        table = VERIFIED_DVAUI
    elif kind == "tdb":
        table = {"ef851c3ccbc43da4e0d4899a52baa05a7a71a3cef3a4fdb09abbb0263960ccb5": "26.5.0.89"}
    else:
        table = VERIFIED_FLT
    return table.get(str(sha or "").lower(), "")


def _require_verified(path, sha, kind, allow_unknown=False):
    """Recompute SHA and fail closed unless the build is in the verified table.

    The SHA is re-read from disk so a caller cannot pass a stale/mixed value
    (e.g. a 2021 flt SHA with a 2025 flt path) and have it become a label.
    Structural checks are not a licence to execute on an unknown build.
    """
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise BuildMismatch("cannot read %s: %s"
                            % (os.path.basename(path), exc))
    given = str(sha or "").lower()
    if given and given != actual:
        raise BuildMismatch(
            "sha256 mismatch for %s: passed %s actual %s"
            % (os.path.basename(path), given[:16], actual[:16]))
    version = known_version(kind, actual)
    if not version and not allow_unknown:
        raise BuildMismatch(
            "unverified AE build (%s sha256=%s); refusing to hook"
            % (os.path.basename(path), actual[:16]))
    return actual


class PEImage:
    """Minimal read-only PE32+ reader (no external dependency)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, "rb") as f:
                self.raw = f.read()
        except OSError as exc:
            raise BuildMismatch("cannot read %s: %s" % (path, exc))
        if len(self.raw) < 0x40 or self.raw[:2] != b"MZ":
            raise BuildMismatch("not a PE image: %s" % path)
        e = self._u32_at(0x3C)
        if self.raw[e:e + 4] != b"PE\x00\x00":
            raise BuildMismatch("bad PE header: %s" % path)
        coff = e + 4
        nsect = self._u16_at(coff + 2)
        optsz = self._u16_at(coff + 16)
        if optsz < 0x70:
            raise BuildMismatch("bad optional header: %s" % path)
        opt = coff + 20
        if self._u16_at(opt) != 0x20B:
            raise BuildMismatch("not PE32+ (x64): %s" % path)
        self.image_base = self._u64_at(opt + 24)
        self.size_of_image = self._u32_at(opt + 56)
        ndirs = self._u32_at(opt + 108)
        self.export_dir = (0, 0)
        self.exception_dir = (0, 0)
        if ndirs >= 1:
            self.export_dir = (self._u32_at(opt + 112),
                               self._u32_at(opt + 116))
        if ndirs >= 4 and optsz >= 144:
            self.exception_dir = (self._u32_at(opt + 136), self._u32_at(opt + 140))
        self.sections = []
        for i in range(nsect):
            s = opt + optsz + i * 40
            name = self.raw[s:s + 8].rstrip(b"\x00")
            vsize = self._u32_at(s + 8)
            vaddr = self._u32_at(s + 12)
            rawsz = self._u32_at(s + 16)
            rawptr = self._u32_at(s + 20)
            self.sections.append({"name": name, "vaddr": vaddr,
                                  "vsize": max(vsize, rawsz),
                                  "rawptr": rawptr, "rawsz": rawsz})

    # -- primitives ---------------------------------------------------
    def _u16_at(self, off):
        if off < 0 or off + 2 > len(self.raw):
            raise BuildMismatch("truncated PE")
        return struct.unpack_from("<H", self.raw, off)[0]

    def _u32_at(self, off):
        if off < 0 or off + 4 > len(self.raw):
            raise BuildMismatch("truncated PE")
        return struct.unpack_from("<I", self.raw, off)[0]

    def _u64_at(self, off):
        if off < 0 or off + 8 > len(self.raw):
            raise BuildMismatch("truncated PE")
        return struct.unpack_from("<Q", self.raw, off)[0]

    def _section(self, rva):
        for s in self.sections:
            if s["vaddr"] <= rva < s["vaddr"] + s["vsize"]:
                return s
        return None

    def raw_offset(self, rva):
        s = self._section(rva)
        if s is None:
            raise BuildMismatch("rva %#x outside sections" % rva)
        off = int(s["rawptr"]) + int(rva) - int(s["vaddr"])
        if off < 0 or off + 1 > len(self.raw):
            raise BuildMismatch("rva %#x outside file" % rva)
        return off

    def rva_of_raw(self, off):
        for s in self.sections:
            r = int(s["rawptr"])
            if r <= off < r + int(s["rawsz"]):
                return int(s["vaddr"]) + (off - r)
        raise BuildMismatch("file offset %#x outside sections" % off)

    def read_rva(self, rva, n):
        off = self.raw_offset(rva)
        if off + n > len(self.raw):
            raise BuildMismatch("rva %#x truncated" % rva)
        return self.raw[off:off + n]

    def u32_rva(self, rva):
        return struct.unpack("<I", self.read_rva(rva, 4))[0]

    def u64_rva(self, rva):
        return struct.unpack("<Q", self.read_rva(rva, 8))[0]

    def cstring_rva(self, rva, maxlen=600):
        off = self.raw_offset(rva)
        end = self.raw.find(b"\x00", off, off + maxlen)
        if end < 0:
            return None
        return self.raw[off:end]

    def in_image(self, va):
        return self.image_base <= va < self.image_base + self.size_of_image

    # -- directories --------------------------------------------------
    def exports(self):
        rva, size = self.export_dir
        if not rva or not size:
            return {}
        nfuncs = self.u32_rva(rva + 20)
        nnames = self.u32_rva(rva + 24)
        funcs = self.u32_rva(rva + 28)
        names = self.u32_rva(rva + 32)
        ords = self.u32_rva(rva + 36)
        if nnames > 65536 or nfuncs > 65536:
            raise BuildMismatch("unreasonable export table")
        out = {}
        for i in range(nnames):
            name_rva = self.u32_rva(names + 4 * i)
            name = self.cstring_rva(name_rva, 2048)
            if not name:
                continue
            o = struct.unpack("<H", self.read_rva(ords + 2 * i, 2))[0]
            if o >= nfuncs:
                continue
            out[name] = self.u32_rva(funcs + 4 * o)
        return out

    def text(self):
        for s in self.sections:
            if s["name"] == b".text" and int(s["rawsz"]):
                start = int(s["rawptr"])
                return (self.raw[start:start + int(s["rawsz"])],
                        int(s["vaddr"]))
        raise BuildMismatch("no .text section")

    def rdata_blocks(self):
        for s in self.sections:
            if s["name"] in (b".rdata", b".data") and int(s["rawsz"]):
                start = int(s["rawptr"])
                yield (self.raw[start:start + int(s["rawsz"])],
                       int(s["vaddr"]), start)


def _find_arg5_store(text, call_off):
    """Return (offset, pattern) of the closest arg-5 XMM store before call."""
    window_start = max(0, call_off - MAX_PRE_GAP)
    best = None
    for pat in ARG5_STORE_PATTERNS:
        k = text.rfind(pat, window_start, call_off)
        if k >= 0 and (best is None or k > best[0]):
            best = (k, pat)
    return best


_TOPIC_GETTERS = {
    b"?GetODataPtr@CTopicPane@@QEAAPEAUOutlineData@@XZ": "classic",
    b"?GetODataPtr@CTopicPaneV2@@QEAAPEAUOutlineData@@XZ": "v2",
}


def _draw_site(text, base, offset):
    found = _find_arg5_store(text, offset)
    if found is None:
        raise BuildMismatch("arg5 prelude store missing at call %#x" % (base + offset))
    pre, pattern = found
    if not len(pattern) <= offset - pre <= MAX_PRE_GAP or offset + 13 > len(text):
        raise BuildMismatch("invalid argument store or truncated draw callsite")
    return {
        "pre_rva": base + pre, "call_rva": base + offset, "ret_rva": base + offset + 5,
        "pre_bytes": pattern.hex(), "call_bytes": text[offset:offset + 5].hex(),
        "ret_bytes": text[offset + 5:offset + 13].hex(),
    }


def _verified_topic_panes(image, exports, sites):
    """Select matching paint functions by their known pane and drawing calls."""
    try:
        from capstone import CS_ARCH_X86, CS_GRP_CALL, CS_GRP_JUMP, CS_MODE_64, Cs
        from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP, X86_REG_RSP
    except ImportError as error:
        raise BuildMismatch("topic-pane verification requires Capstone") from error

    directory, size = image.exception_dir
    if not directory or not 12 <= size <= 4 * 1024 * 1024 or size % 12:
        raise BuildMismatch("no valid function table for topic-pane verification")
    functions = list(struct.iter_unpack("<III", image.read_rva(directory, size)))
    getters = {exports[name]: tag for name, tag in _TOPIC_GETTERS.items() if name in exports}
    drawing_calls = (b"?EDB_GetFontFromStyleSheet@@", b"?EDB_GetBaselineForCenteredText@@")
    by_address = {rva: name for name, rva in exports.items() if name.startswith(drawing_calls)}
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    verified = []
    shapes = []
    owners = []
    for site in sites:
        call = site["call_rva"]
        index = bisect.bisect_right(functions, call, key=lambda item: item[0]) - 1
        if index < 0:
            raise BuildMismatch("draw call outside function table")
        start, end, _ = functions[index]
        if not start <= site["pre_rva"] < call < end or end - start > 2048:
            raise BuildMismatch("draw call outside a bounded topic-pane function")
        raw = image.read_rva(start, end - start)
        normalized = bytearray(raw)
        targets = []
        pane_getters = []
        helpers = []
        cursor = start
        instructions = set()
        stable_stack = True
        for ins in decoder.disasm(raw, start):
            instructions.add(ins.address)
            pos = ins.address - start
            cursor = ins.address + ins.size
            if site["pre_rva"] <= ins.address < call:
                stable_stack &= not (ins.group(CS_GRP_CALL) or ins.group(CS_GRP_JUMP)
                                      or X86_REG_RSP in ins.regs_access()[1])
            if ins.group(CS_GRP_CALL) and ins.operands[0].type == X86_OP_IMM:
                target = ins.operands[0].imm
                if ins.address < site["pre_rva"] and target in by_address:
                    helpers.append(by_address[target].split(b"@@", 1)[0] + b"@@")
                if target in getters:
                    pane_getters.append(getters[target])
                    target = "pane-getter"
                targets.append((pos, target))
                size = ins.imm_size
                operand = pos + ins.imm_offset
                normalized[operand:operand + size] = bytes(size)
            elif ins.group(CS_GRP_JUMP) and ins.operands[0].type == X86_OP_IMM:
                if not start <= ins.operands[0].imm < end:
                    raise BuildMismatch("topic-pane branch leaves the verified function")
            for op in ins.operands:
                if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                    targets.append((pos, cursor + op.mem.disp))
                    size = ins.disp_size
                    operand = pos + ins.disp_offset
                    normalized[operand:operand + size] = bytes(size)
        if not pane_getters:
            continue
        if (cursor != end or len(pane_getters) != 1 or tuple(helpers) != drawing_calls
                or not stable_stack or raw[site["pre_rva"] - start - 5:site["pre_rva"] - start] != b"\x48\x89\x5c\x24\x28"
                or not {site["pre_rva"], call, site["ret_rva"]} <= instructions):
            raise BuildMismatch("topic-pane evidence or draw instruction boundary missing")
        verified.append(site)
        owners.append(pane_getters[0])
        shapes.append((normalized, targets, call - start, site["pre_rva"] - start))
    if not verified:
        raise BuildMismatch("no candidate calls a verified topic-pane getter")
    if len(verified) > 1 and (len(verified) != 2 or set(owners) != {"classic", "v2"} or shapes[0] != shapes[1]):
        raise BuildMismatch("topic-pane function bodies disagree")
    return verified


def draw_candidate(path, sha=None, allow_unknown=False):
    """Derive and verify the EDB_DrawString parameter-label call site.

    Returns a dict with pre/call/ret RVAs and integrity bytes.  Raises
    BuildMismatch when the binary cannot be proven to match the template.
    """
    sha = _require_verified(path, sha, "afterfxlib", allow_unknown)
    img = PEImage(path)
    exports = img.exports()
    names = [n for n in exports if DRAW_EXPORT in n]
    if len(names) != 1:
        raise BuildMismatch("EDB_DrawString export count=%d" % len(names))
    sig = names[0]
    sig_sha = hashlib.sha256(sig).hexdigest()
    if sig_sha != EDB_SIGNATURE_SHA256:
        raise BuildMismatch("EDB_DrawString signature changed (%s)" % sig_sha[:16])
    target = int(exports[sig])
    text, text_rva = img.text()
    if not text:
        raise BuildMismatch("empty .text")
    scored = []
    pos = text.find(b"\xe8")
    while pos >= 0:
        if pos + 5 <= len(text):
            rel = struct.unpack_from("<i", text, pos + 1)[0]
            if text_rva + pos + 5 + rel == target:
                if (pos >= ANCHOR_BEFORE
                        and pos + ANCHOR_AFTER <= len(text)):
                    win = text[pos - ANCHOR_BEFORE:pos + ANCHOR_AFTER]
                    score = difflib.SequenceMatcher(
                        None, DRAW_ANCHOR, win, autojunk=False).ratio()
                    scored.append((score, pos))
        pos = text.find(b"\xe8", pos + 1)
    if not scored:
        raise BuildMismatch("no direct call EDB_DrawString")
    scored.sort(reverse=True)
    top_score, top_off = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    if top_score < MIN_SCORE:
        raise BuildMismatch("draw callsite not recognized (score=%.3f)" % top_score)
    site = _draw_site(text, text_rva, top_off)
    additional_sites = []
    draw_family = "single"
    if top_score - runner < MIN_MARGIN:
        ambiguous = "draw callsite ambiguous (%.3f vs %.3f)" % (top_score, runner)
        close = [off for score, off in scored if score >= MIN_SCORE and top_score - score < MIN_MARGIN]
        if len(close) not in (2, 3, 4):
            raise BuildMismatch(ambiguous)
        try:
            sites = [_draw_site(text, text_rva, off) for off in close]
            site, *additional_sites = _verified_topic_panes(img, exports, sites)
        except BuildMismatch as error:
            raise BuildMismatch(f"{ambiguous}: {error}") from error
        draw_family = "topic-pane-pair" if additional_sites else "topic-pane-single"
    return {
        "kind": "afterfxlib",
        "path": os.path.abspath(path),
        "sha256": sha,
        "known_version": known_version("afterfxlib", sha),
        "verified_build": bool(known_version("afterfxlib", sha)),
        "signature_sha256": sig_sha,
        **site,
        "additional_sites": additional_sites,
        "draw_family": draw_family,
        "arg_slot": DRAW_ARG_SLOT,
        "arg5_slot": DRAW_ARG5_SLOT,
        "confidence": round(top_score, 3),
        "runner_up": round(runner, 3),
        "callsites": len(scored),
    }


def text_draw_profile(path, sha=None, allow_unknown=False):
    """Derive the dvaui static-text paint sink used by 2026+ name labels.

    Returns a dict describing the exported ``UI_StaticText::Draw`` entry and
    the std::wstring offset inside ``DrawStaticTextParameters``.  The offset
    is read from the exported ``GetTextString`` getter (``lea rax,[rcx+d]``
    followed by ``ret``), so it is structural rather than hardcoded; a known
    build additionally must match the historical value (self-check).
    """
    sha = _require_verified(path, sha, "dvaui", allow_unknown)
    version = known_version("dvaui", sha)
    img = PEImage(path)
    exports = img.exports()
    source_draw = b"?UI_Draw@UI_StaticText@controls@dvaui@@MEBAXPEAVDrawbot@drawbot@3@@Z"
    if source_draw not in exports:
        raise BuildMismatch("UI_StaticText source draw export missing")
    draw = [n for n in exports if n == DVAUI_DRAW_EXPORT]
    if len(draw) != 1:
        raise BuildMismatch("UI_StaticText::Draw export count=%d" % len(draw))
    getters = [n for n in exports if n.startswith(DVAUI_GETTEXT_PREFIX)]
    if len(getters) != 1:
        raise BuildMismatch("GetTextString export count=%d" % len(getters))
    text, text_rva = img.text()
    if not text:
        raise BuildMismatch("empty .text")

    def _rel(rva):
        pos = int(rva) - int(text_rva)
        if pos < 0 or pos >= len(text):
            raise BuildMismatch("export rva %#x outside .text" % rva)
        return pos

    goff = _rel(exports[getters[0]])
    pro = text[goff:goff + 8]
    if pro[:3] == b"\x48\x8d\x81" and len(pro) >= 8 and pro[7] == 0xC3:
        disp = struct.unpack_from("<i", pro, 3)[0]
    elif pro[:3] == b"\x48\x8d\x41" and len(pro) >= 5 and pro[4] == 0xC3:
        disp = struct.unpack_from("<b", pro, 3)[0]
    else:
        raise BuildMismatch("GetTextString prologue unexpected: %s" % pro.hex())
    if disp <= 0 or disp % 8:
        raise BuildMismatch("text offset %#x is implausible" % disp)

    doff = _rel(exports[draw[0]])
    dpro = text[doff:doff + 0x18]
    if dpro[:3] != b"\x48\x8b\xc4":          # mov rax, rsp
        raise BuildMismatch("UI_StaticText::Draw prologue unexpected")
    stores = [b"\x48\x89\x58\x10", b"\x48\x89\x58\x18"]
    if version:
        expected = KNOWN_TEXT_OFFSET.get(version)
        expected_store = KNOWN_TEXT_STORE.get(version)
        if expected is None or expected_store is None:
            raise BuildMismatch("no known text layout for %s" % version)
        if disp != expected:
            raise BuildMismatch("text offset %#x != expected %#x for %s"
                                % (disp, expected, version))
        stores = [b"\x48\x89\x58" + bytes([expected_store])]
    if not any(s in dpro for s in stores):
        raise BuildMismatch("UI_StaticText::Draw prologue store missing")
    return {
        "kind": "dvaui_text",
        "path": os.path.abspath(path),
        "sha256": sha,
        "known_version": version,
        "verified_build": bool(version),
        "module": "dvaui.dll",
        "draw_export": DVAUI_DRAW_EXPORT.decode("ascii"),
        "gettext_export": getters[0].decode("ascii"),
        "source_draw_export": source_draw.decode("ascii"),
        "source_draw_bytes": text[_rel(exports[source_draw]):_rel(exports[source_draw]) + 12].hex(),
        "source_class": ".?AVCECStaticTextElement@internal@ecp@@",
        "text_offset": disp,
        "len_offset": TEXT_LEN_OFFSET,
        "cap_offset": TEXT_CAP_OFFSET,
        "inline_capacity": TEXT_INLINE_CAPACITY,
    }



def popup_draw_profile(path, sha=None, allow_unknown=False, *, scoped=False):
    """Verified paint-time string of the current dropdown selection.

    AE caches UI_Popup text separately from its typed TDB definition. Derive
    the temporary DrawPopupParameters wstring from its exported getter; never
    guess a UI object layout or change a parameter value/selection.
    """
    sha = _require_verified(path, sha, "dvaui", allow_unknown)
    version = known_version("dvaui", sha)
    img = PEImage(path)
    exports = img.exports()
    getters = [n for n in exports if n.startswith(
        b"?GetString@DrawPopupParameters@controls@dvaui@@QEBAAEBV?$basic_string@_W")]
    draws = [
        b"?Draw@UI_Popup@controls@dvaui@@SAXAEBVDrawPopupParameters@23@PEAVDrawbot@drawbot@3@@Z",
        b"?Draw@UI_PopupButton@controls@dvaui@@SAXAEBVDrawPopupParameters@23@PEAVDrawbot@drawbot@3@@Z",
    ]
    if len(getters) != 1 or any(n not in exports for n in draws):
        raise BuildMismatch("popup paint exports unavailable or ambiguous")
    text, base = img.text()
    def code_at(name, size):
        pos = int(exports[name]) - int(base)
        if pos < 0 or pos + size > len(text):
            raise BuildMismatch("popup paint export outside .text")
        return text[pos:pos + size]
    pro = code_at(getters[0], 8)
    if pro[:3] != b"\x48\x8d\x81" or pro[7] != 0xc3:
        raise BuildMismatch("popup GetString getter shape changed")
    offset = struct.unpack_from("<i", pro, 3)[0]
    known = {"18.2.0.37": 0x158, "22.6.0.64": 0x1a0,
             "23.0.0.59": 0x1c0, "24.1.0.78": 0x1c0,
             "25.5.0.4": 0x178, "26.5.0.89": 0x180}
    if not 0 < offset <= 0x1000 or offset % 8 or (version and known.get(version) != offset):
        raise BuildMismatch("popup paint wstring offset mismatch")
    profile = {"kind": "dvaui_popup_paint", "module": "dvaui.dll",
            "path": os.path.abspath(path), "sha256": sha,
            "known_version": version, "verified_build": bool(version),
            "text_offset": offset, "len_offset": TEXT_LEN_OFFSET,
            "cap_offset": TEXT_CAP_OFFSET, "inline_capacity": TEXT_INLINE_CAPACITY,
            "draw_exports": [n.decode("ascii") for n in draws],
            "draw_bytes": {n.decode("ascii"): code_at(n, 12).hex() for n in draws},
            "gettext_export": getters[0].decode("ascii")}
    if scoped:
        sources = []
        for name, draw in zip((b"UI_Popup", b"UI_PopupButton"), draws):
            prefix = b"?UI_Draw@" + name + b"@"
            matches = [n for n in exports if n.startswith(prefix)]
            if len(matches) != 1:
                raise BuildMismatch("classic popup paint source unavailable or ambiguous")
            source = matches[0]
            body = _popup_function(img, exports[source])
            call = _popup_call_index(body, exports[draw])
            after = body[call].address + body[call].size - exports[source]
            sources.append({"name": source.decode("ascii"), "bytes": code_at(source, 12).hex(),
                            "return_offset": after})
        profile.update(supplemental=True, paint_sources=sources)
    return profile


def _rtti_vt_candidates(img, class_name):
    """Return sorted unique MSVC RTTI vtable RVAs for a class name."""
    needle = class_name.encode("ascii")
    hits = []
    start = 0
    while True:
        p = img.raw.find(needle, start)
        if p < 0:
            break
        start = p + 1
        try:
            name_rva = img.rva_of_raw(p)
        except BuildMismatch:
            continue
        td_rva = name_rva - 16
        if td_rva < 0:
            continue
        # TypeDescriptor layout: vftable(8) + spare(8) + name[].  The spare
        # dword must read as zero; this is a real bounds/consistency check.
        try:
            if img.u32_rva(td_rva + 12) != 0:
                continue
        except BuildMismatch:
            continue
        hits.append(td_rva)
    if not hits:
        return []
    text_rva, text_len = 0, 0
    try:
        text, text_rva = img.text()
        text_len = len(text)
    except BuildMismatch:
        pass
    code_range = (text_rva, text_rva + text_len)
    vt_candidates = []
    for td_rva in sorted(set(hits)):
        pat = struct.pack("<I", int(td_rva))
        for data, va, _raw_start in img.rdata_blocks():
            off = 0
            while True:
                q = data.find(pat, off)
                if q < 0:
                    break
                off = q + 1
                col_rva = (va + q) - 12
                if col_rva <= 0:
                    continue
                try:
                    if img.u32_rva(col_rva) != 1:            # COL signature
                        continue
                    if img.u32_rva(col_rva + 4) != 0:        # primary offset
                        continue
                    if img.u32_rva(col_rva + 8) != 0:        # primary cdOffset
                        continue
                except BuildMismatch:
                    continue
                col_va = img.image_base + col_rva
                slot = struct.pack("<Q", col_va)
                for data2, va2, _r2 in img.rdata_blocks():
                    o2 = data2.find(slot)
                    while o2 >= 0:
                        vt_rva = (va2 + o2 + 8)
                        # The first vtable entry must point into .text.
                        try:
                            first = img.u64_rva(vt_rva) - img.image_base
                            lo, hi = code_range
                            if lo <= first < hi:
                                vt_candidates.append(int(vt_rva))
                        except BuildMismatch:
                            pass
                        o2 = data2.find(slot, o2 + 1)
    return sorted(set(vt_candidates))


def _resolved_vtable(path, sha, class_name, expected_map, label,
                     allow_unknown=False):
    """Verify the binary and resolve one RTTI vtable, failing closed."""
    sha = _require_verified(path, sha, "flt", allow_unknown)
    vt_candidates = _rtti_vt_candidates(PEImage(path), class_name)
    if not vt_candidates:
        raise BuildMismatch("%s vtable not resolved (flt)" % label)
    expected = expected_map.get(sha)
    if expected is not None:
        if vt_candidates != [int(expected)]:
            raise BuildMismatch(
                "%s vtable self-check failed: %s != %#x"
                % (label, [hex(v) for v in vt_candidates], expected))
    elif len(vt_candidates) > 1:
        raise BuildMismatch("%s vtable ambiguous: %s"
                            % (label, [hex(v) for v in vt_candidates]))
    return sha, vt_candidates[0]


def popup_profile(path, sha=None, allow_unknown=False):
    """Derive the popup parameter vtable RVA through MSVC RTTI.

    The class name is matched in the type-descriptor strings, then the
    Complete Object Locator is found by its type-descriptor RVA and finally
    the vtable by its COL pointer slot.  Exactly one primary (offset 0)
    candidate must exist.
    """
    sha, vt_rva = _resolved_vtable(
        path, sha, POPUP_CLASS, KNOWN_POPUP_VT, "popup", allow_unknown)
    return {
        "kind": "flt",
        "path": os.path.abspath(path),
        "sha256": sha,
        "known_version": known_version("flt", sha),
        "verified_build": bool(known_version("flt", sha)),
        "vt_rva": vt_rva,
        "class_name": POPUP_CLASS,
        "field_offset": POPUP_FIELD_OFFSET,
    }


def parade_vtable(path, sha=None, allow_unknown=False):
    """Derive the effect-parade root vtable RVA through MSVC RTTI.

    Older built-in adapter profiles predate this derivation and leave
    ``root_vtable`` as 0; the parameter catalog needs the real address to
    enumerate parades, so derive it from the exact flt.dll on disk.
    """
    sha, vt_rva = _resolved_vtable(
        path, sha, PARADE_CLASS, KNOWN_PARADE_VT, "parade", allow_unknown)
    return {
        "kind": "flt",
        "path": os.path.abspath(path),
        "sha256": sha,
        "known_version": known_version("flt", sha),
        "verified_build": bool(known_version("flt", sha)),
        "vt_rva": vt_rva,
        "class_name": PARADE_CLASS,
    }


def _popup_compare_offset(code, param_type, pointer_use):
    # Extra compiler form of the same 0x8090 type mask: cases 4, 7 and 15
    # all reach the field computation. The first two branches must land
    # exactly at the MOV, and the rejected case must skip the pointer use.
    pattern = (param_type + rb"\x83\xf8\x04\x74\x0e\x83\xf8\x07\x74\x09"
               rb"\x83\xf8\x0f\x0f\x85(?P<skip>....)\xb8(?P<offset>....)" + pointer_use)
    matches = list(re.finditer(pattern, code, re.S))
    if len(matches) != 1:
        return None
    match = matches[0]
    rejected = match.end("skip") + struct.unpack("<i", match["skip"])[0]
    if rejected < match.end():
        return None
    return struct.unpack("<I", match["offset"])[0]


def popup_field_layout(path, sha=None):
    """Verify the classic TDB popup string layout through SetParamDef.

    The exported wrapper passes the stream's embedded PF_ParamDef to the
    copier. Follow its calls to the type-checked string reader and writer,
    which both must address the same member. Relative branches may move.
    """
    _require_verified(path, sha, "tdb", allow_unknown=True)
    image = PEImage(path)
    exports = image.exports()
    name = b"?SetParamDef@TDB_StreamBase@@QEAAXAEBUPF_ParamDef@@@Z"
    entry = exports.get(name)
    code, base = image.text()

    def span(rva, length):
        if rva is None or not base <= rva <= base + len(code) - length:
            raise BuildMismatch("popup definition function outside TDB code")
        return code[rva - base:rva - base + length]

    def branch(rva, opcode):
        raw = span(rva, 5)
        if raw[0] != opcode:
            raise BuildMismatch("popup definition branch changed")
        return rva + 5 + struct.unpack_from("<i", raw, 1)[0]

    wrapper = span(entry, 15)
    if not re.fullmatch(rb"\x48\x8b\xc2\x48\x8d\x51(.)\x48\x8b\xc8\xe9....",
                        wrapper, re.S):
        raise BuildMismatch("popup definition wrapper changed")
    copy = branch(entry + 10, 0xE9)
    call_pattern = (rb"\x48\x8b\xd7\x48\x8d\x4c\x24.\xe8....\x90"
                    rb"\x45\x33\xc0\x48\x8b\xd0\x48\x8b\xcb\xe8....")
    calls = list(re.finditer(call_pattern, span(copy, 240), re.S))
    if len(calls) != 1:
        raise BuildMismatch("popup string copy calls changed")
    reader = branch(copy + calls[0].start() + 8, 0xE8)
    writer = branch(copy + calls[0].start() + 23, 0xE8)
    # PF_Param_POPUP participates in the typed string mask (0x8090).
    # Both paths independently check the type and dereference the string.
    read_code = span(reader, 160)
    write_code = span(writer, 100)
    read = re.search(rb"\x8b\x42\x0c\x83\xf8\x0f\x0f\x87....\xb9\x90\x80\x00\x00"
                     rb"\x0f\xa3\xc1\x0f\x83....\x48\x8d\x42(.)"
                     rb"\x48\x85\xc0\x0f\x84....\x48\x8b\x10", read_code, re.S)
    write = re.search(rb"\x8b\x41\x0c\x83\xf8\x0f\x0f\x87....\xba\x90\x80\x00\x00"
                      rb"\x0f\xa3\xc2\x0f\x83....\x48\x8d\x59(.)"
                      rb"\x48\x85\xdb\x0f\x84....\x48\x8b\x0b", write_code, re.S)
    if read and write:
        read_offset, write_offset = read[1][0], write[1][0]
    else:
        read_offset = _popup_compare_offset(
            read_code, rb"\x8b\x42\x0c",
            rb"(?:\x48\x8d\x0c\x02\x48\x85\xc9\x74.\x48\x8b\x11"
            rb"|\x4c\x8d\x34\x02\x4c\x89\x74\x24.\x4d\x85\xf6\x0f\x84....\x49\x83\x3e\x00)")
        write_offset = _popup_compare_offset(
            write_code, rb"\x8b\x41\x0c",
            rb"\x48\x8d\x1c[\x01\x08]\x48\x85\xdb\x0f\x84....\x48\x8b\x0b")
    if (read_offset is None or read_offset != write_offset
            or wrapper[6] + read_offset != POPUP_FIELD_OFFSET):
        raise BuildMismatch("popup option pointer layout changed")
    return POPUP_FIELD_OFFSET


def popup_activity_profile(path):
    """Read-only notification when a typed popup definition is requested."""
    image = PEImage(path)
    exports = image.exports()
    parent = exports.get(b"?GetParent@TDB_StreamBase@@QEBAPEBVTDB_StreamGroup@@XZ")
    if not parent or image.read_rva(parent, 5) != bytes.fromhex("488b4120c3"):
        raise BuildMismatch("popup activity parent layout changed")
    text, base = image.text()
    getters = {}
    for name in (b"?GetParamDef@TDB_StreamBase@@QEBAPEBUPF_ParamDef@@XZ",
                 b"?GetParamDef@TDB_StreamBase@@QEAAPEAUPF_ParamDef@@XZ"):
        addr = exports.get(name)
        if addr is not None and base <= addr <= base + len(text) - 12:
            getters[name.decode("ascii")] = text[addr-base:addr-base+12].hex()
    if not getters:
        raise BuildMismatch("popup activity getter missing")
    return {"path": os.path.abspath(path), "getters": getters}


def _popup_export(exports, prefixes):
    matches = [(name, rva) for name, rva in exports.items() if name.startswith(prefixes)]
    if len(matches) != 1:
        raise BuildMismatch("popup display export unavailable or ambiguous")
    return matches[0]


def _popup_function(image, start):
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64

    directory, size = image.exception_dir
    code, base = image.text()
    for first, end, _ in struct.iter_unpack("<III", image.read_rva(directory, size)):
        if first == start and base <= first < end <= base + len(code):
            decoder = Cs(CS_ARCH_X86, CS_MODE_64)
            return list(decoder.disasm(code[first-base:end-base], first))
    raise BuildMismatch("popup function boundary unavailable")


def _popup_call_index(body, target):
    calls = [i for i, op in enumerate(body) if op.mnemonic == "call" and op.op_str == hex(target)]
    if len(calls) != 1:
        raise BuildMismatch("popup display callsite unavailable or ambiguous")
    return calls[0]


def popup_label_profile(path):
    """Locate the CLayer effect-menu label conversion from the actual host code."""
    image = PEImage(path)
    exports = image.exports()
    scope_name, scope = _popup_export(exports, (
        b"?RebuildPopupParamMenu@CLayer@@QEAAXAEBVTDB_StreamIDPath@@_NAEAVUI_MenuSpec@@",
        b"?RebuildPopupParamMenu@CLayer@@QEBA?AVUI_MenuSpec@@AEBVTDB_StreamIDPath@@",
    ))
    path_arg = 1 if b"@@QEAAX" in scope_name else 2
    _, leaf = _popup_export(exports, (
        b"?MakePopupData@CLayer@@AEBA?AVUI_MenuSpec@@AEBUPF_ParamDef@@",
        b"?GetPopupData@CLayer@@AEAAXAEBUPF_ParamDef@@AEAVUI_MenuSpec@@",
    ))
    _, getter = _popup_export(exports, b"?GetPopupItemString@CLayer@@")
    code = _popup_function(image, leaf)
    calls = []
    label_local = ""
    for index, instruction in enumerate(code):
        if instruction.mnemonic != "call":
            continue
        if instruction.op_str == hex(getter):
            label_local = ""
            for m in range(max(1, index - 12), index):
                store = code[m]
                load = code[m - 1]
                if store.mnemonic == "mov" and store.op_str.startswith("qword ptr [rsp + 0x20], "):
                    register = store.op_str.rsplit(", ", 1)[1]
                    if load.mnemonic == "lea" and load.op_str.startswith(register + ", ["):
                        label_local = load.op_str.split(", ", 1)[1]
            continue
        if label_local and instruction.bytes[:2] == b"\xff\x15":
            args = code[index-2:index]
            if (len(args) == 2 and args[0].mnemonic == args[1].mnemonic == "lea"
                    and args[0].op_str == "rdx, " + label_local and args[1].op_str.startswith("rcx, [")):
                calls.append(instruction)
            label_local = ""
    if len(calls) != 1:
        raise BuildMismatch("popup label conversion unavailable or ambiguous")
    call = calls[0]
    after = call.address + call.size
    tdb = PEImage(os.path.join(os.path.dirname(os.path.abspath(path)), "TDB.dll"))
    tdb_exports = tdb.exports()
    helpers = {}
    for key, prefix in (("length", b"?GetLength@TDB_StreamIDPath@@QEBAIXZ"),
                        ("component", b"?GetComponent@TDB_StreamIDPath@@QEBAAEBVTDB_StreamID@@I@Z"),
                        ("match", b"?GetMatchName@TDB_StreamID@@QEBAAEBVTDB_MatchName@@XZ"),
                        ("name", b"?GetName@TDB_MatchName@@QEBAPEBEXZ")):
        name, rva = _popup_export(tdb_exports, prefix)
        helpers[key] = {"name": name.decode("ascii"), "bytes": tdb.read_rva(rva, 12).hex()}
    return {"scope_rva": scope, "scope_bytes": image.read_rva(scope, 12).hex(),
            "path_arg": path_arg, "helpers": helpers,
            "call_rva": call.address, "call_bytes": call.bytes.hex(),
            "ret_rva": after, "ret_bytes": image.read_rva(after, 8).hex()}


def media_popup_field_layout(tdb_path, tdb_sha, media_path, media_sha):
    """Verify a TDB wrapper that delegates the typed option storage to MediaFoundation."""
    import pefile
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    _require_verified(tdb_path, tdb_sha, "tdb", allow_unknown=True)
    _require_verified(media_path, media_sha, "mediafoundation", allow_unknown=True)
    tdb = PEImage(tdb_path)
    media = PEImage(media_path)
    wrapper_export = b"?SetParamDef@TDB_StreamBase@@QEAAXAEBUPF_ParamDef@@@Z"
    copy_export = b"?PF_ParamDefStr_Copy@PF_ParamDefStr@@QEBAXAEBUPF_ParamDef@@PEAU2@@Z"
    entry = tdb.exports().get(wrapper_export)
    if entry is None:
        raise BuildMismatch("TDB parameter definition wrapper missing")
    wrapper = tdb.read_rva(entry, 48)
    found = re.fullmatch(
        rb"\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x20\x48\x8b\xfa\x48\x8b\xd9"
        rb"\xe8....\x4c\x8d\x43(.)\x48\x8b\xd7\x48\x8b\xc8\x48\x8b\x5c\x24\x30"
        rb"\x48\x83\xc4\x20\x5f\x48\xff\x25(....)", wrapper, re.S)
    if not found:
        raise BuildMismatch("TDB MediaFoundation popup wrapper changed")
    import_slot = entry + len(wrapper) + struct.unpack("<i", found[2])[0]
    try:
        with pefile.PE(data=tdb.raw, fast_load=True) as parsed:
            parsed.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            imports = [(dll.dll.lower(), fn.name) for dll in getattr(parsed, "DIRECTORY_ENTRY_IMPORT", ())
                       for fn in dll.imports if fn.address - parsed.OPTIONAL_HEADER.ImageBase == import_slot]
    except pefile.PEFormatError as error:
        raise BuildMismatch("invalid TDB import table") from error
    if imports != [(b"mediafoundation.dll", copy_export)]:
        raise BuildMismatch("TDB popup copy does not resolve to MediaFoundation")

    exports = media.exports()
    code, base = media.text()

    def entry_by_prefix(prefix):
        matches = [rva for name, rva in exports.items() if name.startswith(prefix)]
        if len(matches) != 1 or not base <= matches[0] < base + len(code):
            raise BuildMismatch("MediaFoundation popup accessor missing or ambiguous")
        return matches[0]

    getter = entry_by_prefix(b"?PF_ParamDefStr_GetParamValueLabel@PF_ParamDefStr@@")
    setter = entry_by_prefix(b"?PF_ParamDefStr_SetParamValueLabel@PF_ParamDefStr@@")
    copier = entry_by_prefix(copy_export)
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    calls = []
    for ins in decoder.disasm(code[copier - base:copier - base + 480], copier):
        if ins.mnemonic == "ret":
            break
        if ins.bytes[0] == 0xE8 and ins.size == 5:
            target = ins.address + 5 + struct.unpack("<i", ins.bytes[1:])[0]
            if target in (getter, setter):
                calls.append(target)
    if calls != [getter, setter]:
        raise BuildMismatch("MediaFoundation popup copy calls changed")

    read_offset = _popup_compare_offset(
        code[getter - base:getter - base + 170], rb"\x41\x8b\x40\x0c",
        rb"\x49\x8d\x14\x00\x48\x85\xd2\x0f\x84....\x4c\x8b\x02")
    write_code = code[setter - base:setter - base + 700]
    writes = list(re.finditer(
        rb"\x8b\x42\x0c\x83\xf8\x04\x74\x0a\x83\xf8\x07\x74\x05"
        rb"\x83\xf8\x0f\x75\x04\x4c\x8d\x6a(.)\x4d\x85\xed\x0f\x84....", write_code, re.S))
    if (read_offset is None or len(writes) != 1 or read_offset != writes[0][1][0]
            or not re.search(rb"\x49\x89\x45\x00", write_code[writes[0].end():])
            or found[1][0] + read_offset != POPUP_FIELD_OFFSET):
        raise BuildMismatch("MediaFoundation popup option pointer layout changed")
    return POPUP_FIELD_OFFSET


def popup_menu_profile(path):
    """Verified scoped menu-label construction, without translating metadata."""
    folder = os.path.dirname(os.path.abspath(path))
    images = {"dvaui": PEImage(path), "afterfxlib": PEImage(os.path.join(folder, "AfterFXLib.dll")),
              "tdb": PEImage(os.path.join(folder, "TDB.dll"))}
    exports = {k: v.exports() for k, v in images.items()}
    specs = {
        "osbuilder": ("dvaui", "?DoPopupMenu@UI_Popup@controls@dvaui@@MEAAX"),
        "custom": ("dvaui", "?DoCustomDrawnFlyoutMenu@UI_Popup@"),
        "flyoutitem": ("dvaui", "?CreateFlyoutMenuItem@UI_Popup@"),
        "flyoutctor": ("dvaui", "??0BasicMenuItem@controls@dvaui@@QEAA@HAEBV?$basic_string@E"),
        "osappend": ("dvaui", "?AppendItem@OS_Menu@utility@dvaui@@UEAAHAEBV?$basic_string@_WU?$char_traits@_W@std@@U?$STLAllocator@_W@allocator@dvacore@@@std@@V?$IntrusivePtr"),
        "items": ("dvaui", "?GetItems@UI_Popup@"),
        "parent": ("dvaui", "?UI_GetParentNode@UI_Node@ui@dvaui@@QEBAPEBV"),
        "path": ("afterfxlib", "?GetStreamPath@CECStreamV2@internal@ecp@@"),
        "dtor": ("tdb", "??1TDB_StreamIDPath@@"),
        "length": ("tdb", "?GetLength@TDB_StreamIDPath@@"),
        "component": ("tdb", "?GetComponent@TDB_StreamIDPath@@"),
        "match": ("tdb", "?GetMatchName@TDB_StreamID@@"),
        "name": ("tdb", "?GetName@TDB_MatchName@@"),
    }
    if any(name.startswith(b"?BuildUIMenuFromItems@UI_Popup@") for name in exports["dvaui"]):
        specs.update(builder=("dvaui", "?BuildUIMenuFromItems@UI_Popup@"),
                     append=("dvaui", "?AppendItem@UI_Menu@controls@dvaui@@QEAA?AV?$IntrusivePtr@VUI_MenuItem@"))
    symbols = {}
    addresses = {}
    for key, (kind, prefix) in specs.items():
        name, rva = _popup_export(exports[kind], prefix.encode("ascii"))
        addresses[key] = rva
        symbols[key] = {"module": kind, "name": name.decode("ascii"),
                        "bytes": images[kind].read_rva(rva, 12).hex()}
    getter = images["afterfxlib"].read_rva(addresses["path"], 27)
    if not re.fullmatch(rb"\x40\x53\x48\x83\xec\x30\x48\x8b\x01\x48\x8b\xda\xff\x90...."
                        rb"\x48\x8b\xc3\x48\x83\xc4\x30\x5b\xc3", getter, re.S):
        raise BuildMismatch("effect popup path return ABI changed")
    if images["tdb"].read_rva(addresses["length"], 12) != bytes.fromhex("488bc1488b4910482b480848"):
        raise BuildMismatch("effect popup path bounds layout changed")
    image = images["dvaui"]
    profile = {"symbols": symbols}
    if "builder" in symbols:
        body = _popup_function(image, addresses["builder"])
        call = body[_popup_call_index(body, addresses["append"])]
        profile.update(builder_bytes=symbols["builder"]["bytes"],
                       label_return_offset=call.address + call.size - addresses["builder"])
    body = _popup_function(image, addresses["osbuilder"])
    calls = [op for i, op in enumerate(body) if i >= 2 and op.bytes == bytes.fromhex("ff5038")
             and body[i-2].mnemonic == body[i-1].mnemonic == "mov"
             and body[i-2].op_str.startswith("rdx, ") and body[i-1].op_str.startswith("rcx, ")]
    if len(calls) != 1:
        raise BuildMismatch("OS popup menu label callsite unavailable or ambiguous")
    body = _popup_function(image, addresses["custom"])
    i = _popup_call_index(body, addresses["flyoutitem"])
    item = body[i]
    step = image.read_rva(item.address + item.size, 8)
    stride = struct.unpack_from("<I", step, 4)[0]
    if step[:4] != b"\x90\x48\x81\xc3" or not 32 <= stride <= 2048 or stride % 8:
        raise BuildMismatch("popup descriptor stride changed")
    body = _popup_function(image, addresses["flyoutitem"])
    label = body[_popup_call_index(body, addresses["flyoutctor"])]
    profile.update(descriptor_stride=stride,
                   flyout_item_return_offset=item.address + item.size - addresses["custom"],
                   flyout_ctor_return_offset=label.address + label.size - addresses["flyoutitem"],
                   os_builder_bytes=symbols["osbuilder"]["bytes"],
                   os_label_return_offset=calls[0].address + calls[0].size - addresses["osbuilder"])
    return profile
