# -*- coding: utf-8 -*-
"""AeLocalizer language runtime and single Frida SDK host.

Runs as an independent ordinary-permission Python process. Effect titles and
parameter/dropdown paint share this SDK host; typed popup definitions use
owner-checked, capacity-bounded writes with English restore snapshots. It never
imports or executes the legacy shared guard. Runtime, out, tmp, control and lock
all live under the caller's DATA_ROOT.

Control protocol v1 (atomic request.json / status.json):
  request: {protocol, gen, request_id, host:{pid, ctime, image}, mode:on|off|init|stop}
  status : {protocol, control_ready, language_ready, phase, ready, runtime_pid,
            runtime_ctime, host, session, gen, ack_gen, mode, state, pending,
            error, session_lost, updated}

The production host validator uses real Win32 process identity. Tests exercise
the state machine with an injected validator; the production loop has no
"skip identity" switch.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import uuid

PROTOCOL = "aeloc-managed-guard/1"
CONTROL_VERSION = 1
DATA_ROOT = os.environ.get("AELOC_DATA_ROOT", "").strip()
if not DATA_ROOT:
    DATA_ROOT = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AeLocalizer")
RUNTIME_DIR = os.path.join(DATA_ROOT, "guard_runtime")
OUT = os.path.join(RUNTIME_DIR, "out")
CTL = os.path.join(RUNTIME_DIR, "control")
TMP = os.path.join(RUNTIME_DIR, "tmp")
DICT = os.environ.get("AELOC_ENGINE_DICT", os.path.join(DATA_ROOT, "engine_dict"))
PROJECT_ROOT = os.environ.get("AELOC_PROJECT_ROOT", "")
STOP_FLAG = os.path.join(DICT, "STOP_NOW.flag")
LOG = os.path.join(OUT, "managed_guard.log")
LOCK = os.path.join(OUT, "guard.lock")
LOCK_ID = os.path.join(OUT, "guard.lock.json")
REQ = os.path.join(CTL, "request.json")
# 补词页「立即落盘」标志：GUI 点「提取」时丢它，本轮立刻把观察结果写出去。
DUMP_NOW = os.path.join(OUT, "DUMP_NOW.flag")
ST = os.path.join(CTL, "status.json")

# Popup failures we intentionally degrade on: the session builder wraps a
# structural BuildMismatch into RuntimeError("FLT build not verified: ..."),
# and live process reads surface OSError.  Anything else is a programming bug
# and must NOT be silently converted into "popup unavailable" (reviewer R4).
_POPUP_EXPECTED = (RuntimeError, OSError)


def _log(msg):
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _atomic_json(path, obj):
    """Atomic JSON with unique temp + Windows reader-sharing retry.

    A GUI reading status.json can briefly keep a handle that makes replace fail
    with WinError 5. Retry; on permanent failure keep the old file (never fake a
    fresh ready) and log. Returns bool.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%s.%s" % (path, os.getpid(), uuid.uuid4().hex)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for i in range(40):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if i == 39:
                    raise
                time.sleep(0.02)
        return False
    except Exception as exc:
        _log("atomic %s failed: %s" % (os.path.basename(path), exc))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _write_capture(out_dir, obs):
    """Publish this session's observed parameter labels for the GUI 补词 page.

    The managed runtime replaces labels at paint time and never renames a
    parameter, so it can never produce the old guard's
    all_param_names/renamed_names/rename_miss/enum_missing quartet. Instead it
    writes what it actually painted, which is exactly what the 补词 page needs:

      all_param_names.json   every label met (list, sorted)
      renamed_names.json     labels whose translation was really drawn
      rename_miss.json       [{en, why}] labels seen but not drawn
      enum_missing.json      dropdown/menu labels met without a translation

    Everything here is derived from one session and cleared on startup by the
    GUI's existing "AE 没在运行 / 还没有采集文件" guards, so a stale file can
    never be mistaken for live data.
    """
    try:
        names = sorted(obs.get("seen") or {})
        applied = sorted(obs.get("applied") or {})
        miss_map = obs.get("miss") or {}
        miss = [{"en": k, "why": str(miss_map[k])[:40]}
                for k in sorted(miss_map)][:2000]
        enum = sorted(obs.get("enum_seen") or {})
    except Exception as exc:
        _log("capture build failed: %r" % (exc,))
        return False
    ok = True
    for name, payload in (("all_param_names.json", names),
                          ("renamed_names.json", applied),
                          ("rename_miss.json", miss),
                          ("enum_missing.json", enum)):
        if not _atomic_json(os.path.join(out_dir, name), payload):
            ok = False
    return ok


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class HostGone(Exception):
    """Definitive: pid does not exist (or target exited)."""


def _proc_identity(pid):
    """Real process identity; None means unknown (permission/temporary error).

    HostGone is raised only for WAIT_OBJECT_0 or OpenProcess
    ERROR_INVALID_PARAMETER (nonexistent pid). Access denied/timeouts stay
    unknown and must never be treated as death.
    """
    if not pid:
        raise HostGone()
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        k.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p]
        k.GetProcessTimes.restype = ctypes.c_int
        k.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong)]
        k.QueryFullProcessImageNameW.restype = ctypes.c_int
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        k.WaitForSingleObject.restype = ctypes.c_ulong

        class FT(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

        # WaitForSingleObject needs SYNCHRONIZE; QUERY_LIMITED alone is not enough.
        h = k.OpenProcess(0x00100000 | 0x1000, 0, int(pid))
        if not h:
            err = int(ctypes.get_last_error())
            if err == 87:          # ERROR_INVALID_PARAMETER -> no such pid
                raise HostGone()
            return None            # access denied / other: unknown, not dead
        try:
            rc = int(k.WaitForSingleObject(ctypes.c_void_p(h), 0))
            if rc == 0:            # WAIT_OBJECT_0 -> exited
                raise HostGone()
            if rc != 0x102:        # WAIT_FAILED/other -> unknown
                return None
            c, e, kt, u = FT(), FT(), FT(), FT()
            if not k.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(c),
                                     ctypes.byref(e), ctypes.byref(kt),
                                     ctypes.byref(u)):
                return None
            ctime = (int(c.hi) << 32) | int(c.lo)
            buf = ctypes.create_unicode_buffer(32768)
            n = ctypes.c_ulong(32768)
            if not k.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf,
                                                ctypes.byref(n)):
                return None
            return {"pid": int(pid), "ctime": ctime,
                    "image": os.path.normcase(os.path.abspath(buf.value))}
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except HostGone:
        raise
    except Exception:
        return None


