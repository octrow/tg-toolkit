"""Desktop-export conversion, both directions. Pure — no telethon, no network."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from fakes import (
    Channel,
    Forward,
    Message,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityUnknownThing,
    MessageEntityUrl,
    MessageService,
    Photo,
    ReactionCount,
    Reactions,
    User,
)

from tg_toolkit import export
from tg_toolkit.export import (
    ENTITY_TYPES,
    chat_type,
    desktop_payload,
    flatten_text,
    parse_export,
    parse_exports,
    reactions,
    sender_name,
    text_segments,
    to_desktop_message,
    write_export,
)

UTC = dt.timezone.utc


def test_import_does_not_pull_telethon():
    assert "telethon" not in export.__dict__


# --- text_segments ----------------------------------------------------------

def test_empty_text_has_no_segments():
    assert text_segments("", None) == []
    assert text_segments("", [MessageEntityBold(0, 1)]) == []


def test_no_entities_is_one_plain_chunk():
    assert text_segments("hello", None) == ["hello"]
    assert text_segments("hello", []) == ["hello"]


def test_entity_splits_into_plain_and_typed_chunks():
    # "a bold c" — bold covers offsets 2..6
    assert text_segments("a bold c", [MessageEntityBold(2, 4)]) == [
        "a ", {"type": "bold", "text": "bold"}, " c",
    ]


def test_entity_at_start_and_end_needs_no_empty_padding():
    assert text_segments("abc", [MessageEntityCode(0, 3)]) == [
        {"type": "code", "text": "abc"},
    ]


def test_unknown_entity_type_degrades_to_plain_text():
    out = text_segments("xy", [MessageEntityUnknownThing(0, 2)])
    assert out == ["xy"]


def test_offsets_are_utf16_code_units_not_python_chars():
    # An emoji outside the BMP is 2 UTF-16 units but 1 Python char; a naive
    # str-slice implementation misaligns everything after it.
    raw = "🎭 bold"
    out = text_segments(raw, [MessageEntityBold(3, 4)])
    assert out == ["🎭 ", {"type": "bold", "text": "bold"}]


def test_overlapping_entities_are_skipped_not_duplicated():
    ents = [MessageEntityBold(0, 5), MessageEntityCode(2, 2)]
    out = text_segments("abcdefg", ents)
    assert out == [{"type": "bold", "text": "abcde"}, "fg"]
    assert "".join(c if isinstance(c, str) else c["text"] for c in out) == "abcdefg"


def test_entities_are_sorted_by_offset():
    #  a(0) ' '(1) 1(2) 2(3) 3(4) 4(5) ' '(6) u(7) r(8) l(9)
    ents = [MessageEntityUrl(7, 3), MessageEntityBold(0, 1)]
    out = text_segments("a 1234 url", ents)
    assert out == [{"type": "bold", "text": "a"}, " 1234 ",
                   {"type": "link", "text": "url"}]


def test_noise_entity_names_match_desktop_exactly():
    # A downstream parser strips these by name; a rename silently breaks it.
    for cls, name in [("MessageEntityCode", "code"), ("MessageEntityPre", "pre"),
                      ("MessageEntityUrl", "link"), ("MessageEntityMention", "mention"),
                      ("MessageEntityBotCommand", "bot_command"),
                      ("MessageEntityHashtag", "hashtag"),
                      ("MessageEntityTextUrl", "text_link")]:
        assert ENTITY_TYPES[cls] == name


# --- sender_name / reactions -------------------------------------------------

@pytest.mark.parametrize("sender,expected", [
    (None, None),
    (User(first_name="Dan", last_name="P"), "Dan P"),
    (User(first_name="Dan"), "Dan"),
    (User(last_name="P"), "P"),
    (Channel(title="Afisha"), "Afisha"),
    (User(id=7, username="nick"), "nick"),
    (User(id=42), "42"),
])
def test_sender_name(sender, expected):
    assert sender_name(sender) == expected


def test_reactions_empty_when_absent():
    assert reactions(Message()) == []
    assert reactions(Message(reactions=Reactions())) == []


def test_reactions_custom_emoji_collapses():
    msg = Message(reactions=Reactions(ReactionCount(3, "👍"), ReactionCount(1)))
    assert reactions(msg) == [{"count": 3, "emoji": "👍"},
                              {"count": 1, "emoji": "custom"}]


# --- to_desktop_message ------------------------------------------------------

def test_service_message_is_dropped():
    assert to_desktop_message(MessageService(id=5, text="joined")) is None


def test_minimal_message_record():
    msg = Message(id=9, text="hi", date=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                  sender=User(id=11, first_name="Dan"))
    rec = to_desktop_message(msg)
    assert rec["id"] == 9
    assert rec["type"] == "message"
    assert rec["from"] == "Dan"
    assert rec["from_id"] == "user11"
    assert rec["text"] == "hi"
    assert rec["text_entities"] == ["hi"]
    assert rec["date_unixtime"] == str(int(msg.date.timestamp()))
    # optional keys stay absent, exactly like a Desktop export
    for k in ("reply_to_message_id", "edited", "forwarded_from", "media_type", "reactions"):
        assert k not in rec


def test_channel_sender_gets_channel_prefix():
    rec = to_desktop_message(Message(id=1, text="x", sender=Channel(id=5), sender_id=5))
    assert rec["from_id"] == "channel5"


def test_missing_sender_id_yields_none_from_id():
    rec = to_desktop_message(Message(id=1, text="x", sender=None, sender_id=None))
    assert rec["from_id"] is None
    assert rec["from"] is None


def test_optional_fields_are_emitted_when_present():
    rec = to_desktop_message(Message(
        id=3, text="x", sender=User(id=1, first_name="A"),
        reply_to_msg_id=2,
        edit_date=dt.datetime(2026, 1, 3, 10, 0, 0, tzinfo=UTC),
        forward=Forward(User(first_name="Orig")),
        media=Photo(),
        reactions=Reactions(ReactionCount(1, "🔥")),
    ))
    assert rec["reply_to_message_id"] == 2
    assert rec["edited"].startswith("2026-01-03T")
    assert rec["forwarded_from"] == "Orig"
    assert rec["media_type"] == "Photo"
    assert rec["reactions"] == [{"count": 1, "emoji": "🔥"}]


def test_forward_from_unknown_sender():
    rec = to_desktop_message(Message(id=3, text="x", sender=User(id=1, first_name="A"),
                                     forward=Forward(None)))
    assert rec["forwarded_from"] == "unknown"


# --- payload / round trip ----------------------------------------------------

def test_chat_type_by_entity_class():
    assert chat_type(User(id=1)) == "personal_chat"
    assert chat_type(Channel(id=2)) == "group_chat"


def test_desktop_payload_shape():
    p = desktop_payload(Channel(id=7, title="Afisha"), [{"id": 1}])
    assert p == {"name": "Afisha", "type": "group_chat", "id": 7, "messages": [{"id": 1}]}
    assert desktop_payload(Channel(id=7, title="Afisha"), [], title="Override")["name"] == "Override"


def test_round_trip_telethon_to_rows(tmp_path):
    msgs = [to_desktop_message(Message(id=i, text=f"m{i}",
                                       date=dt.datetime(2026, 1, i, tzinfo=UTC),
                                       sender=User(id=1, first_name="Dan")))
            for i in (1, 2)]
    payload = desktop_payload(Channel(id=7, title="Afisha"), msgs)
    p = write_export(payload, tmp_path / "sub" / "result.json")
    assert json.loads(p.read_text(encoding="utf-8"))["name"] == "Afisha"
    rows = parse_export(p)
    assert [r["text"] for r in rows] == ["m1", "m2"]
    assert {r["chat"] for r in rows} == {"Afisha"}
    assert rows[0]["from_id"] == "user1"


# --- parse side --------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("plain", "plain"),
    ("", ""),
    ([], ""),
    (["a", {"type": "code", "text": "b"}, "c"], "abc"),
    ([{"type": "link"}], ""),
])
def test_flatten_text(value, expected):
    assert flatten_text(value) == expected


def _write(path, name, messages):
    path.write_text(json.dumps({"name": name, "messages": messages}), encoding="utf-8")
    return path


def test_parse_export_drops_service_messages(tmp_path):
    p = _write(tmp_path / "a.json", "Chat", [
        {"type": "service", "date": "2026-01-01T00:00:00", "text": "joined"},
        {"type": "message", "date": "2026-01-02T00:00:00", "from": "Dan",
         "from_id": "user1", "text": ["hi ", {"type": "bold", "text": "there"}]},
    ])
    rows = parse_export(p)
    assert rows == [{"chat": "Chat", "date": "2026-01-02T00:00:00", "from": "Dan",
                     "from_id": "user1", "text": "hi there"}]


def test_parse_exports_merges_and_sorts_by_date(tmp_path):
    _write(tmp_path / "b.json", "B", [{"type": "message", "date": "2026-01-05T00:00:00",
                                       "text": "later"}])
    _write(tmp_path / "a.json", "A", [{"type": "message", "date": "2026-01-01T00:00:00",
                                       "text": "earlier"}])
    rows = parse_exports(str(tmp_path / "*.json"))
    assert [r["text"] for r in rows] == ["earlier", "later"]
    # an iterable of paths works too
    rows2 = parse_exports([tmp_path / "b.json", tmp_path / "a.json"])
    assert [r["text"] for r in rows2] == ["earlier", "later"]


def test_parse_exports_tolerates_missing_date(tmp_path):
    _write(tmp_path / "a.json", "A", [{"type": "message", "text": "x"}])
    assert parse_exports(str(tmp_path / "*.json"))[0]["date"] is None


def test_parse_exports_of_nothing_is_empty():
    assert parse_exports("/nonexistent/dir/*.json") == []
