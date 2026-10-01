"""Read-loop mechanics: cursor parsing, scan verdicts, FloodWait pacing.

No telethon client, no network: pure functions are called directly and every
client interaction goes through the fakes.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import types

import pytest
from fakes import Channel, Dialog, FakeClient, FakeFloodWaitError, FloodingClient, Message, User

from tg_toolkit import reader
from tg_toolkit.reader import (
    SKIP,
    STOP,
    TAKE,
    collect,
    dialogs_by_name,
    iter_flood_safe,
    last_seen_msg_id,
    resolve_credentials,
    resolve_entity,
    scan_decision,
    scan_messages,
    window_cutoff,
)

UTC = dt.timezone.utc


def at(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 1, day, hour, tzinfo=UTC)


def msgs(*ids_days):
    """Newest-first messages: pass (id, day) pairs."""
    return [Message(id=i, text=f"m{i}", date=at(d)) for i, d in ids_days]


def test_importing_reader_does_not_import_telethon():
    assert "telethon" not in reader.__dict__


# --- credentials -------------------------------------------------------------

def test_explicit_credentials_win_and_are_cached(tmp_path):
    cache = tmp_path / "sub" / ".telegram.json"
    assert resolve_credentials("123", "hash", cache_path=cache, env={}) == (123, "hash")
    # cached for next time, even though the parent dir did not exist
    assert resolve_credentials(None, None, cache_path=cache, env={}) == (123, "hash")


def test_env_is_the_last_resort(tmp_path):
    env = {"TG_API_ID": "9", "TG_API_HASH": "h"}
    assert resolve_credentials(cache_path=tmp_path / "none.json", env=env) == (9, "h")


def test_missing_credentials_raise_actionable_error():
    with pytest.raises(RuntimeError, match="my.telegram.org"):
        resolve_credentials(env={})


def test_credentials_without_cache_path_do_not_touch_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_credentials(5, "h", env={}) == (5, "h")
    assert list(tmp_path.iterdir()) == []


# --- incremental cursor ------------------------------------------------------

@pytest.mark.parametrize("cursor,expected", [
    (None, 0),
    ("", 0),
    (0, 0),
    (42, 42),
    ("42", 42),
    ("telegram:afisha:1234", 1234),
    ("garbage", 0),          # corrupt cursor must not stop collection
    ("telegram:afisha:", 0),
])
def test_last_seen_msg_id(cursor, expected):
    assert last_seen_msg_id(cursor) == expected


def test_window_cutoff_is_now_minus_days():
    now = at(31)
    assert window_cutoff(60, now=now) == now - dt.timedelta(days=60)
    assert window_cutoff(now=now) == now - dt.timedelta(days=reader.DEFAULT_WINDOW_DAYS)


# --- scan verdicts (pure) ----------------------------------------------------

def test_take_by_default():
    assert scan_decision(at(10), 0) == TAKE


def test_message_older_than_since_stops_the_scan():
    assert scan_decision(at(1), 0, since=at(5)) == STOP


def test_message_at_since_is_taken():
    assert scan_decision(at(5), 0, since=at(5)) == TAKE


def test_message_at_or_after_until_is_skipped_not_stopped():
    assert scan_decision(at(9), 0, until=at(9)) == SKIP
    assert scan_decision(at(10), 0, until=at(9)) == SKIP
    assert scan_decision(at(8), 0, until=at(9)) == TAKE


def test_max_scan_stops_exactly_at_the_cap():
    assert scan_decision(at(10), 499, max_scan=500) == TAKE
    assert scan_decision(at(10), 500, max_scan=500) == STOP


def test_max_scan_wins_over_everything_else():
    assert scan_decision(at(10), 5, since=at(1), until=at(20), max_scan=5) == STOP


def test_dateless_message_is_never_a_stop():
    assert scan_decision(None, 0, since=at(5), until=at(1)) == TAKE


# --- FloodWait pacing --------------------------------------------------------

def test_iter_flood_safe_passes_messages_through():
    slept = []
    out = list(iter_flood_safe(iter([1, 2, 3]), sleep=slept.append,
                              flood_error=FakeFloodWaitError))
    assert out == [1, 2, 3]
    assert slept == []


def test_flood_wait_sleeps_the_server_mandated_seconds_and_resumes():
    client = FloodingClient(msgs((3, 10), (2, 10), (1, 10)), flood_at=(1,), seconds=7)
    slept = []
    out = list(iter_flood_safe(client.iter_messages(None), sleep=slept.append,
                               flood_error=FakeFloodWaitError))
    assert [m.id for m in out] == [3, 2, 1]   # nothing lost across the wait
    assert slept == [7]


def test_repeated_flood_waits_all_get_slept():
    client = FloodingClient(msgs((3, 10), (2, 10), (1, 10)), flood_at=(0, 2), seconds=3)
    slept = []
    out = list(iter_flood_safe(client.iter_messages(None), sleep=slept.append,
                               flood_error=FakeFloodWaitError))
    assert [m.id for m in out] == [3, 2, 1]
    assert slept == [3, 3]


def test_non_flood_exception_is_not_swallowed():
    class Boom(Exception):
        pass

    def gen():
        yield 1
        raise Boom

    with pytest.raises(Boom):
        list(iter_flood_safe(gen(), flood_error=FakeFloodWaitError))


# --- scan_messages -----------------------------------------------------------

def scan(client, **kw):
    kw.setdefault("flood_error", FakeFloodWaitError)
    kw.setdefault("sleep", lambda s: None)
    return list(scan_messages(client, object(), **kw))


def test_pacing_and_min_id_reach_telethon():
    client = FakeClient(msgs((3, 10), (2, 10), (1, 10)))
    out = scan(client, min_id=1)
    assert [m.id for m in out] == [3, 2]           # server-side min_id filter
    assert client.iter_kwargs["min_id"] == 1
    assert client.iter_kwargs["wait_time"] == reader.DEFAULT_WAIT_TIME


def test_until_becomes_offset_date_and_still_filters_locally():
    client = FakeClient(msgs((3, 20), (2, 10), (1, 5)))
    out = scan(client, until=at(15))
    assert client.iter_kwargs["offset_date"] == at(15)
    assert [m.id for m in out] == [2, 1]           # id 3 is at/after `until`


def test_since_stops_the_scan_at_the_first_older_message():
    client = FakeClient(msgs((5, 20), (4, 12), (3, 4), (2, 3), (1, 2)))
    assert [m.id for m in scan(client, since=at(10))] == [5, 4]


def test_max_scan_backstop_bounds_a_busy_chat():
    client = FakeClient(msgs(*[(i, 20) for i in range(30, 0, -1)]))
    assert len(scan(client, max_scan=5)) == 5


def test_since_and_min_id_and_max_scan_compose():
    client = FakeClient(msgs((9, 20), (8, 19), (7, 18), (6, 1), (5, 1)))
    out = scan(client, min_id=7, since=at(10), max_scan=10)
    assert [m.id for m in out] == [9, 8]


def test_progress_fires_every_n_taken_messages():
    client = FakeClient(msgs(*[(i, 20) for i in range(10, 0, -1)]))
    seen = []
    scan(client, progress=seen.append, progress_every=3)
    assert seen == [3, 6, 9]


def test_reverse_scan_rejects_newest_first_bounds():
    """since= on a reverse scan would silently STOP at the first (oldest) message."""
    client = FakeClient(msgs((1, 10)))
    with pytest.raises(ValueError, match="newest-first"):
        scan(client, reverse=True, since=at(5))
    with pytest.raises(ValueError, match="newest-first"):
        scan(client, reverse=True, until=at(5))


def test_extra_kwargs_are_forwarded_to_iter_messages():
    client = FakeClient(msgs((1, 10)))
    scan(client, reverse=True, limit=5)
    assert client.iter_kwargs["reverse"] is True
    assert client.iter_kwargs["limit"] == 5


def test_scan_survives_a_flood_wait():
    client = FloodingClient(msgs((3, 10), (2, 10), (1, 10)), flood_at=(1,), seconds=4)
    slept = []
    out = list(scan_messages(client, object(), sleep=slept.append,
                             flood_error=FakeFloodWaitError))
    assert [m.id for m in out] == [3, 2, 1]
    assert slept == [4]


# --- collect -----------------------------------------------------------------

def test_collect_maps_and_drops_none():
    client = FakeClient([Message(id=3, text="keep", date=at(10)),
                         Message(id=2, text="   ", date=at(10)),
                         Message(id=1, text="also", date=at(10))])

    def mapper(m):
        return m.message.strip() or None

    rows = collect(client, object(), mapper, flood_error=FakeFloodWaitError)
    assert rows == ["keep", "also"]


def test_collect_sort_key_puts_the_new_cursor_last():
    client = FakeClient(msgs((9, 10), (7, 10), (5, 10)))
    rows = collect(client, object(), lambda m: (m.id, m.message),
                   sort_key=lambda r: r[0], flood_error=FakeFloodWaitError)
    assert [r[0] for r in rows] == [5, 7, 9]


def test_collect_of_an_empty_chat_is_empty():
    assert collect(FakeClient([]), object(), lambda m: m,
                   flood_error=FakeFloodWaitError) == []


# --- entity resolution -------------------------------------------------------

def test_handle_is_stripped_of_at_sign():
    client = FakeClient()
    resolve_entity(client, "@afisha")
    assert client.get_entity_calls == ["afisha"]


@pytest.mark.parametrize("target,expected", [("123", 123), ("-100500", -100500), (77, 77)])
def test_numeric_targets_become_ints(target, expected):
    client = FakeClient()
    resolve_entity(client, target)
    assert client.get_entity_calls == [expected]


def test_unseen_entity_is_retried_after_priming_the_dialog_cache():
    client = FakeClient(fail_get_entity_once=True, entities={555: Channel(id=555)})
    entity = resolve_entity(client, "555")
    assert entity.id == 555
    assert client.dialogs_listed == 1
    assert client.get_entity_calls == [555, 555]


def test_a_still_unknown_entity_is_not_retried_a_third_time():
    """Priming buys exactly one retry; a second failure is the caller's problem."""
    class AlwaysFails(FakeClient):
        def get_entity(self, target):
            self.get_entity_calls.append(target)
            raise ValueError("Could not find the input entity")

    client = AlwaysFails()
    with pytest.raises(ValueError):
        resolve_entity(client, "555")
    assert client.dialogs_listed == 1
    assert client.get_entity_calls == [555, 555]


