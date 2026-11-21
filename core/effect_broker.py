# -*- coding: utf-8 -*-
"""Authenticated local AF_PIPE broker for the single Frida host.

Only one process (managed runtime) may call frida.attach.  The GUI sends
effect-hook operations to that owner over a random local named pipe protected
by a 32-byte authkey.  No TCP listener, no external service, no eval.
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import threading
import time
import uuid
from multiprocessing.connection import Client, Listener


def own_process_identity(pid: int):
    """Generic Win32 identity for the broker's own Python process (not AE)."""
    from ctypes import wintypes as W

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(W.FILETIME)] * 4
    h = k.OpenProcess(0x00100000 | 0x1000, False, int(pid))
    if not h:
        raise RuntimeError("OpenProcess %s" % ctypes.get_last_error())
    try:
        c, e, ke, u = (W.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                 ctypes.byref(ke), ctypes.byref(u)):
            raise RuntimeError("GetProcessTimes")
        return (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def info_path(data_root: str) -> str:
    return os.path.join(data_root, "guard_runtime", "effect.pipe.json")


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%s.%s" % (path, os.getpid(), uuid.uuid4().hex)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for i in range(20):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.02)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


class BrokerServer:
    def __init__(self, data_root: str, handler, log=None):
        self.data_root = data_root
        self.handler = handler
        self.log = log or (lambda m: None)
        self.address = ""
        self.authkey = b""
        self.pid = os.getpid()
        self.ctime = 0
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._stopping = False
        self._handlers = set()
        self._handlers_lock = threading.Lock()
        self._token = uuid.uuid4().hex

    def start(self) -> bool:
        try:
            self.authkey = secrets.token_bytes(32)
            self.address = r"\\.\pipe\AeLocalizerEffect-%s-%s" % (
                os.getpid(), secrets.token_hex(8))
            self._listener = Listener(self.address, family="AF_PIPE",
                                      authkey=self.authkey)
            try:
                self.ctime = int(own_process_identity(os.getpid()) or 0)
            except Exception:
                self.ctime = 0
            if not _atomic_json(info_path(self.data_root), {
                    "schema": 1, "address": self.address,
                    "authkey": self.authkey.hex(), "pid": self.pid,
                    "ctime": self.ctime, "token": self._token}):
                self.stop()
                return False
            self._thread = threading.Thread(target=self._serve, daemon=True,
                                            name="effect-broker")
            self._thread.start()
            self.log("effect broker ready %s" % self.address)
            return True
        except Exception as exc:
            self.log("effect broker start failed: %r" % (exc,))
            self.stop()
            return False

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except Exception:
                if self._stop.is_set():
                    return
                time.sleep(0.05)
                continue
            if self._stopping:
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            t = threading.Thread(target=self._handle_conn, args=(conn,),
                                 daemon=True, name="effect-broker-rpc")
            with self._handlers_lock:
                self._handlers.add(t)
            t.start()

    def _handle_conn(self, conn):
        try:
            if not conn.poll(2.0):
                return
            req = conn.recv()
            if (not isinstance(req, dict) or req.get("schema") != 1
                    or req.get("broker_token") != self._token
                    or self._stopping):
                conn.send({"schema": 1, "ok": False,
                           "error": "stale-broker"})
                return
            try:
                result = self.handler(req)
                conn.send({"schema": 1, "ok": True, "request_id":
                           str(req.get("request_id") or ""),
                           "result": result})
            except Exception as exc:
                conn.send({"schema": 1, "ok": False, "request_id":
                           str(req.get("request_id") or ""),
                           "error": str(exc)[:240]})
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            with self._handlers_lock:
                self._handlers.discard(threading.current_thread())

    def quiesced(self, timeout: float = 2.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._handlers_lock:
                active = [t for t in self._handlers if t.is_alive()]
            if not active:
                return True
            time.sleep(0.02)
        return False

    def stop(self, timeout: float = 2.0) -> bool:
        self._stopping = True
        self._stop.set()
        try:
            if self._listener is not None:
                self._listener.close()
        except Exception:
            pass
        self._listener = None
        # Reject new work immediately; do not remove the owner/session until
        # every accepted handler has returned.
        try:
            p = info_path(self.data_root)
            with open(p, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict) and old.get("token") == self._token:
                os.remove(p)
        except Exception:
            pass
        ok = self.quiesced(timeout)
        self.log("effect broker stopped quiesced=%s" % ok)
        return ok


class BrokerClient:
    def __init__(self, data_root: str, timeout: float = 2.0):
        self.data_root = data_root
        self.timeout = float(timeout)
        self._inflight = False
        self._inflight_lock = threading.Lock()

    def _info(self):
        with open(info_path(self.data_root), encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict) or obj.get("schema") != 1:
            raise RuntimeError("broker-info")
        return obj

    def call(self, op: str, payload=None, host=None, gen=0):
        with self._inflight_lock:
            if self._inflight:
                raise RuntimeError("effect-rpc-busy")
            self._inflight = True
        try:
            return self._call_locked(op, payload, host, gen)
        finally:
            # A timed-out worker keeps this True until its real IO ends; this
            # prevents unbounded daemon creation on repeated retries.
            pass

    def _call_locked(self, op: str, payload=None, host=None, gen=0):
        try:
            info = self._info()
        except Exception:
            with self._inflight_lock:
                self._inflight = False
            raise
        req = {"schema": 1, "op": str(op), "request_id": uuid.uuid4().hex,
               "broker_token": str(info.get("token") or ""),
               "gen": int(gen or 0),
               "payload": payload if payload is not None else {},
               "host": host or {}}
        deadline = time.monotonic() + self.timeout
        out = {}
        done = threading.Event()
        cancelled = threading.Event()

        def worker():
            conn = None
            try:
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("effect broker timeout before send")
                conn = Client(info["address"], family="AF_PIPE",
                              authkey=bytes.fromhex(info["authkey"]))
                # Auth/connect may have consumed the whole deadline. Never
                # send a stale ON after the caller already timed out.
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("effect broker timeout before send")
                conn.send(req)
                left = max(0.0, deadline - time.monotonic())
                if not conn.poll(left):
                    raise TimeoutError("effect broker timeout")
                res = conn.recv()
                if not isinstance(res, dict) or not res.get("ok"):
                    raise RuntimeError(str(res.get("error") or "broker-error")[:200])
                if str(res.get("request_id") or "") != req["request_id"]:
                    raise RuntimeError("stale effect response")
                out["result"] = res.get("result")
            except Exception as exc:
                out["error"] = exc
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                with self._inflight_lock:
                    self._inflight = False
                done.set()

        t = threading.Thread(target=worker, daemon=True,
                             name="effect-rpc-%s" % op)
        try:
            t.start()
        except Exception:
            with self._inflight_lock:
                self._inflight = False
            raise
        if not done.wait(max(0.0, deadline - time.monotonic())):
            cancelled.set()
            raise TimeoutError("effect broker timeout")
        if "error" in out:
            raise out["error"]
        return out.get("result")
