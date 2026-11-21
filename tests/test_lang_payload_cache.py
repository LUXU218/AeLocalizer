# -*- coding: utf-8 -*-
"""Full language payload cache and real channel-mutation semantics."""
import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QObject

from app.backend import AppBackend
from core.inventory import CH_ENUM, CH_PARAM


class _Store:
    def __init__(self):
        self.revision = 1
        self.calls = 0

    def resolve(self):
        self.calls += 1
        return {"A": SimpleNamespace(zh="中"), "B": SimpleNamespace(zh="文")}


class _Group:
    def __init__(self, gid="g1", channels=None):
        self.group_id = gid
        self.selected = True
        self.channels = set(channels if channels is not None
                            else (CH_PARAM, CH_ENUM))


def _publish(path, entries):
    blob = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    return SimpleNamespace(payload_sha256=hashlib.sha256(blob).hexdigest())


def _backend(tmp, groups):
    path = os.path.join(tmp, "extra_overrides.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    b = AppBackend.__new__(AppBackend)
    QObject.__init__(b)
    b._inv = type("I", (), {"selected": lambda self: list(groups)})()
    b._store = _Store()
    b._attrib = None
    b._attribLoaded = False
    b._keepUnowned = True
    b._langPayloadSig = None
    b._live_watch_log = lambda *a, **k: None
    b._send_language_control = lambda: True
    return b, path


class LangCacheTest(unittest.TestCase):
    def test_same_group_channels_off_on_retains_file_and_cache(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_langcache_")
        g = _Group()
        b, path = _backend(tmp, [g])
        b.channelState = lambda: [False, True, True]
        prep_contents = []
        with mock.patch("app.backend.engine.ENGINE_DICT_DIR", tmp), \
             mock.patch("app.backend.QTimer.singleShot"):
            def prep(entries, **kwargs):
                prep_contents.append(dict(entries))
                return _publish(path, entries)
            with mock.patch("app.backend.engine.prepare_dict",
                            side_effect=prep) as p, \
                 mock.patch("app.backend.engine.request_reload",
                            return_value=True):
                self.assertTrue(b._reload_engine_channels(True))
                self.assertEqual(p.call_count, 1)
                first = open(path, encoding="utf-8").read()
                # public setAllChannels does exactly this: mutate same group.
                g.channels = set()
                b.channelState = lambda: [False, False, False]
                self.assertTrue(b._reload_engine_channels(True))
                self.assertEqual(p.call_count, 1)
                self.assertEqual(open(path, encoding="utf-8").read(), first)
                # Restore channels and ON: trusted full payload still valid.
                g.channels = {CH_PARAM, CH_ENUM}
                b.channelState = lambda: [False, True, True]
                self.assertTrue(b._reload_engine_channels(True))
                self.assertEqual(p.call_count, 1)
                self.assertEqual(b._store.calls, 1)
                self.assertEqual(open(path, encoding="utf-8").read(), first)

    def test_actual_empty_selection_publishes_empty(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_langcache_empty_")
        b, path = _backend(tmp, [])
        b.channelState = lambda: [False, True, True]
        with mock.patch("app.backend.engine.ENGINE_DICT_DIR", tmp), \
             mock.patch("app.backend.QTimer.singleShot"), \
             mock.patch("app.backend.engine.prepare_dict") as p:
            p.side_effect = lambda entries, **k: _publish(path, entries)
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 1)
            self.assertEqual(json.loads(open(path, encoding="utf-8").read()), {})
            b.channelState = lambda: [False, False, False]
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 1)

    def test_foreign_publish_between_build_and_prime_not_trusted(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_langcache_foreign_")
        g = _Group()
        b, path = _backend(tmp, [g])
        b.channelState = lambda: [False, True, True]
        with mock.patch("app.backend.engine.ENGINE_DICT_DIR", tmp), \
             mock.patch("app.backend.QTimer.singleShot"), \
             mock.patch("app.backend.engine.prepare_dict") as p, \
             mock.patch("app.backend.engine.request_reload",
                        return_value=True):
            def prep(entries, **k):
                meta = _publish(path, entries)
                _publish(path, {"foreign": "publisher"})
                return meta
            p.side_effect = prep
            self.assertTrue(b._reload_engine_channels(True))
            self.assertIsNone(b._langPayloadSig)
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 2)

    def test_store_revision_changes_during_resolve_not_primed(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_langcache_rev_")
        g = _Group()
        b, path = _backend(tmp, [g])
        b.channelState = lambda: [False, True, True]
        original = b._store.resolve

        def resolve_then_change():
            out = original()
            b._store.revision += 1
            return out
        b._store.resolve = resolve_then_change
        with mock.patch("app.backend.engine.ENGINE_DICT_DIR", tmp), \
             mock.patch("app.backend.QTimer.singleShot"), \
             mock.patch("app.backend.engine.prepare_dict") as p, \
             mock.patch("app.backend.engine.request_reload",
                        return_value=True):
            p.side_effect = lambda entries, **k: _publish(path, entries)
            self.assertTrue(b._reload_engine_channels(True))
            self.assertIsNone(b._langPayloadSig)
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 2)

    def test_changes_while_off_rebuild_on_next_on(self):
        tmp = tempfile.mkdtemp(prefix="aeloc_langcache_changes_")
        g = _Group()
        b, path = _backend(tmp, [g])
        b.channelState = lambda: [False, True, True]
        with mock.patch("app.backend.engine.ENGINE_DICT_DIR", tmp), \
             mock.patch("app.backend.QTimer.singleShot"), \
             mock.patch("app.backend.engine.prepare_dict") as p, \
             mock.patch("app.backend.engine.request_reload",
                        return_value=True):
            p.side_effect = lambda entries, **k: _publish(path, entries)
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 1)
            g.channels = set()
            b.channelState = lambda: [False, False, False]
            self.assertTrue(b._reload_engine_channels(True))
            b._store.revision += 1
            g.channels = {CH_PARAM, CH_ENUM}
            b.channelState = lambda: [False, True, True]
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 2)
            # Attrib change while OFF is deferred until next ON.
            b._attrib = object()
            b._attribLoaded = True
            g.channels = set()
            b.channelState = lambda: [False, False, False]
            self.assertTrue(b._reload_engine_channels(True))
            g.channels = {CH_PARAM, CH_ENUM}
            b.channelState = lambda: [False, True, True]
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 3)
            # External file change invalidates the ON cache.
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"ext":1}')
            self.assertTrue(b._reload_engine_channels(True))
            self.assertEqual(p.call_count, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