def test_dialogs_by_name_is_case_insensitive_and_keeps_pairs():
    a, b = Channel(id=1, title="Voice Product"), User(id=2, first_name="Dan")
    client = FakeClient(dialogs=[Dialog("Voice Product", a), Dialog("Dan", b),
                                 Dialog(None, Channel(id=3))])
    assert dialogs_by_name(client, ["voice product"]) == [(a, "Voice Product")]
    assert dialogs_by_name(client, ["DAN", "voice PRODUCT"]) == [
        (a, "Voice Product"), (b, "Dan")]
    assert dialogs_by_name(client, ["missing"]) == []


# --- client construction -----------------------------------------------------

def test_make_client_builds_a_user_session_client(monkeypatch):
    """A user session, never a bot token — a bot cannot read arbitrary channels."""
    seen = {}

    class FakeTelegramClient:
        def __init__(self, session, api_id, api_hash):
            seen.update(session=session, api_id=api_id, api_hash=api_hash)
            self.session = types.SimpleNamespace()

    sync = types.ModuleType("telethon.sync")
    setattr(sync, "TelegramClient", FakeTelegramClient)
    monkeypatch.setitem(sys.modules, "telethon", types.ModuleType("telethon"))
    monkeypatch.setitem(sys.modules, "telethon.sync", sync)

    client = reader.make_client(77, "hash", pathlib.Path("/tmp/s.session"),
                                flood_sleep_threshold=3)

    # session stringified, credentials passed through, and no token argument exists
    assert seen == {"session": "/tmp/s.session", "api_id": 77, "api_hash": "hash"}
    assert client.session.flood_sleep_threshold == 3


# --- read_messages one-shot --------------------------------------------------

def test_read_messages_opens_and_closes_the_client(monkeypatch):
    client = FakeClient(msgs((2, 10), (1, 10)), entities={"afisha": Channel(id=9)})
    monkeypatch.setattr(reader, "make_client", lambda *a, **k: client)
    rows = reader.read_messages(api_id=1, api_hash="h", session="s", target="@afisha",
                                mapper=lambda m: m.id, flood_error=FakeFloodWaitError)
    assert rows == [2, 1]
    assert client.entered and client.exited


def test_credentials_cache_is_owner_only(tmp_path):
    cache = tmp_path / "creds.json"
    resolve_credentials("123", "hash", cache_path=cache, env={})
    assert cache.stat().st_mode & 0o077 == 0