def _parse_ctime(v):
    """协议：int，或明确 16 位 hex；纯十进制字符串不得先当 hex。"""
    if type(v) is int:
        return v
    if isinstance(v, str) and v:
        s = v.strip()
        try:
            if len(s) == 16:
                return int(s, 16)
            if s.isdigit():
                return int(s, 10)
            return int(s, 16)
        except ValueError:
            return 0
    return 0


def validate_production_host(h):
    """Production validator: real alive pid, exact FILETIME, absolute AfterFX.exe."""
    if not isinstance(h, dict):
        return None
    try:
        pid = h.get("pid")
        if type(pid) is not int or pid <= 0:
            return None
        want_ctime = _parse_ctime(h.get("ctime"))
        image = h.get("image")
        if not want_ctime or not isinstance(image, str) or not image:
            return None
        actual = _proc_identity(pid)
        if not actual or actual["ctime"] != want_ctime:
            return None
        want_img = os.path.normcase(os.path.abspath(image))
        if not want_img.lower().endswith("afterfx.exe"):
            return None
        if actual["image"] != want_img:
            return None
        return actual
    except Exception:
        return None


def _redraw_host(pid):
    """Async, input-free Win32 invalidation for current AE window (3 bounded)."""
    try:
        import ctypes
        from ctypes import wintypes as W
        if PROJECT_ROOT and PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        from core.proc import main_window

        hwnd = main_window(int(pid))
        if not hwnd:
            return False
        u = ctypes.windll.user32
        u.RedrawWindow.argtypes = [W.HWND, ctypes.c_void_p,
                                   ctypes.c_void_p, W.UINT]
        u.RedrawWindow.restype = W.BOOL
        u.InvalidateRect.argtypes = [W.HWND, ctypes.c_void_p, W.BOOL]
        u.InvalidateRect.restype = W.BOOL
        for _ in range(3):
            u.RedrawWindow(W.HWND(hwnd), None, None,
                           0x1 | 0x4 | 0x80 | 0x400)
            u.InvalidateRect(W.HWND(hwnd), None, False)
            time.sleep(0.12)
        return True
    except Exception:
        return False


def _effect_host_status(host):
    if not isinstance(host, dict):
        return "dead"
    try:
        actual = _proc_identity(int(host.get("pid") or 0))
    except HostGone:
        return "dead"
    except Exception:
        return "unknown"
    if not actual:
        return "unknown"
    try:
        if (int(actual.get("ctime") or 0)
                != int(host.get("ctime") or 0)
                or str(actual.get("image") or "").lower()
                != str(host.get("image") or "").lower()):
            return "dead"
    except Exception:
        return "unknown"
    return "alive"


def _validate_effect_host(host):
    if not isinstance(host, dict):
        return None
    pid = int(host.get("pid") or 0)
    try:
        actual = _proc_identity(pid)
    except HostGone:
        return None
    except Exception:
        return None
    if not actual:
        return None
    if (int(actual.get("ctime") or 0) != int(host.get("ctime") or 0)
            or str(actual.get("image") or "").lower()
            != str(host.get("image") or "").lower()):
        return None
    return actual


