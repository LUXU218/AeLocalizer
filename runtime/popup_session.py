# -*- coding: utf-8 -*-
"""Typed popup session: only the real ParamPopup vtable, only +0x70.

Runs inside the managed runtime; imports project core read-only through
AELOC_PROJECT_ROOT. No shared-guard JS/WIN dependency.

The vtable is NOT hardcoded to AE2025.  ``build_profile.popup_profile`` finds
the ``TDB_DynamicEnglishDisplayName`` RTTI class on disk and derives the
vtable RVA (complete-object-locator + vftable-slot scan) together with the
field offset used by every write.  The runtime still verifies the live vtable
pointer and the original bytes before any write, so an unverified layout can
only produce diagnostics, never a blind write.
"""
from __future__ import annotations

import os
import sys
import time

try:                                    # deployed flat next to guard_uni.py
    from build_profile import (POPUP_FIELD_OFFSET, BuildMismatch, known_version, popup_activity_profile,
                               media_popup_field_layout, popup_field_layout, popup_profile)
except ImportError:                     # imported as runtime.popup_session
    from .build_profile import (POPUP_FIELD_OFFSET, BuildMismatch, known_version, popup_activity_profile,
                                media_popup_field_layout, popup_field_layout, popup_profile)

# AE2025 25.5.0.4 historical value. Real sessions derive the vtable from the
# installed flt.dll; this fallback only keeps callers that inject a fake
# _open (unit tests) and diagnostics working.
POPUP_VT_RVA = 0xCDC10

#: Largest typed definition we will read and treat as one dropdown record.
#: BCC_FILMSTOCKS' film-stock catalogue is 3409 bytes / 194 options; the old
#: 2048 limit dropped it entirely. Reads stay bounded and page-validated, and
#: writes never allocate, so a larger ceiling only widens discovery.
POPUP_RECORD_MAX_BYTES = 65536


