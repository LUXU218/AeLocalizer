# -*- coding: utf-8 -*-
"""GUI-side proxy for the effect DisplayHook owned by the managed runtime."""
from __future__ import annotations

import threading
import time

from .effect_broker import BrokerClient

_GEN_LOCK = threading.Lock()
_GEN_FLOOR = 0


def _next_wire_gen():
    """Process-wide floor; Windows monotonic_ns can be coarse (15.6ms)."""
    global _GEN_FLOOR
    with _GEN_LOCK:
        _GEN_FLOOR = max(int(time.monotonic_ns()), int(_GEN_FLOOR) + 1)
        return _GEN_FLOOR


class DisplayHookProxy:
    """Same surface as core.live_hook.DisplayHook, but no local frida.attach."""

    def __init__(self, data_root: str, timeout: float = 2.0):
        self._client = BrokerClient(data_root, timeout=timeout)
        self._running = False
        self._paused = False
        self._pid = 0
        self._panes = 0
        self._sig = None
        self._host = None
        self._local_gen = -1
        self._gen = 0       # monotonic cross-GUI wire generation
        self.last_error = ""
        self.on_patch = None
        self.on_ra = None

    def _map_gen(self, local):
        if local is None:
            return True
        local = int(local)
        if local < self._local_gen:
            # Never upgrade an old local intent (e.g. stale OFF) to the current
            # wire gen; reject it instead.
            self.last_error = "stale-local-gen"
            return False
        if local == self._local_gen:
            return True
        self._local_gen = local
        self._gen = _next_wire_gen()
        return True

    def _call(self, op, payload=None, host=None, gen=None):
        if not self._map_gen(gen):
            return None
        try:
            res = self._client.call(
                op, payload=payload,
                host=host if host is not None else self._host,
                gen=self._gen)
            self.last_error = ""
            return res
        except Exception as exc:
            self.last_error = str(exc)[:200]
            self._running = False
            return None

    def _host_for(self, pid):
        import os
        from core import proc

        for p in proc.list_ae_processes():
            if int(p.get("pid") or 0) == int(pid):
                return {"pid": int(pid),
                        "ctime": int(p.get("creation_time") or 0),
                        "image": os.path.abspath(str(p.get("image") or ""))}
        return None

    def ensure(self, pid, mapping, real_names=None, ui_title_ret=None,
               host=None, sig=None, gen=None):
        if not self._map_gen(gen):
            return False
        host = host or self._host_for(pid)
        if not host:
            self.last_error = "host-missing"
            self._running = False
            return False
        if (sig is not None and sig == self._sig and self._running
                and self._host == host):
            p = self._call("ping")
            if p is not None and p.get("running") and int(p.get("pid") or 0) == int(pid):
                return True
            self._running = False
        res = self._call("ensure", {
            "table": dict(mapping or {}),
            "real_names": list(real_names or ()),
            "ui_title_ret": int(ui_title_ret or 0),
            "table_len": len(mapping or {}),
            "sig": str(sig if sig is not None else ""),
        }, host=host)
        if not res or not res.get("running"):
            # Negative ack: do not publish a new owner/signature or state.
            self.last_error = str((res or {}).get("error") or "ensure-negative")
            return False
        self._host = host
        self._running = True
        self._paused = bool(res.get("paused"))
        self._pid = int(pid)
        self._sig = sig if sig is not None else None
        return True

    def stop(self, gen=None):
        if not self._map_gen(gen):
            return False
        res = self._call("stop", host=self._host or {})
        if not res or not res.get("stopped"):
            return False
        self._running = False
        self._paused = False
        self._pid = 0
        self._sig = None
        return True

    def pause(self, gen=None):
        if not self._map_gen(gen):
            return False
        res = self._call("pause", host=self._host or {})
        if not res or not res.get("ok"):
            return False
        self._paused = True
        return True

    def resume(self, gen=None):
        if not self._map_gen(gen):
            return False
        res = self._call("resume", host=self._host or {})
        if not res or not res.get("ok"):
            return False
        self._paused = False
        self._running = True
        return True

    def stats(self):
        res = self._call("stats")
        if res is None:
            return {}
        self._running = bool(res.get("ok"))
        self._paused = bool(res.get("paused"))
        self._panes = int(res.get("panes") or 0)
        return res

    def revert_now(self):
        res = self._call("revert")
        return int((res or {}).get("count") or 0)

    def revert_addrs(self, addrs):
        res = self._call("revert_addrs", {"addrs": list(addrs or [])})
        return dict((res or {}).get("result") or {})

    def set_real_names(self, names):
        res = self._call("setreal", {"names": list(names or ())})
        return bool((res or {}).get("ok"))

    def invalidate(self):
        res = self._call("invalidate")
        return int((res or {}).get("count") or 0)

    def running(self):
        return bool(self._running)

    def paused(self):
        return bool(self._paused)

    def pid(self):
        return int(self._pid)

    def panes(self):
        return int(self._panes)