class LanguageEngine:
    """Single writer for parameter draw + typed popup; no PF/display writes."""

    def __init__(self, project_root, data_root, engine_dict):
        self.project_root = project_root
        self.data_root = data_root
        self.engine_dict = engine_dict
        self.draw = None
        self.popup = None
        self.host = None
        self.mapping = {}
        self.last_error = ""
        self.popup_reasons = {}
        self.popup_unsupported = 0
        self.rows_pending = False
        self.background_discovery_pending = False
        self.draw_stats = {}
        self.draw_error = ""
        self.map_error = ""
        self.popup_error = ""
        self.popup_restore_unknown = False
        self.identity_meta = {}
        self.stop_stage = ""
        self.stop_reason = ""
        self.stop_detail = ""
        self._stop_logged = None

    def _draw_progress(self, stage, ok=None):
        # Called by ParamDrawHook.close BEFORE blocking RPC/unload/detach.
        if ok is None:
            self._set_stop(stage, "begin")
        elif ok:
            self._set_stop(stage, "done")
        else:
            self._set_stop(stage, "failed", getattr(self.draw, "last_error", ""))

    def _set_stop(self, stage, reason, detail=""):
        self.stop_stage = str(stage)[:80]
        self.stop_reason = str(reason)[:160]
        self.stop_detail = str(detail)[:240]
        if self._stop_logged != (stage, reason, detail):
            self._stop_logged = (stage, reason, detail)
            _log("stop stage=%s reason=%s detail=%s"
                 % (stage, reason, str(detail)[:160]))

    def observe(self):
        """-> {"rev", "seen", "applied", "miss", "enum_seen"} for the 补词 page.

        Read-only. Drawn labels come from the draw hook; dropdown options come
        from the typed popup records, which already hold each parameter's full
        "A|B|C" definition (the old guard's enum_missing.json source). Only
        options without a translation are reported, so the page keeps showing
        exactly the words that still need one.
        """
        if self.draw is None:
            return {}
        try:
            obs = dict(self.draw.observe() or {})
        except Exception as exc:
            self.draw_error = "observe:%s" % str(exc)[:120]
            return {}
        if not obs:
            return {}
        try:
            enums = dict(obs.get("enum_seen") or {})
            records = getattr(self.popup, "records", None) or []
            for rec in records:
                for raw in str(rec.get("text") or "").split("|"):
                    opt = " ".join(raw.split())
                    if not opt or opt in self.mapping:
                        continue
                    if any("\u4e00" <= ch <= "\u9fff" for ch in opt):
                        continue
                    enums.setdefault(opt, 1)
            obs["enum_seen"] = enums
            # Popup records change without touching the draw revision.
            obs["rev"] = int(obs.get("rev") or 0) + len(records)
        except Exception as exc:
            self.draw_error = "observe-enum:%s" % str(exc)[:120]
        return obs

    def diagnostics(self):
        try:
            st = self.draw.stats() if self.draw is not None else {}
        except Exception as exc:
            st = {}
            self.draw_error = str(exc)[:160]
        if not st:
            st = dict(self.draw_stats or {})
        pst = {}
        if self.popup is not None:
            pst = {"vt": int(getattr(self.popup, "_vt", 0) or 0),
                   "verified_build": bool(
                       (getattr(self.popup, "profile", None) or {}).get(
                           "verified_build")),
                   "scan_stats": dict(getattr(self.popup, "scan_stats", None)
                                      or {}),
                   "diag": dict(getattr(self.popup, "diag", None) or {})}
        return {"draw": st, "draw_error": self.draw_error,
                "identity": dict(self.identity_meta),
                "popup": pst,
                "popup_error": self.popup_error,
                "popup_restore_unknown": bool(getattr(self, "popup_restore_unknown", False)),
                "popup_reasons": dict(self.popup_reasons),
                "popup_unsupported": self.popup_unsupported,
                "rows_pending": bool(self.rows_pending),
                "background_discovery_pending": bool(
                    self.background_discovery_pending),
                "map_error": self.map_error,
                "stop_stage": self.stop_stage,
                "stop_reason": self.stop_reason,
                "stop_detail": self.stop_detail}

    def _map(self):
        try:
            path = os.path.join(self.engine_dict, "extra_overrides.json")
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return {}, "not-dict"
            return ({str(k): str(v) for k, v in obj.items() if k and v},
                    "")
        except Exception as exc:
            return {}, "map-read:%s" % str(exc)[:120]

    def _refresh_mapping(self):
        new, err = self._map()
        if err:
            self.map_error = err
            self.draw_error = err
            # Keep the last trusted map for recovery, but never publish a
            # successful language apply from an unreadable dictionary.
            return False
        # Valid empty JSON is a real "no translations" payload: publish it and
        # clear the cache. Only read/parse errors preserve the last good map.
        changed = new != self.mapping
        self.mapping = new
        self.map_error = ""
        self.draw_error = ""
        if changed and self.draw is not None:
            self.draw.set_map(self.mapping)
        return True

    def _afterfxlib(self, pid):
        if self.project_root and self.project_root not in sys.path:
            sys.path.insert(0, self.project_root)
        from core.adapter import adapted_runtime_profile
        from core.proc import Process, module_digest
        from build_profile import known_version

        with Process(int(pid), False) as p:
            modules = p.modules()
            m = next((x for x in modules
                      if x["name"].lower() == "afterfxlib.dll"), None)
            if not m:
                raise RuntimeError("AfterFXLib.dll missing")
            sha = module_digest(p, m["path"])
            if known_version("afterfxlib", sha):
                return m["path"], None
            return m["path"], adapted_runtime_profile(p, modules, self.data_root)

    def attach(self, host):
        from param_draw_hook import ParamDrawHook
        from popup_session import PopupSession

        pid = int(host["pid"])
        same = (self.host and int(self.host.get("pid") or 0) == pid
                and int(self.host.get("ctime") or 0) == int(host.get("ctime") or 0)
                and str(self.host.get("image") or "") == str(host.get("image") or ""))
        healthy = (self.draw is not None and self.draw.script is not None)
        if same and healthy:
            return
        if not self.close():
            raise RuntimeError("old draw hook active; refusing second host")
        # Transaction: do not publish host/draw/popup until BOTH constructed.
        self.host = None
        _new_map, _map_err = self._map()
        if _map_err:
            self.map_error = _map_err
            if not self.mapping:
                raise RuntimeError("dictionary unavailable: " + _map_err)
        else:
            # Empty JSON object is a valid payload (OFF or user selected no
            # groups); it must clear old words, not be treated as unreadable.
            self.mapping = _new_map
            self.map_error = ""
        new_draw = None
        new_popup = None
        try:
            ident = ""
            plugins = {}
            try:
                with open(os.path.join(self.data_root, "guard_runtime",
                                       "param_identity.json"),
                          encoding="utf-8") as f:
                    cfg = json.load(f)
                plugins = cfg.get("plugins") or {}
                sel_ver = str(cfg.get("ae_version") or "")
                host_ver = str(cfg.get("host_ae_version") or "")
                # The running host is authoritative: a stale "selected
                # install" version must never namespace the popup baseline.
                ident = host_ver or sel_ver
                self.identity_meta = {
                    "selected": sel_ver,
                    "host": host_ver,
                    "source": "host" if host_ver else "selected",
                }
            except Exception:
                pass
            lib, adapted = self._afterfxlib(pid)
            new_draw = ParamDrawHook(pid, self.mapping, lib, adopted=adapted)
            new_draw.attach()
            import hashlib
            ident = hashlib.sha256(ident.encode("utf-8")).hexdigest()
            # Popup channel is optional: a build whose flt.dll popup vtable
            # cannot be structurally verified must not tear down the verified
            # parameter-draw channel. It fails closed (no popup writes) and
            # reports its own channel error instead.
            self.popup_error = ""
            try:
                new_popup = PopupSession(pid, self.data_root,
                                         self.project_root,
                                         identity=ident,
                                         plugin_hashes=plugins)
                new_popup.display_only = bool(new_draw.popup_paint_profile)
            except Exception as _pe:
                new_popup = None
                self.popup_error = str(_pe)[:160]
            self.draw = new_draw
            self.popup = new_popup
            self.host = dict(host)
            self.rows_pending = False
            self.background_discovery_pending = False
            self.last_error = ""
        except Exception:
            try:
                if new_draw is not None:
                    new_draw.abandon()
            except Exception:
                pass
            try:
                if new_popup is not None:
                    new_popup.close()
            except Exception:
                pass
            self.draw = None
            self.popup = None
            self.host = None
            raise

    @staticmethod
    def _critical_popup_reasons(reasons):
        """Reasons that mean the current popup write/identity really failed.

        capacity / unchanged are reported per-value but do not fail the whole
        language session; identity / expected-bytes / encoding / write do.
        """
        return {k: v for k, v in (reasons or {}).items()
                if k not in ("capacity", "capacity-unverified", "unchanged-no-translation",
                             "unchanged-already", "off-already", "display-only")}

    def _popup_progress(self):
        """Return (current_rows_complete, reason) and sync diagnostics.

        Only current-row completeness gates language_ready. The periodic
        full-heap parade rescan is a background discovery activity and must
        not flip an already-applied generation back to syncing. A cold start
        with zero discovered rows is the one exception: it cannot be called
        ready while the first discovery is still incomplete.
        """
        stats = getattr(self.popup, "scan_stats", None) or {}
        if self.popup is None:
            self.rows_pending = False
            self.background_discovery_pending = False
            return True, ""
        try:
            rows = int(stats.get("rows") or 0)
        except Exception:
            rows = 0
        try:
            processed = int(stats.get("processed") or 0)
        except Exception:
            processed = 0
        rows_pending = bool(
            getattr(self.popup, "rows_pending", False)
            or stats.get("rows_pending", False)
            or stats.get("truncated", False)
            or (rows > 0 and processed < rows))
        discovery_pending = bool(
            getattr(self.popup, "discovery_pending", False)
            or stats.get("discovery_pending", False))
        self.rows_pending = rows_pending
        self.background_discovery_pending = discovery_pending
        if rows_pending:
            return False, "row-pass-pending:%s" % (stats or {})
        if rows <= 0 and discovery_pending:
            # Cold start: no discovered row yet, so "applied" would be a lie.
            return False, "discovery-pending:%s" % (stats or {})
        return True, ""

    def _drop_popup(self, exc):
        """Popup unusable at scan/apply time: fail closed, keep draw channel."""
        self.popup_error = str(exc)[:160]
        try:
            if self.popup is not None:
                self.popup.close()
        except Exception:
            pass
        self.popup = None
        self.popup_reasons = {}
        self.popup_unsupported = 0
        self.rows_pending = False
        self.background_discovery_pending = False

    def _restore_popup_best_effort(self):
        """Restore cached popup records without a fresh scan.

        Returns True only when the owner cache is present and the off-apply
        reports no skipped row.  False means the UI must not claim the native
        strings were restored.
        """
        try:
            if self.popup is None or not (getattr(self.popup, "records", None)
                                          or {}):
                return False
            res = self.popup.apply("off", self.mapping)
            return not res.get("skipped")
        except Exception:
            return False

    def apply(self, mode, host, gen):
        """Return (language_ready, error). Caller updates status only from here."""
        try:
            self.attach(host)
            if not self._refresh_mapping():
                return False, "dictionary-unavailable:%s" % self.map_error
            if mode == "on":
                if not self.draw.mode(True, int(gen)):
                    self.draw_error = "draw-mode-rejected"
                    return False, "draw-mode"
                _redraw_host(int(host["pid"]))
                if self.popup is None:
                    # Params verified; popup channel explicitly unavailable.
                    self.popup_reasons = {}
                    self.popup_unsupported = 0
                    return True, ""
                try:
                    self.popup.scan()
                    if getattr(self.popup, "display_only", False):
                        if not self.draw.set_popup_records(self.popup.records, self.popup.encoding):
                            return False, "popup-menu-records"
                    res = self.popup.apply("on", self.mapping)
                except _POPUP_EXPECTED as _pe:
                    self._drop_popup(_pe)
                    return True, ""
                self.popup_reasons = dict(res.get("reasons") or {})
                self.popup_unsupported = int(res.get("skipped") or 0)
                complete, preason = self._popup_progress()
                if not complete:
                    return False, preason
                # ON: capacity/no-translation of one popup value is reported
                # as unsupported, not a failed parameter-language session.
                critical = self._critical_popup_reasons(self.popup_reasons)
                if critical:
                    return False, "popup-partial:%s" % self.popup_reasons
                return True, ""
            if mode == "off":
                self.draw.mode(False, int(gen))
                if not self.draw.drain(2.0):
                    return False, "draw-active"
                _redraw_host(int(host["pid"]))
                if self.popup is None:
                    self.popup_reasons = {}
                    self.popup_unsupported = 0
                    return True, ""
                try:
                    self.popup.scan()
                    if getattr(self.popup, "display_only", False):
                        if not self.draw.set_popup_records(self.popup.records, self.popup.encoding):
                            return False, "popup-menu-records"
                    res = self.popup.apply("off", self.mapping)
                    self.popup_restore_unknown = False
                except _POPUP_EXPECTED as _pe:
                    # The popup channel already wrote translations in this
                    # session.  A transient scan failure must not report "已
                    # 还原原文": try the cached records first, and if that
                    # cannot be confirmed expose popup_restore_unknown.
                    restored = self._restore_popup_best_effort()
                    self.popup_restore_unknown = not restored
                    self._drop_popup(_pe)
                    return True, ""
                self.popup_reasons = dict(res.get("reasons") or {})
                self.popup_unsupported = int(res.get("skipped") or 0)
                complete, preason = self._popup_progress()
                if not complete:
                    return False, preason
                # Same rule as apply("on")/refresh(): a per-value capacity or
                # already-restored outcome is not a failed OFF. Mirrors
                # _critical_popup_reasons so OFF and STOP agree on severity.
                critical = self._critical_popup_reasons(
                    dict(res.get("reasons") or {}))
                if critical:
                    self.popup_restore_unknown = True
                    return False, "popup-restore-partial:%s" % critical
                return True, ""
            return True, ""
        except Exception as exc:
            self.last_error = str(exc)[:180]
            self.draw_error = self.last_error
            return False, self.last_error

    def refresh(self, mode):
        """Periodically pick up new popup streams/dictionary updates while ON."""
        try:
            old_map = dict(self.mapping)
            if not self._refresh_mapping():
                return False, "dictionary-unavailable:%s" % self.map_error
            map_changed = self.mapping != old_map
            if self.popup is None:
                if self.popup_error:
                    # Params-only session: popup channel already reported as
                    # unavailable; do not flap language_ready every tick.
                    return True, ""
                return False, "no-popup-session"
            try:
                self.popup.scan()
                if getattr(self.popup, "display_only", False):
                    if not self.draw.set_popup_records(self.popup.records, self.popup.encoding):
                        return False, "popup-menu-records"
                res = self.popup.apply(mode, self.mapping)
            except _POPUP_EXPECTED as _pe:
                if mode == "off":
                    restored = self._restore_popup_best_effort()
                    self.popup_restore_unknown = not restored
                self._drop_popup(_pe)
                return True, ""
            # Apply the records already discovered even when background parade
            # discovery is still running; otherwise their labels wait for the
            # slowest discovery slice. Background discovery is only a diagnostic
            # and does not make the already-applied translation unready.
            if (res.get("changed") or map_changed) and self.host:
                _redraw_host(int(self.host["pid"]))
            self.popup_reasons = dict(res.get("reasons") or {})
            self.popup_unsupported = int(res.get("skipped") or 0)
            complete, preason = self._popup_progress()
            if not complete:
                return False, preason
            if mode == "off" and res.get("skipped"):
                return False, "popup-restore-partial:%s" % (res.get("reasons") or {})
            if self._critical_popup_reasons(self.popup_reasons):
                return False, "popup-partial:%s" % self.popup_reasons
            return True, ""
        except Exception as exc:
            return False, str(exc)[:160]

    def _host_status(self):
        """alive / dead / unknown; unknown must never authorize cleanup."""
        if not self.host:
            return "dead"
        pid = int(self.host.get("pid") or 0)
        try:
            actual = _proc_identity(pid)
        except HostGone:
            return "dead"
        except Exception:
            return "unknown"
        if actual is None:
            return "unknown"
        try:
            if (int(actual.get("ctime") or 0)
                    != int(self.host.get("ctime") or 0)
                    or str(actual.get("image") or "").lower()
                    != str(self.host.get("image") or "").lower()):
                return "dead"   # PID reused by another image/session
        except Exception:
            return "unknown"
        return "alive"

    def close(self):
        # STEP 1: stop new draw replacements first, even when identity is
        # unknown; only resource release is gated below.
        if self.draw is not None:
            self._set_stop("disable-rpc", "begin")
            try:
                if not self.draw.mode(False, self.draw._gen + 1):
                    self._set_stop("disable-rpc", "rejected")
                else:
                    self._set_stop("disable-rpc", "done")
            except Exception as exc:
                self._set_stop("disable-rpc", "exception", repr(exc))
        status = self._host_status()
        if status == "unknown":
            self._set_stop("identity", "host-unknown")
            return False
        alive = status == "alive"
        if not alive:
            # Exact host identity is dead. Never scan its memory or call native
            # UI; discard frida/popup resources and let a new host attach.
            try:
                if self.draw is not None:
                    self.draw.abandon()
            except Exception as exc:
                self._set_stop("abandon", "draw-abandon-exception", repr(exc))
            self.draw = None
            try:
                if self.popup is not None:
                    self.popup.close()
            except Exception as exc:
                self._set_stop("popup-close", "popup-close-exception", repr(exc))
            self.popup = None
            self.host = None
            self._set_stop("done", "host-dead-discard")
            return True
        try:
            if self.popup is not None:
                # alive host: re-read current tree before restoring.
                self._set_stop("popup-scan", "begin")
                try:
                    self.popup.scan()
                    self._set_stop("popup-scan", "done")
                except Exception as exc:
                    self._set_stop("popup-scan", "scan-exception", repr(exc))
                    return False
                self._set_stop("popup-restore", "begin")
                try:
                    res = self.popup.apply("off", self.mapping)
                except Exception as exc:
                    self._set_stop("popup-restore", "apply-exception", repr(exc))
                    return False
                if res.get("changed") and self.host:
                    _redraw_host(int(self.host["pid"]))
                # Only a reason that means the restore itself failed may block
                # STOP. "capacity" (a single value whose translated text does
                # not fit back into its original allocation) and "off-already"
                # are per-value outcomes that apply("off") already classifies
                # as non-critical elsewhere (_critical_popup_reasons). Treating
                # any skipped value as a fatal restore failure here made STOP
                # retry forever: AE stays alive, close() keeps returning False,
                # and the guard spins on popup-scan/popup-restore.
                critical = self._critical_popup_reasons(
                    dict(res.get("reasons") or {}))
                if critical:
                    self._set_stop("popup-restore", "skipped",
                                   json.dumps(critical, ensure_ascii=False))
                    self.popup_restore_unknown = True
                    return False
                self._set_stop("popup-restore", "done")
                self._set_stop("popup-close", "begin")
                self.popup.close()
                self._set_stop("popup-close", "done")
                self.popup = None
        except Exception as exc:
            self._set_stop("popup-close", "exception", repr(exc))
            return False
        if self.draw is not None:
            self._set_stop("draw-close", "begin")
            try:
                ok_draw = self.draw.close(progress=self._draw_progress)
            except Exception as exc:
                self._set_stop("draw-close", "exception", repr(exc))
                return False
            if not ok_draw:
                try:
                    st = self.draw.stats()
                except Exception as exc:
                    st = {"stats_error": repr(exc)}
                self._set_stop("draw-close", "close-false",
                               "last_error=%r stats=%s"
                               % (getattr(self.draw, "last_error", ""),
                                  json.dumps(st, ensure_ascii=False)[:240]))
                return False
            self.draw = None
        self.host = None
        self._set_stop("done", "safe")
        return True


