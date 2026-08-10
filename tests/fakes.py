"""Fake Telethon objects — the suite never touches telethon or the network.

Class *names* matter: export.py duck-types Telethon by ``type(x).__name__``
(``Message``, ``Channel``, ``User``), which is what keeps it telethon-free.
"""

from __future__ import annotations

import datetime as dt


class User:
    def __init__(self, id=1, first_name=None, last_name=None, username=None, phone=None):
        self.id, self.first_name, self.last_name = id, first_name, last_name
        self.username, self.phone = username, phone


class Channel:
    def __init__(self, id=2, title="Chan", username=None):
        self.id, self.title, self.username = id, title, username


class Chat(Channel):
    pass


class MessageEntityBold:
    def __init__(self, offset, length):
        self.offset, self.length = offset, length


class MessageEntityCode(MessageEntityBold):
    pass


class MessageEntityUrl(MessageEntityBold):
    pass


class MessageEntityUnknownThing(MessageEntityBold):
    """An entity type not in ENTITY_TYPES — must degrade to plain text."""


class Reaction:
    def __init__(self, emoticon=None):
        self.emoticon = emoticon


class ReactionCount:
    def __init__(self, count, emoticon=None):
        self.count, self.reaction = count, Reaction(emoticon)


class Reactions:
    def __init__(self, *results):
        self.results = list(results)


class Photo:
    pass


class Message:
    def __init__(self, id=1, text="", date=None, sender=None, sender_id=None,
                 entities=None, reply_to_msg_id=None, edit_date=None,
                 forward=None, media=None, reactions=None, video=None):
        self.id = id
        self.message = self.raw_text = text
        self.date = date or dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
        self.sender = sender
        self.sender_id = sender_id if sender_id is not None else getattr(sender, "id", None)
        self.entities = entities
        self.reply_to_msg_id = reply_to_msg_id
        self.edit_date = edit_date
        self.forward = forward
        self.media = media
        self.reactions = reactions
        self.video = video


class MessageService(Message):
    """Class name is not ``Message`` → must be dropped as a service message."""


class Forward:
    def __init__(self, sender=None):
        self.sender = sender


class Dialog:
    def __init__(self, name, entity):
        self.name, self.entity = name, entity


class FakeFloodWaitError(Exception):
    def __init__(self, seconds):
        super().__init__(f"wait {seconds}s")
        self.seconds = seconds


class FakeClient:
    """Records iter_messages kwargs; serves a canned newest-first message list."""

    def __init__(self, messages=(), dialogs=(), entities=None, fail_get_entity_once=False):
        self.messages = list(messages)
        self.dialogs = list(dialogs)
        self.entities = entities or {}
        self.fail_get_entity_once = fail_get_entity_once
        self.iter_kwargs: dict = {}
        self.get_entity_calls: list = []
        self.dialogs_listed = 0
        self.entered = self.exited = False

    # context manager, like telethon.sync's client
    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def get_entity(self, target):
        self.get_entity_calls.append(target)
        if self.fail_get_entity_once and self.dialogs_listed == 0:
            raise ValueError("Could not find the input entity")
        return self.entities.get(target, Channel(title=str(target)))

    def iter_dialogs(self, limit=None):
        self.dialogs_listed += 1
        return iter(self.dialogs)

    def iter_messages(self, entity, **kwargs):
        self.iter_kwargs = kwargs
        min_id = kwargs.get("min_id") or 0
        return iter([m for m in self.messages if m.id > min_id])


class FloodingIterator:
    """Raises FakeFloodWaitError once at each given 0-based position, then resumes.

    Deliberately NOT a generator: a generator is dead after an exception escapes
    it, while Telethon's real iterator survives a FloodWait and can be pulled
    again — which is the whole behaviour ``iter_flood_safe`` relies on.
    """

    def __init__(self, messages, flood_at=(), seconds=7):
        self.messages = list(messages)
        self.pending = set(flood_at)
        self.seconds = seconds
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i in self.pending:
            self.pending.discard(self.i)
            raise FakeFloodWaitError(self.seconds)
        if self.i >= len(self.messages):
            raise StopIteration
        msg = self.messages[self.i]
        self.i += 1
        return msg


class FloodingClient(FakeClient):
    """FakeClient whose iterator FloodWaits at the given positions."""

    def __init__(self, messages, flood_at=(), seconds=7):
        super().__init__(messages)
        self.flood_at = set(flood_at)
        self.seconds = seconds

    def iter_messages(self, entity, **kwargs):
        self.iter_kwargs = kwargs
        return FloodingIterator(self.messages, self.flood_at, self.seconds)
