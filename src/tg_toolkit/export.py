"""Telegram-Desktop-export shape: Telethon messages → export records, and back.

Two directions, both **pure** (no telethon import, no network, no client):

* ``to_desktop_message`` / ``desktop_payload`` — turn Telethon ``Message``
  objects into exactly the JSON Telegram Desktop's "Export chat history"
  produces, so an offline parser written against Desktop exports keeps working
  against a live fetch (dialogue-lens' original reason for existing).
* ``parse_exports`` / ``flatten_text`` — read Desktop ``result.json`` files back
  into flat rows.

Telethon objects are duck-typed by attribute and class name, so tests (and
callers that only parse exports) need neither telethon nor a session.

Invariants:

* Entity **offsets are UTF-16 code units** (Telegram's convention). Slicing must
  go through ``utf-16-le`` or every message with an emoji shifts.
* Overlapping / nested entities are skipped rather than merged — duplicating the
  covered text would corrupt the reconstruction.
* Only entity types in :data:`ENTITY_TYPES` are annotated; anything else
  degrades to plain text. The noise types a downstream parser strips
  (code/pre/link/mention/bot_command/hashtag) must map **exactly** to Desktop's
  names, or the strip silently stops working.
* Service messages (anything whose class is not ``Message``) → ``None``.
"""

from __future__ import annotations

import glob
import json
from collections.abc import Iterable
from datetime import timezone
from pathlib import Path

#: Telethon entity class name → Telegram-Desktop ``text_entities`` type.
ENTITY_TYPES = {
    "MessageEntityMention": "mention",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityUrl": "link",
    "MessageEntityTextUrl": "text_link",
    "MessageEntityBotCommand": "bot_command",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
}

_DESKTOP_TIME = "%Y-%m-%dT%H:%M:%S"


# --- Telethon → Desktop ------------------------------------------------------

def text_segments(raw: str, entities: Iterable | None) -> list:
    """Split message text into Desktop-style ``text_entities``.

    Returns a list of plain ``str`` and ``{"type": ..., "text": ...}`` dicts.
    """
    if not raw:
        return []
    if not entities:
        return [raw]
    u16 = raw.encode("utf-16-le")

    def sl(off: int, length: int) -> str:
        return u16[off * 2:(off + length) * 2].decode("utf-16-le", "ignore")

    out: list = []
    cur = 0
    for e in sorted(entities, key=lambda e: e.offset):
        if e.offset < cur:  # overlap with a previous entity — skip
            continue
        if e.offset > cur:
            out.append(sl(cur, e.offset - cur))
        etype = ENTITY_TYPES.get(type(e).__name__)
        text = sl(e.offset, e.length)
        out.append({"type": etype, "text": text} if etype else text)
        cur = e.offset + e.length
    total = len(u16) // 2
    if cur < total:
        out.append(sl(cur, total - cur))
    return out


def sender_name(sender) -> str | None:
    """Human name for a User / Channel / Chat: full name > title > username > id."""
    if sender is None:
        return None
    first = getattr(sender, "first_name", None)
    last = getattr(sender, "last_name", None)
    if first or last:
        return " ".join(p for p in (first, last) if p)
    if getattr(sender, "title", None):  # channel/chat
        return sender.title
    if getattr(sender, "username", None):
        return sender.username
    return str(getattr(sender, "id", "")) or None


def reactions(msg) -> list:
    """``[{"count": int, "emoji": str}]``; custom emoji collapse to ``"custom"``."""
    r = getattr(msg, "reactions", None)
    if not r or not getattr(r, "results", None):
        return []
    return [{"count": rc.count,
             "emoji": getattr(rc.reaction, "emoticon", None) or "custom"}
            for rc in r.results]


def to_desktop_message(msg) -> dict | None:
    """One Telethon ``Message`` → one Desktop ``message`` record (None if service)."""
    if type(msg).__name__ != "Message":  # service message
        return None
    local = msg.date.astimezone()  # UTC-aware → local, like Desktop exports
    sender = msg.sender
    sid = msg.sender_id
    prefix = "channel" if type(sender).__name__ == "Channel" else "user"
    raw = msg.raw_text or ""
    rec: dict = {
        "id": msg.id,
        "type": "message",
        "date": local.strftime(_DESKTOP_TIME),
        "date_unixtime": str(int(msg.date.replace(tzinfo=timezone.utc).timestamp())),
        "from": sender_name(sender),
        "from_id": f"{prefix}{sid}" if sid else None,
        "text": raw,
        "text_entities": text_segments(raw, msg.entities or []),
    }
    if getattr(msg, "reply_to_msg_id", None):
        rec["reply_to_message_id"] = msg.reply_to_msg_id
    if getattr(msg, "edit_date", None):
        rec["edited"] = msg.edit_date.astimezone().strftime(_DESKTOP_TIME)
    if getattr(msg, "forward", None):
        rec["forwarded_from"] = sender_name(getattr(msg.forward, "sender", None)) or "unknown"
    if getattr(msg, "media", None) is not None:
        rec["media_type"] = type(msg.media).__name__
    reacts = reactions(msg)
    if reacts:
        rec["reactions"] = reacts
    return rec


def chat_type(entity) -> str:
    """Desktop's ``type`` field: ``personal_chat`` for a User, else ``group_chat``."""
    return "personal_chat" if type(entity).__name__ == "User" else "group_chat"


def desktop_payload(entity, messages: list[dict], *, title: str | None = None) -> dict:
    """The whole ``result.json`` envelope Desktop writes around the messages."""
    return {
        "name": title or sender_name(entity) or str(getattr(entity, "id", "")),
        "type": chat_type(entity),
        "id": getattr(entity, "id", None),
        "messages": messages,
    }


def write_export(payload: dict, path: str | Path) -> Path:
    """Write a Desktop-shaped payload to ``path`` (indent=1, like Desktop). """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# --- Desktop → rows ----------------------------------------------------------

def flatten_text(text) -> str:
    """Desktop ``text`` (str or list of str/{"text":...}) → one plain string."""
    if isinstance(text, str):
        return text
    if not text:
        return ""
    return "".join(i if isinstance(i, str) else i.get("text", "") for i in text)


ROW_FIELDS = ["chat", "date", "from", "from_id", "text"]


def parse_export(path: str | Path) -> list[dict]:
    """One Desktop ``result.json`` → rows (:data:`ROW_FIELDS`), service msgs dropped."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    chat = d.get("name")
    return [{
        "chat": chat,
        "date": m.get("date"),
        "from": m.get("from"),
        "from_id": m.get("from_id"),
        "text": flatten_text(m.get("text", "")),
    } for m in d.get("messages", []) if m.get("type") == "message"]


def parse_exports(data_glob: str | Iterable[str | Path]) -> list[dict]:
    """Many exports (a glob pattern or an iterable of paths) → rows sorted by date."""
    paths = glob.glob(data_glob) if isinstance(data_glob, str) else list(data_glob)
    rows: list[dict] = []
    for f in paths:
        rows.extend(parse_export(f))
    rows.sort(key=lambda r: r["date"] or "")
    return rows