def _copy_language_progress(state, lang):
    """Mirror the row/background split into the control status on every tick."""
    state.rows_pending = bool(getattr(lang, "rows_pending", False))
    state.background_discovery_pending = bool(
        getattr(lang, "background_discovery_pending", False))


def _language_poll_interval(mode, popup):
    return 0.30 if mode == "on" and popup is not None else 1.0


def _popup_capacity_skips(reasons):
    return int(reasons.get("capacity", 0)) + int(reasons.get("capacity-unverified", 0))


def _sync_language_diag(state, lang):
    try:
        d = lang.diagnostics()
        state.draw_stats = d.get("draw") or {}
        state.draw_error = d.get("draw_error") or ""
        state.map_error = d.get("map_error") or ""
        state.popup_reasons = d.get("popup_reasons") or {}
        state.popup_unsupported = int(d.get("popup_unsupported") or 0)
        state.popup_error = d.get("popup_error") or ""
        state.popup_restore_unknown = bool(
            d.get("popup_restore_unknown", False))
        state.identity = d.get("identity") or {}
        state.popup_diag = d.get("popup") or {}
        state.rows_pending = bool(d.get("rows_pending", False))
        state.background_discovery_pending = bool(
            d.get("background_discovery_pending", False))
        state.stop_stage = d.get("stop_stage") or ""
        state.stop_reason = d.get("stop_reason") or ""
        state.stop_detail = d.get("stop_detail") or ""
    except Exception as exc:
        state.draw_error = "diagnostics:%s" % str(exc)[:140]


