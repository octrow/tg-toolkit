"""The load-bearing invariant: nothing imports telethon at import time.

``tg_toolkit.push`` must be usable on a machine with no telethon installed
(chat-watch only wants Bot API push), and ``tg_toolkit.export`` is pure. We
simulate absence by blocking the name in the import system and re-importing the
package from scratch in a subprocess-free way.
"""

from __future__ import annotations

import importlib
import sys

import pytest


class BlockTelethon:
    """A meta-path finder that makes any ``telethon`` import raise ImportError."""

    def find_module(self, name, path=None):  # legacy API, harmless
        return None

    def find_spec(self, name, path=None, target=None):
        if name == "telethon" or name.startswith("telethon."):
            raise ImportError(f"No module named {name!r} (blocked by test)")
        return None


@pytest.fixture
def no_telethon(monkeypatch):
    blocker = BlockTelethon()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    for name in [n for n in sys.modules if n == "telethon" or n.startswith("telethon.")]:
        monkeypatch.delitem(sys.modules, name)
    for name in [n for n in sys.modules if n == "tg_toolkit" or n.startswith("tg_toolkit.")]:
        monkeypatch.delitem(sys.modules, name)
    yield
    importlib.invalidate_caches()


def test_telethon_is_really_blocked(no_telethon):
    with pytest.raises(ImportError):
        importlib.import_module("telethon")


def test_push_imports_and_works_without_telethon(no_telethon):
    mod = importlib.import_module("tg_toolkit.push")
    assert mod.scrub_token("bot42:secret") == "bot***"


def test_export_is_pure_and_imports_without_telethon(no_telethon):
    mod = importlib.import_module("tg_toolkit.export")
    assert mod.flatten_text(["a", {"text": "b"}]) == "ab"


def test_the_package_and_reader_import_without_telethon(no_telethon):
    """Only *calling* a client function may need telethon, never importing."""
    pkg = importlib.import_module("tg_toolkit")
    assert pkg.last_seen_msg_id("telegram:x:5") == 5
    reader = importlib.import_module("tg_toolkit.reader")
    assert reader.scan_decision(None, 0) == reader.TAKE


def test_make_client_fails_loudly_only_when_called(no_telethon):
    reader = importlib.import_module("tg_toolkit.reader")
    with pytest.raises(ImportError):
        reader.make_client(1, "h", "s")
