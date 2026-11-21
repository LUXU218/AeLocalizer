# -*- coding: utf-8 -*-
"""Effect DisplayHook owned by the managed runtime (single Frida host)."""
from __future__ import annotations

import threading


class EffectOwner:
    def __init__(self, data_root, project_root, redraw=None, log=None,
                 check_host=None, host_status=None):
        self.data_root = data_root
        self.project_root = project_root
        self.redraw = redraw
        self.log = log or (lambda m: None)
        self.check_host = check_host
        self.host_status = host_status
        self.hook = None
        self.host = None
        self.sig = None
        self.gen = 0
        self.desired = "off"
        self.attaching = False
        self.pending_hook = None
        self.pending_host = None
        self.lock = threading.RLock()

    def _check_host(self, host):
        if not isinstance(host, dict):
            raise RuntimeError("host")
        pid = int(host.get("pid") or 0)
        ctime = int(host.get("ctime") or 0)
        image = str(host.get("image") or "")
        if not pid or not ctime or not image:
            raise RuntimeError("host")
        if self.check_host is None:
            raise RuntimeError("host-validator")
        try:
            actual = self.check_host(host)
        except Exception:
            actual = None
        if not actual:
            raise RuntimeError("host-unknown")
        if (int(actual.get("ctime") or 0) != ctime
                or str(actual.get("image") or "").lower() != image.lower()):
            raise RuntimeError("host-mismatch")
        return pid

    def _owned(self, host):
        if not self.host:
            raise RuntimeError("no-effect-owner")
        if (int(host.get("pid") or 0) != int(self.host.get("pid") or 0)
                or int(host.get("ctime") or 0) != int(self.host.get("ctime") or 0)
                or str(host.get("image") or "").lower()
                != str(self.host.get("image") or "").lower()):
            raise RuntimeError("host-owner-mismatch")
        self._check_host(host)

    def _ensure(self, req, gen):
        host = req.get("host") or {}
        pid = self._check_host(host)
        payload = req.get("payload") or {}
        sig = (pid, int(host.get("ctime") or 0), str(payload.get("sig") or ""),
               int(payload.get("table_len") or 0))
        with self.lock:
            if gen != self.gen or self.desired != "on":
                raise RuntimeError("stale-effect-gen")
            if self.attaching:
                raise RuntimeError("effect-busy")
            if (self.hook is not None and self.host
                    and int(self.host.get("pid") or 0) == pid
                    and int(self.host.get("ctime") or 0) == int(host.get("ctime") or 0)
                    and self.sig == sig and self.hook.running()):
                return {"running": True, "paused": self.hook.paused(),
                        "pid": pid, "cached": True}
            if self.hook is not None and self.host and (
                    int(self.host.get("pid") or 0) != pid
                    or int(self.host.get("ctime") or 0) != int(host.get("ctime") or 0)
                    or str(self.host.get("image") or "").lower()
                    != str(host.get("image") or "").lower()):
                st = "unknown"
                if self.host_status:
                    try:
                        st = self.host_status(self.host)
                    except Exception:
                        st = "unknown"
                if st != "dead":
                    raise RuntimeError("host-busy:%s" % st)
            if self.hook is not None:
                try:
                    self.hook.stop()
                except Exception:
                    pass
                self.hook = None
                self.host = None
                self.sig = None
            from core.live_hook import DisplayHook

            hook = DisplayHook()
            hook.on_patch = (lambda: self._redraw(pid))
            self.attaching = True
            self.pending_hook = hook
            self.pending_host = dict(host)
        try:
            ok = hook.ensure(
                pid, payload.get("table") or {},
                payload.get("real_names") or (),
                payload.get("ui_title_ret") or 0)
        except Exception:
            # Keep attaching=True across the cleanup barrier so a new ensure
            # cannot attach while the old script is still unloading.
            try:
                hook.stop()
            except Exception:
                pass
            with self.lock:
                self.attaching = False
                self.pending_hook = None
                self.pending_host = None
            raise
        with self.lock:
            stale = (gen != self.gen or self.desired != "on")
            if ok and not stale:
                self.attaching = False
                self.pending_hook = None
                self.pending_host = None
                self.hook = hook
                self.host = dict(host)
                self.sig = sig
                return {"running": True, "paused": hook.paused(),
                        "pid": pid, "cached": False,
                        "stats": dict(getattr(hook, "last_stats", {}) or {})}
        # stale/failed: retain attaching barrier until unload completes.
        try:
            hook.stop()
        except Exception:
            pass
        with self.lock:
            self.attaching = False
            self.pending_hook = None
            self.pending_host = None
        if not ok:
            raise RuntimeError("ensure-failed:%s" %
                               (getattr(hook, "last_error", "") or ""))
        raise RuntimeError("stale-effect-gen")

    def _redraw(self, pid):
        if self.redraw:
            try:
                self.redraw(pid)
            except Exception:
                pass

    def _same_host(self, a, b):
        return bool(a and b
                    and int(a.get("pid") or 0) == int(b.get("pid") or 0)
                    and int(a.get("ctime") or 0) == int(b.get("ctime") or 0)
                    and str(a.get("image") or "").lower()
                    == str(b.get("image") or "").lower())

    def handle(self, req):
        op = str(req.get("op") or "")
        if op not in ("ensure", "pause", "resume", "stop", "ping", "stats",
                      "revert", "revert_addrs", "setreal", "invalidate"):
            raise RuntimeError("op")
        gen = int(req.get("gen") or 0)
        host = req.get("host") or {}
        if op == "ensure":
            # Validate the requested host and existing owner BEFORE committing
            # gen/desired. A rejected old-host request must not cancel a newer
            # accepted attach or mutate the owner generation.
            self._check_host(host)
            with self.lock:
                if gen < self.gen:
                    raise RuntimeError("stale-effect-gen")
                if (self.hook is not None and self.host
                        and not self._same_host(self.host, host)):
                    st = self.host_status(self.host) if self.host_status else "unknown"
                    if st != "dead":
                        raise RuntimeError("host-busy:%s" % st)
                if self.attaching and not self._same_host(self.pending_host, host):
                    raise RuntimeError("effect-busy")
                if gen > self.gen:
                    self.gen = gen
                self.desired = "on"
            return self._ensure(req, gen)
        with self.lock:
            if op == "ping":
                if self.hook is None:
                    return {"running": False, "pid": 0}
                self._owned(host)
                return {"running": bool(self.hook.running()),
                        "pid": int((self.host or {}).get("pid") or 0)}
            if op == "stats":
                # read-only: never mutates gen/desired
                if gen < self.gen:
                    raise RuntimeError("stale-effect-gen")
                if self.hook is None:
                    return {"ok": False, "paused": False, "pid": 0,
                            "panes": 0, "script_error": "no-effect-owner"}
                self._owned(host)
                st = {}
                try:
                    st.update(dict(self.hook.stats() or {}))
                except Exception as exc:
                    st["stats_error"] = str(exc)[:160]
                st.update({
                    "ok": bool(self.hook.running()),
                    "paused": bool(self.hook.paused()),
                    "pid": int((self.host or {}).get("pid") or 0),
                    "panes": int(self.hook.panes()),
                    "last_error": str(getattr(self.hook, "last_error", "")),
                    "script_error": str(getattr(self.hook, "last_error", "")),
                    "script_errors": int(getattr(self.hook,
                                                  "last_error_count", 0)),
                })
                return st
            # Mutating ops: validate host/ownership/pending identity first,
            # then atomically commit generation/desired.
            if self.hook is not None and self.host:
                self._owned(host)
            elif self.attaching:
                if not self._same_host(self.pending_host, host):
                    raise RuntimeError("host-owner-mismatch")
                self._check_host(host)
            else:
                self._check_host(host)
            if gen < self.gen:
                raise RuntimeError("stale-effect-gen")
            if gen > self.gen:
                self.gen = gen
            if op in ("pause", "stop"):
                self.desired = "off"
            if op == "stop":
                if self.attaching:
                    return {"stopped": False, "pending": "attaching"}
                if self.hook is None:
                    return {"stopped": True}
                try:
                    self.hook.stop()
                finally:
                    self.hook = None
                    self.host = None
                    self.sig = None
                return {"stopped": True}
            if op == "pause":
                if self.hook is None and not self.attaching:
                    return {"ok": True, "no-owner": True}
                if self.attaching:
                    # Don't pretend OFF completed while an ensure is in flight;
                    # its post-attach gen check will stop/discard it.
                    return {"ok": False, "pending": "attaching"}
                return {"ok": bool(self.hook and self.hook.pause())}
            if op == "resume":
                return {"ok": bool(self.hook and self.hook.resume())}
            if op == "revert":
                return {"count": int(self.hook.revert_now() if self.hook else 0)}
            if op == "revert_addrs":
                return {"result": dict(self.hook.revert_addrs(
                    (req.get("payload") or {}).get("addrs") or [])
                    if self.hook else {})}
            if op == "setreal":
                names = (req.get("payload") or {}).get("names") or []
                return {"ok": bool(self.hook and self.hook.set_real_names(names))}
            if op == "invalidate":
                return {"count": int(self.hook.invalidate() if self.hook else 0)}
            raise RuntimeError("op")

    def stop(self):
        with self.lock:
            if self.hook is not None:
                try:
                    self.hook.stop()
                except Exception:
                    pass
                self.hook = None
                self.host = None
                self.sig = None