class ControlState:
    """Pure state machine; validator is injected (production or test)."""

    def __init__(self, validator=validate_production_host):
        self.validate = validator
        self.host = None
        self.session = ""
        self.last_gen = 0
        self.mode = "off"
        self.state = "ready"
        self.error = ""
        self.session_lost = False
        self.request_id = ""
        self.language_ready = False
        self.phase = "control"
        self.applied_gen = 0
        self.pending = False
        self.rows_pending = False
        self.background_discovery_pending = False
        self.idempotent = False
        self.popup_skipped = 0
        self.popup_reasons = {}
        self.popup_unsupported = 0
        self.popup_error = ""
        self.popup_restore_unknown = False
        self.identity = {}
        self.popup_diag = {}
        self.draw_stats = {}
        self.draw_error = ""
        self.map_error = ""
        self.stop_stage = ""
        self.stop_reason = ""
        self.stop_detail = ""

    def _host_key(self, h):
        return "%s|%s|%s" % (h.get("pid"), h.get("ctime"), h.get("image", ""))

    def _host_status(self):
        """alive/dead/unknown for the currently bound host (not the request)."""
        if not self.host:
            return "dead"
        # Injected pure-state validator (tests/embedding): None means dead for
        # that finite fake world. Production keeps the tri-state Win32 path.
        if self.validate is not validate_production_host:
            try:
                return "alive" if self.validate(self.host) else "dead"
            except Exception:
                return "unknown"
        pid = int(self.host.get("pid") or 0)
        try:
            actual = _proc_identity(pid)
        except HostGone:
            return "dead"
        except Exception:
            return "unknown"
        if actual is None:
            return "unknown"
        try:
            if (int(actual.get("ctime") or 0)
                    != int(self.host.get("ctime") or 0)
                    or str(actual.get("image") or "").lower()
                    != str(self.host.get("image") or "").lower()):
                return "dead"
        except Exception:
            return "unknown"
        return "alive"

    def _host_alive(self):
        return self._host_status() == "alive"

    def status(self, runtime_pid, runtime_ctime, ready=True):
        return {
            "protocol": PROTOCOL, "control_version": CONTROL_VERSION,
            "control_ready": bool(ready), "language_ready": self.language_ready,
            "phase": self.phase, "ready": bool(ready),
            "runtime_pid": int(runtime_pid), "runtime_ctime": int(runtime_ctime),
            "host": self.host, "session": self.session,
            "gen": self.last_gen, "ack_gen": self.last_gen,
            "applied_gen": self.applied_gen,
            "mode": self.mode, "state": self.state,
            "pending": self.pending, "error": self.error,
            "rows_pending": self.rows_pending,
            "background_discovery_pending": self.background_discovery_pending,
            "popup_skipped": self.popup_skipped,
            "popup_reasons": self.popup_reasons,
            "popup_unsupported": self.popup_unsupported,
            "popup_error": self.popup_error,
            "popup_restore_unknown": bool(self.popup_restore_unknown),
            "draw_stats": self.draw_stats, "draw_error": self.draw_error,
            "identity": self.identity, "popup_diag": self.popup_diag,
            "map_error": self.map_error,
            "stop_stage": self.stop_stage, "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
            "session_lost": self.session_lost,
            "request_id": self.request_id, "updated": time.time(),
        }

    def mark_session_lost(self):
        if self.host and self._host_status() == "dead":
            self.session_lost = True
            self.mode = "off"
            self.state = "session-lost"
            self.error = "host-exited"
            self.language_ready = False
            self.phase = "control"
            self.pending = False
            self.rows_pending = False
            self.background_discovery_pending = False

    def handle(self, req):
        """Return (accepted, error, stop_requested). Rejections never mutate state."""
        self.idempotent = False
        if not isinstance(req, dict):
            return False, "request", False
        if req.get("protocol") != PROTOCOL:
            return False, "protocol", False
        mode = req.get("mode")
        if mode not in ("on", "off", "init", "stop"):
            return False, "mode", False
        # STOP is allowed without a live AE host.
        if mode == "stop":
            self.mode = "stop"
            self.state = "stopped"
            self.error = ""
            self.request_id = str(req.get("request_id") or "")
            self.language_ready = False
            self.pending = False
            self.phase = "control"
            self.rows_pending = False
            self.background_discovery_pending = False
            return True, "", True
        gen = req.get("gen")
        if type(gen) is not int or gen <= 0:
            return False, "gen", False
        actual = self.validate(req.get("host"))
        if not actual:
            return False, "host", False
        key = self._host_key(actual)
        if self.host and key != self.session:
            # Only replace a host after the old one is proven dead.
            _st = self._host_status()
            if _st == "alive":
                return False, "host-busy", False
            if _st == "unknown":
                return False, "host-unknown", False
            self.host = None
            self.session = ""
            self.session_lost = True
            self.mode = "off"
            self.state = "session-lost"
        if not self.host:
            self.host = actual
            self.session = key
            self.session_lost = False
            self.error = ""
        if gen < self.last_gen:
            return False, "stale-gen", False
        if gen == self.last_gen:
            if mode != self.mode:
                return False, "same-gen-mode", False
            # identical host/intent/gen: idempotent ack; do not clear ready or
            # trigger another full dictionary/scan pass.
            self.idempotent = True
            return True, "", False
        # Commit only after all validation passed.
        self.last_gen = gen
        self.mode = mode
        self.state = "control-acked"
        self.error = ""
        self.request_id = str(req.get("request_id") or "")
        self.language_ready = False
        self.phase = "language-pending"
        self.pending = (mode in ("on", "off"))
        self.rows_pending = False
        self.background_discovery_pending = False
        return True, "", False