class PopupSession:
    def __init__(self, pid: int, data_root: str, project_root: str,
                 encoding="gbk", deadline=3.0, identity="",
                 plugin_hashes=None):
        self.pid = int(pid)
        self.identity = str(identity or "")
        self.plugin_hashes = dict(plugin_hashes or {})
        self.data_root = data_root
        self.project_root = project_root
        self.display_only = False
        self.encoding = encoding
        self.deadline = float(deadline)
        self.records = []
        self.errors = []
        self.profile = {}
        self.diag = {}
        # 当前这轮已发现行的完整性与后台 parade 全堆重扫必须分开：
        # rows_pending 决定当前语言开关是否已应用；discovery_pending 只是
        # 后台还在发现新增 parade/效果。pending 仅作为旧调用方的兼容并集。
        self.rows_pending = False
        self.discovery_pending = False
        self.pending = False
        self.rows_truncated = False
        self.scan_stats = {}
        self._row_cursor = 0
        self._pass_active = False
        self._pass_seen = set()
        self._pass_blocked = set()
        self.current_owners = set()
        self._blocked_current = set()
        self.baseline = {}
        self.baseline_key = {}
        self._restore_pending = {}
        self.persist = {}
        self.baseline_path = os.path.join(
            data_root, "guard_runtime", "popup_baseline.json")
        try:
            import json
            with open(self.baseline_path, encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                self.persist = obj
        except Exception:
            pass
        self._vt = 0
        self._proc = None
        self._cm = None
        self._popup_file_key = None

    def _open(self):
        try:
            return self._open_inner()
        except BaseException:
            # Never leak the process/frida handle when the build gate, the
            # module scan or the vtable derivation fails (reviewer R6).
            try:
                self._close()
            except Exception:
                pass
            raise

    def _open_inner(self):
        if self.project_root and self.project_root not in sys.path:
            sys.path.insert(0, self.project_root)
        from core import proc

        self._cm = proc.Process(self.pid, True)
        self._proc = self._cm.__enter__()
        modules = self._proc.modules()
        flt = next((m for m in modules
                    if m["name"].lower() == "flt.dll"), None)
        if not flt:
            raise RuntimeError("FLT.dll missing")
        from core.proc import module_digest

        sha = module_digest(self._proc, flt["path"])
        try:
            if known_version("flt", sha):
                profile = popup_profile(flt["path"], sha)
            else:
                from core.adapter import adapted_runtime_profile

                adopted = adapted_runtime_profile(self._proc, modules, self.data_root)
                tdb = next((m for m in modules
                            if m["name"].casefold() == "tdb.dll"), None)
                if not adopted:
                    raise BuildMismatch("no local profile for the loaded FLT and AfterFXLib")
                if not tdb or module_digest(self._proc, tdb["path"]) != adopted.tdb_sha:
                    raise BuildMismatch("loaded TDB does not match the local profile")
                if not adopted.popup_layout_verified:
                    raise BuildMismatch("local profile has no verified TDB popup layout")
                media_sha = adopted.popup_media_sha
                media = next((m for m in modules if m["name"].casefold() == "mediafoundation.dll"), None)
                if media_sha and (not media
                                  or os.path.normcase(os.path.dirname(media["path"])) != os.path.normcase(os.path.dirname(flt["path"]))
                                  or module_digest(self._proc, media["path"]) != media_sha):
                    raise BuildMismatch("loaded MediaFoundation does not match the local popup profile")
                key = (sha, adopted.tdb_sha, media_sha)
                if self._popup_file_key == key:
                    profile = self.profile
                else:
                    if media_sha:
                        media_popup_field_layout(tdb["path"], adopted.tdb_sha, media["path"], media_sha)
                    else:
                        popup_field_layout(tdb["path"], adopted.tdb_sha)
                    profile = popup_profile(flt["path"], sha, allow_unknown=True)
                    profile["verified_build"] = True
                    profile["local_version"] = adopted.version
                    self._popup_file_key = key
        except BuildMismatch as exc:
            raise RuntimeError("FLT build not verified: %s" % exc)
        self.profile = profile
        self._vt = int(flt["base"]) + int(profile["vt_rva"])
        return self._proc, flt

    def _pkey(self, effect, param, plugin=""):
        return "%s|%s|%s\x1f%s" % (self.identity, plugin, effect, param)

    def activity_profile(self):
        process, _flt = self._open()
        try:
            tdb = next(m for m in process.modules() if m["name"].casefold() == "tdb.dll")
            return dict(popup_activity_profile(tdb["path"]), vtable=hex(self._vt),
                        field_offset=POPUP_FIELD_OFFSET)
        finally:
            self._close()

    def _sync_persist(self):
        try:
            for ptr, b in self.baseline.items():
                key = self.baseline_key.get(ptr)
                if not key:
                    continue
                self.persist[self._pkey(key[0], key[1],
                                        key[2] if len(key) > 2 else "")] = {
                    "raw": b["raw"].hex(),
                    "last": (b.get("last") or b"").hex() if b.get("last") else "",
                    "capacity": int(b.get("capacity") or 0),
                    "address": int(b.get("address") or 0),
                    "guid": b.get("guid") or "",
                    "parent": int(b.get("parent") or 0),
                    "pointer": int(b.get("pointer") or 0),
                }
            os.makedirs(os.path.dirname(self.baseline_path), exist_ok=True)
            tmp = self.baseline_path + ".tmp"
            import json
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.persist, f, ensure_ascii=False)
            os.replace(tmp, self.baseline_path)
        except Exception:
            pass

    def _close(self):
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._proc = None

    @staticmethod
    def _rec_owner(rec):
        return (int(rec["address"]), str(rec["guid"]),
                int(rec["parent"]),
                str(rec.get("effectMatchName") or ""),
                str(rec.get("matchName") or ""))

    def _build_current_ownership(self, p, rows, vt):
        """Cheap full-row ownership/alias prepass before any string read/write."""
        owners = set()
        ptr_owners = {}
        blocked = set()
        for row in rows:
            try:
                addr = int(row.get("address") or 0)
                guid = str(row.get("guid") or "")
                parent = int(row.get("parent") or 0)
                if not addr or not guid:
                    continue
                if p.read(addr + 8, 16).hex() != guid:
                    continue
                if p.u64(addr + 32) != parent:
                    continue
                owner = (addr, guid, parent,
                         str(row.get("effectMatchName") or ""),
                         str(row.get("matchName") or ""))
                owners.add(owner)
                ptr = p.u64(addr + 0x70)
                if ptr:
                    ptr_owners.setdefault(int(ptr), set()).add(owner)
            except Exception:
                continue
        for ptr, os_ in ptr_owners.items():
            if len(os_) > 1:
                blocked.add(int(ptr))
                self.errors.append("alias-conflict:%x" % int(ptr))
        self.current_owners = owners
        self._blocked_current = blocked
        return owners, blocked

    @staticmethod
    def _rec_key(rec):
        return (int(rec["address"]), str(rec["guid"]),
                int(rec["parent"]), int(rec["pointer"]),
                str(rec.get("effectMatchName") or ""),
                str(rec.get("matchName") or ""))

    def _merge_record(self, records_by_pointer, rec, blocked):
        ptr = int(rec["pointer"])
        if ptr in blocked:
            return
        key = self._rec_key(rec)
        # Reinsert refreshed records at the end, preserving their scan order.
        old = records_by_pointer.pop(ptr, None)
        if old is not None:
            old_key = self._rec_key(old)
            if old_key != key:
                blocked.add(ptr)
                self.errors.append("alias-conflict:%x" % ptr)
                self._pass_seen.discard(old_key)
                return
        records_by_pointer[ptr] = rec
        self._pass_seen.add(key)

    def scan(self, *, prefer_current=False, visible_streams=()):
        """Incremental typed scan; retention is safe across partial passes."""
        from core.paramdiscovery import parameter_rows

        self.errors = []
        p, flt = self._open()
        deadline = time.monotonic() + self.deadline
        disc_deadline = min(deadline, time.monotonic() + 1.0)
        parade_budget = min(0.20, self.deadline) if self.scan_stats.get("rows") else None
        try:
            profile, rows, _parades, pending = parameter_rows(
                p, self.data_root, deadline=disc_deadline, with_state=True,
                rescan_interval=2.0, parade_scan_budget=parade_budget,
                prefer_current=prefer_current, visible_streams=visible_streams, popup_ownership=True)
            encoding = str(getattr(profile, "encoding", "") or self.encoding).lower()
            if encoding not in ("utf8", "utf-8", "mbcs", "gbk"):
                raise RuntimeError("unsupported popup encoding: " + encoding)
            self.encoding = encoding
            _pid = "|".join([
                str(getattr(profile, "version", "")),
                str(getattr(profile, "flt_sha", "")),
                str(getattr(profile, "tdb_sha", "")),
                str(getattr(profile, "encoding", self.encoding)),
            ])
            if not self.identity:
                self.identity = _pid
            elif not self.identity.endswith("|" + _pid):
                self.identity = self.identity + "|" + _pid
            vt = self._vt or (int(flt["base"]) + POPUP_VT_RVA)
            self._vt = vt
            self.diag = {
                "vt": vt,
                "verified_build": bool(self.profile.get("verified_build")),
                "class_name": self.profile.get("class_name") or "",
                "field_offset": POPUP_FIELD_OFFSET,
                "encoding": self.encoding,
                "rows": len(rows),
                "vt_mismatch": 0,
                "ptr_invalid": 0,
            }
            # Ownership/alias index uses ALL current rows, before the row
            # budget and before any string read/apply. A later chunk's owner
            # sharing a pointer blocks it before the first write.
            self._build_current_ownership(p, rows, vt)
            if visible_streams:
                # Foreground rows precede the current background cursor.
                self._pass_active = False
            # Budget starts only after the full ownership prepass, and always
            # grants a small minimum slice so a slow index cannot pin cursor=0.
            now = time.monotonic()
            row_deadline = max(now + 0.25, min(deadline, now + 1.0))
            blocked = set(self._pass_blocked) | set(self._blocked_current)
            candidate_bases = {}
            if not self._pass_active:
                self._row_cursor = 0
                self._pass_seen = set()
                self._pass_blocked = set()
                blocked = set(self._blocked_current)
                self._pass_active = True
            self._row_cursor = max(0, min(int(self._row_cursor), len(rows)))
            self.rows_truncated = False
            idx = self._row_cursor
            records_by_pointer = {int(r["pointer"]): r for r in self.records}
            while idx < len(rows):
                if time.monotonic() > row_deadline:
                    self.rows_truncated = True
                    break
                row = rows[idx]
                idx += 1
                if not (row.get("effectMatchName") and row.get("matchName")):
                    continue  # Still participates in the alias index above.
                addr = int(row.get("address") or 0)
                try:
                    if p.read(addr + 8, 16).hex() != row.get("guid"):
                        continue
                    if p.u64(addr + 32) != int(row.get("parent") or 0):
                        continue
                    if p.u64(addr) != vt:
                        self.diag["vt_mismatch"] = self.diag.get("vt_mismatch", 0) + 1
                        continue
                    ptr = p.u64(addr + POPUP_FIELD_OFFSET)
                    if not ptr:
                        continue
                    from core.proc import virtual_query
                    mbi = virtual_query(p, ptr)
                    if not mbi or mbi.Type != 0x20000 or mbi.Protect != 4:
                        self.diag["ptr_invalid"] = self.diag.get("ptr_invalid", 0) + 1
                        continue
                    from core.popup_fields import read_cstring
                    # Long typed definitions are real: BCC_FILMSTOCKS ships a
                    # 3409-byte "Custom|L003 Lavender Tint|..." list (194
                    # options). The old 2048 cap silently dropped every such
                    # record, so those dropdowns were never collected and could
                    # never be translated — the 补词 page then reported "no
                    # missing words" because the dictionary did cover them.
                    # Reading is bounded and page-validated either way; the
                    # write path still refuses to grow the buffer.
                    raw = read_cstring(p, ptr, POPUP_RECORD_MAX_BYTES)
                    if raw is None or not raw or raw.count(b"|") < 1:
                        continue
                    try:
                        text = raw.decode(self.encoding)
                    except UnicodeError:
                        continue
                    effect = str(row.get("effectMatchName") or "")
                    plugin = str(self.plugin_hashes.get(effect) or "")
                    bkey = (effect, str(row.get("matchName") or ""), plugin)
                    owner = {"address": addr,
                             "guid": str(row.get("guid") or ""),
                             "parent": int(row.get("parent") or 0),
                             "pointer": ptr}
                    base = self.baseline.get(ptr)
                    stored = None
                    if base is None:
                        stored = self.persist.get(self._pkey(*bkey))
                    owner_ok = bool(base
                                    and base.get("address") == owner["address"]
                                    and base.get("guid") == owner["guid"]
                                    and base.get("parent") == owner["parent"]
                                    and base.get("pointer") == owner["pointer"])
                    if not owner_ok and isinstance(stored, dict):
                        # Persisted text may help, but its old capacity must
                        # never be used for a new stream/allocation.
                        try:
                            base = {"raw": raw, "last": None,
                                    "original": bytes.fromhex(stored["raw"]),
                                    "capacity": len(raw) + 1,
                                    **owner}
                            owner_ok = True
                        except Exception:
                            base = None
                    if (not owner_ok or base is None
                            or (raw != base.get("raw")
                                and raw != base.get("last"))):
                        # new stream/pointer reuse or externally changed bytes:
                        # original capacity is invalid.
                        base = {"raw": raw, "last": None,
                                "original": raw,
                                "capacity": len(raw) + 1, **owner}
                    candidate_bases[ptr] = (base, bkey)
                    rec = {"effectMatchName": row.get("effectMatchName") or "",
                           "matchName": row.get("matchName") or "",
                           "address": addr, "guid": row.get("guid") or "",
                           "parent": row.get("parent") or 0,
                           "pointer": ptr, "offset": POPUP_FIELD_OFFSET,
                           "raw": raw,
                           "original": base.get("original", base["raw"]),
                           "text": text,
                           "capacity": base["capacity"]}
                    self._merge_record(records_by_pointer, rec, blocked)
                except Exception:
                    continue
            self._row_cursor = idx
            self._pass_blocked = set(blocked)
            for ptr in blocked:
                records_by_pointer.pop(ptr, None)
            self.records = list(records_by_pointer.values())
            # Commit baseline only after ambiguity resolution. A conflicting
            # pointer keeps the prior trustworthy owner/baseline untouched.
            for ptr, (base, bkey) in candidate_bases.items():
                if ptr in blocked:
                    continue
                self.baseline[ptr] = base
                self.baseline_key[ptr] = bkey
            full_pass = (idx >= len(rows)) and not self.rows_truncated
            if full_pass and not pending:
                # Only a complete non-pending current pass may remove owners.
                self.records = [r for r in self.records
                                if self._rec_key(r) in self._pass_seen]
            if full_pass:
                self._row_cursor = 0
                self._pass_active = False
                self._pass_seen = set()
                self._pass_blocked = set()
            else:
                self._pass_active = True
            self.rows_pending = bool(self.rows_truncated)
            self.discovery_pending = bool(pending)
            # 兼容旧调用方；LanguageEngine 只用上面两个独立字段判断完成度。
            self.pending = bool(self.rows_pending or self.discovery_pending)
            self.scan_stats = {
                "rows": len(rows), "processed": int(idx),
                "cursor": int(self._row_cursor),
                "truncated": bool(self.rows_truncated),
                "rows_pending": bool(self.rows_pending),
                "discovery_pending": bool(self.discovery_pending),
                "pending": bool(self.pending),
                "records": len(self.records),
                "vt": int(vt),
                "verified_build": bool(self.profile.get("verified_build")),
                "vt_mismatch": int(self.diag.get("vt_mismatch") or 0),
                "ptr_invalid": int(self.diag.get("ptr_invalid") or 0),
            }
            self._sync_persist()
            return self.records
        finally:
            self._close()

    def _valid(self, rec):
        p = self._proc
        if p is None:
            return False
        try:
            if self._rec_owner(rec) not in self.current_owners:
                return False
            if int(rec["pointer"]) in self._blocked_current:
                return False
            if p.read(rec["address"] + 8, 16).hex() != rec["guid"]:
                return False
            if p.u64(rec["address"] + 32) != int(rec["parent"]):
                return False
            if p.u64(rec["address"]) != self._vt:
                return False
            if p.u64(rec["address"] + POPUP_FIELD_OFFSET) != int(rec["pointer"]):
                return False
            from core.proc import virtual_query
            mbi = virtual_query(p, int(rec["pointer"]))
            if (not mbi or mbi.Type != 0x20000 or mbi.Protect != 4
                    or getattr(mbi, "Protect", 0) & 0x100):
                return False
            return True
        except Exception:
            return False

    @property
    def needs_restore(self):
        # Current verified rows can also recover a prior session's original
        # from the on-disk baseline. The cache alone is not a live owner.
        return bool(self._restore_pending) or any(
            rec["raw"] != rec["original"] for rec in self.records)

    def apply(self, mode: str, mapping: dict):
        """mode on/off; current typed records; original bytes are capacity proof."""
        p, _flt = self._open()
        changed = 0
        skipped = 0
        unchanged = 0
        reasons = {}
        def _why(k):
            reasons[k] = reasons.get(k, 0) + 1
        try:
            for rec in self.records:
                if not self._valid(rec):
                    skipped += 1
                    _why("identity-or-type")
                    continue
                if mode == "on" and self.display_only:
                    # Cached 2026 UI controls must never inherit translated
                    # definitions. Paint and local menu labels supply Chinese.
                    unchanged += 1
                    _why("display-only")
                    continue
                key = self._rec_key(rec)
                if mode == "off":
                    target = rec["original"]
                    if key in self._restore_pending and self._restore_pending[key] != target:
                        skipped += 1
                        _why("restore-baseline-changed")
                        continue
                else:
                    base = self.baseline.get(rec["pointer"])
                    if rec["raw"] != rec["original"] and not (
                            base and base.get("last") == rec["raw"]
                            and base.get("original") == rec["original"]
                            and all(base.get(key) == rec[key] for key in
                                    ("address", "guid", "parent", "pointer"))):
                        unchanged += 1
                        _why("unchanged-already")
                        continue
                    try:
                        parts = rec["original"].decode(self.encoding).split("|")
                        effect = rec.get("effectMatchName")
                        parameter = rec.get("matchName")
                        def translate(part):
                            if not part:
                                return part
                            key = part.strip()
                            if effect and parameter:
                                scoped = mapping.get("\x1f".join((effect, parameter, key)))
                                if scoped:
                                    return scoped
                            if any("\u3400" <= ch <= "\u9fff" for ch in part):
                                return part
                            return mapping.get(key) or part
                        trans = [translate(part) for part in parts]
                        target = "|".join(trans).encode(self.encoding)
                    except UnicodeError:
                        skipped += 1
                        _why("encoding")
                        continue
                if target == rec["raw"]:
                    if mode == "off" and key in self._restore_pending:
                        # A write can report failure after changing memory.
                        # The cached raw value alone is not an off receipt.
                        try:
                            restored = p.read(rec["pointer"], len(target) + 1) == target + b"\0"
                        except Exception:
                            restored = False
                        if not restored:
                            skipped += 1
                            _why("expected-bytes")
                            continue
                        self._restore_pending.pop(key)
                    unchanged += 1
                    if mode == "off":
                        _why("off-already")
                    elif target == rec["original"]:
                        _why("unchanged-no-translation")
                    else:
                        _why("unchanged-already")
                    continue
                if len(target) + 1 > rec["capacity"]:
                    skipped += 1
                    _why("capacity-unverified" if mode == "on" else "capacity")
                    continue
                try:
                    if p.read(rec["pointer"], len(rec["raw"]) + 1) != rec["raw"] + b"\0":
                        skipped += 1
                        _why("expected-bytes")
                        continue
                    if mode == "on" and target != rec["original"]:
                        self._restore_pending.setdefault(key, rec["original"])
                    p.write_exact(rec["pointer"], target + b"\0")
                    if target == rec["original"]:
                        self._restore_pending.pop(key, None)
                    rec["raw"] = target
                    try:
                        rec["text"] = target.decode(self.encoding)
                    except UnicodeError:
                        rec["text"] = ""
                    b = self.baseline.get(rec["pointer"])
                    if b is not None:
                        b["last"] = target
                    changed += 1
                except Exception:
                    skipped += 1
                    _why("write")
            self._sync_persist()
            return {"changed": changed, "skipped": skipped,
                    "unchanged": unchanged, "total": len(self.records),
                    "reasons": reasons, "errors": list(self.errors)}
        finally:
            self._close()

    def close(self):
        self._close()