def _mutex(data_root):
    """Process-lifetime named mutex keyed by DATA_ROOT; second process exits."""
    try:
        import hashlib

        name = "Global\\AeLocalizerManagedGuard-" + hashlib.sha256(
            os.path.abspath(data_root).encode("utf-8")).hexdigest()[:24]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k.CreateMutexW.restype = ctypes.c_void_p
        h = k.CreateMutexW(None, 0, name)
        if not h or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return h
    except Exception:
        return None


def main(argv):
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CTL, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    # 上一个守护会话的采集文件必须清掉：补词页一旦读到旧快照，就会把已经
    # 关掉的 AE 见过的词当成当前状态（2026-11-02 用户实测过这个坑）。
    for _stale in ("all_param_names.json", "renamed_names.json",
                   "rename_miss.json", "enum_missing.json"):
        try:
            os.remove(os.path.join(OUT, _stale))
        except OSError:
            pass
    mtx = _mutex(DATA_ROOT)
    if not mtx:
        return 4
    own = _proc_identity(os.getpid())
    if not own or not own.get("ctime"):
        return 3
    token = "%s|%s|%s" % (os.getpid(), own["ctime"], uuid.uuid4().hex)
    # Own locks: integer PID for engine compatibility + sidecar identity/token.
    try:
        with open(LOCK, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        _atomic_json(LOCK_ID, {"protocol": PROTOCOL, "pid": own["pid"],
                               "ctime": own["ctime"], "image": own["image"],
                               "token": token, "created": time.time()})
    except OSError:
        return 2
    state = ControlState()
    lang = LanguageEngine(PROJECT_ROOT, DATA_ROOT, DICT)
    effect_owner = None
    effect_server = None
    try:
        if PROJECT_ROOT and PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        from core.effect_broker import BrokerServer
        from effect_owner import EffectOwner

        effect_owner = EffectOwner(DATA_ROOT, PROJECT_ROOT,
                                   redraw=_redraw_host,
                                   log=_log,
                                   check_host=_validate_effect_host,
                                   host_status=_effect_host_status)
        effect_server = BrokerServer(DATA_ROOT, effect_owner.handle, log=_log)
        if not effect_server.start():
            effect_server = None
    except Exception as exc:
        _log("effect owner unavailable: %r" % (exc,))
        effect_owner = None
        effect_server = None
    status = state.status(os.getpid(), own["ctime"])
    if not _atomic_json(ST, status):
        _log("cannot publish initial ready status")
        try:
            side = _read_json(LOCK_ID)
            if isinstance(side, dict) and side.get("token") == token:
                for p in (LOCK, LOCK_ID):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        except Exception:
            pass
        return 5
    _log("managed control ready pid=%s" % os.getpid())
    last_req = None
    last_write = 0.0
    last_lang_tick = 0.0
    last_capture = 0.0
    last_capture_rev = -1
    stopping = False
    stopped = False
    try:
        while not stopped:
            if stopping:
                # STOP is two-phase: disable writers, keep process/lock/session
                # until native draw drain and current-popup restore are safe.
                try:
                    safe = bool(lang.close())
                except Exception as exc:
                    safe = False
                    try:
                        lang._set_stop("exception", "close-exception", repr(exc))
                    except Exception:
                        pass
                _sync_language_diag(state, lang)
                host_dead = bool(state.host and state._host_status() == "dead")
                if safe or host_dead:
                    stopped = True
                    state.state = "stopped"
                    state.phase = "control"
                    break
                state.mode = "stop"
                state.state = "stopping"
                state.phase = "stopping"
                state.pending = True
                status = state.status(os.getpid(), own["ctime"])
                _atomic_json(ST, status)
                time.sleep(0.15)
                continue
            if os.path.exists(STOP_FLAG):
                try:
                    os.remove(STOP_FLAG)
                except OSError:
                    pass
                stopping = True
                state.mode = "stop"
                state.state = "stopping"
                state.phase = "stopping"
                state.language_ready = False
                state.pending = True
                state.rows_pending = False
                state.background_discovery_pending = False
                _atomic_json(ST, state.status(os.getpid(), own["ctime"]))
                continue
            was_lost = state.session_lost
            state.mark_session_lost()
            if state.session_lost and not was_lost:
                lang.close()
            req = _read_json(REQ)
            if isinstance(req, dict) and req != last_req:
                last_req = req
                accepted, err, do_stop = state.handle(req)
                if do_stop:
                    stopping = True
                    state.mode = "stop"
                    state.state = "stopping"
                    state.phase = "stopping"
                    state.language_ready = False
                    state.pending = True
                if not accepted:
                    state.error = err
                    state.state = "rejected"
                    _log("reject gen=%r mode=%r err=%s"
                         % (req.get("gen"), req.get("mode"), err))
                else:
                    state.error = ""
                    _log("accept gen=%r mode=%s" % (req.get("gen"), state.mode))
                    if (not state.idempotent and state.mode in ("on", "off")
                            and state.host):
                        ok, lerr = lang.apply(state.mode, state.host,
                                              state.last_gen)
                        last_lang_tick = time.monotonic()
                        _sync_language_diag(state, lang)
                        state.language_ready = bool(ok)
                        state.popup_skipped = int(lang.popup_unsupported)
                        state.popup_reasons = dict(lang.popup_reasons)
                        state.popup_unsupported = _popup_capacity_skips(lang.popup_reasons)
                        try:
                            state.draw_stats = lang.draw.stats() if lang.draw else {}
                        except Exception as _de:
                            state.draw_stats = {}
                            state.draw_error = str(_de)[:160]
                        if getattr(lang, "draw_error", ""):
                            state.draw_error = lang.draw_error
                        state.map_error = getattr(lang, "map_error", "")
                        state.phase = ("language-applied" if ok
                                       else "language-syncing")
                        state.pending = not ok
                        if ok:
                            state.applied_gen = state.last_gen
                            state.error = ""
                        else:
                            state.error = lerr
                            _log("language apply failed: %s" % lerr)
                status = state.status(os.getpid(), own["ctime"])
                _atomic_json(ST, status)
                last_write = time.monotonic()
            else:
                nowm = time.monotonic()
                if ((state.mode == "on"
                     or (state.mode in ("on", "off") and not state.language_ready)
                     or (state.mode == "off"
                         and state.background_discovery_pending))
                        and state.host and nowm - last_lang_tick >=
                        _language_poll_interval(state.mode, lang.popup)):
                    if state.language_ready:
                        ok, lerr = lang.refresh(state.mode)
                    else:
                        ok, lerr = lang.apply(state.mode, state.host,
                                              state.last_gen)
                    last_lang_tick = time.monotonic()
                    _sync_language_diag(state, lang)
                    state.language_ready = bool(ok)
                    state.phase = ("language-applied" if ok
                                   else "language-syncing")
                    state.pending = not ok
                    state.popup_skipped = int(lang.popup_unsupported)
                    state.popup_reasons = dict(lang.popup_reasons)
                    state.popup_unsupported = _popup_capacity_skips(lang.popup_reasons)
                    try:
                        state.draw_stats = lang.draw.stats() if lang.draw else {}
                    except Exception as _de:
                        state.draw_stats = {}
                        state.draw_error = str(_de)[:160]
                    if getattr(lang, "draw_error", ""):
                        state.draw_error = lang.draw_error
                    state.map_error = getattr(lang, "map_error", "")
                    if ok:
                        state.applied_gen = state.last_gen
                        state.error = ""
                    else:
                        state.error = lerr
                    status = state.status(os.getpid(), own["ctime"])
                    _atomic_json(ST, status)
                    last_write = nowm
                elif nowm - last_write >= 1.0:
                    status = state.status(os.getpid(), own["ctime"])
                    _atomic_json(ST, status)
                    try:
                        os.utime(LOCK, None)
                        os.utime(LOCK_ID, None)
                    except OSError:
                        pass
                    last_write = nowm
            # 补词采集落盘：只在观察内容真的变了（或 GUI 要求立刻落盘）时写，
            # 不参与 native 绘制路径，也不阻塞控制循环。
            # 用独立时间戳：nowm 只在下面的 else 分支里定义。
            capm = time.monotonic()
            forced = False
            try:
                if os.path.exists(DUMP_NOW):
                    os.remove(DUMP_NOW)
                    forced = True
            except OSError:
                pass
            if forced or capm - last_capture >= 1.0:
                # 变更版本号来自 JS 侧 stats()（轻量，本来就是每秒一次的控制
                # 回读）；不能用 lang.draw_stats —— 那是 LanguageEngine 自己
                # 的空占位，从不被刷新，会让采集永远停在 rev=0。
                try:
                    rev = int((lang.draw.stats() or {}).get("seen_rev") or 0) \
                        if lang.draw is not None else 0
                except Exception:
                    rev = 0
                if forced or rev != last_capture_rev:
                    # 采集只是旁路观测：任何异常都不能把控制循环（以及正在
                    # 生效的语言会话）带崩。
                    try:
                        obs = lang.observe()
                    except Exception as _ce:
                        obs = {}
                        _log("capture observe failed: %r" % (_ce,))
                    if obs:
                        _write_capture(OUT, obs)
                        last_capture_rev = int(obs.get("rev") or rev)
                    last_capture = capm
            time.sleep(0.15)
    finally:
        # Any unexpected exception/exit path must still drain native work while
        # the host is alive. close=False is never treated as permission to exit.
        safe = False
        while not safe:
            try:
                safe = bool(lang.close())
            except Exception as exc:
                safe = False
                try:
                    lang._set_stop("exception", "close-exception", repr(exc))
                except Exception:
                    pass
            _sync_language_diag(state, lang)
            if safe:
                break
            host_dead = bool(state.host and state._host_status() == "dead")
            if host_dead:
                safe = True
                break
            state.mode = "stop"
            state.state = "stopping"
            state.phase = "stopping"
            state.pending = True
            _atomic_json(ST, state.status(os.getpid(), own["ctime"]))
            time.sleep(0.15)
        try:
            if effect_server is not None:
                while not effect_server.stop(2.0):
                    _log("effect broker still has accepted handler; "
                         "not detaching owner")
                    time.sleep(0.15)
            if effect_owner is not None:
                effect_owner.stop()
        except Exception as exc:
            _log("effect teardown failed: %r" % (exc,))
        _sync_language_diag(state, lang)
        status = state.status(os.getpid(), own["ctime"], ready=False)
        status.update({"ready": False, "state": "stopped"})
        _atomic_json(ST, status)
        try:
            side = _read_json(LOCK_ID)
            if isinstance(side, dict) and side.get("token") == token:
                for p in (LOCK, LOCK_ID):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        except Exception:
            pass
        _log("managed control exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
