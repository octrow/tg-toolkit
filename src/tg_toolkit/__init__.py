"""tg-toolkit — Telethon user-session reading + Telegram push, extracted once.

Three independent modules; import only what you need:

* :mod:`tg_toolkit.reader` — the paced, incremental, bounded read loop
  (telethon, imported lazily).
* :mod:`tg_toolkit.export` — Telegram-Desktop-export conversion and parsing
  (pure, no telethon).
* :mod:`tg_toolkit.push` — Bot API / desktop notifications (stdlib only, works
  with telethon absent).
"""

from .export import (
    ENTITY_TYPES,
    ROW_FIELDS,
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
from .push import (
    BOT_API,
    bot_send,
    notify,
    notify_with_action,
    scrub_token,
    send_async,
)
from .reader import (
    DEFAULT_FLOOD_SLEEP_THRESHOLD,
    DEFAULT_MAX_SCAN,
    DEFAULT_WAIT_TIME,
    DEFAULT_WINDOW_DAYS,
    SKIP,
    STOP,
    TAKE,
    collect,
    dialogs_by_name,
    iter_flood_safe,
    last_seen_msg_id,
    make_client,
    read_messages,
    resolve_credentials,
    resolve_entity,
    scan_decision,
    scan_messages,
    window_cutoff,
)

__all__ = [
    "BOT_API", "DEFAULT_FLOOD_SLEEP_THRESHOLD", "DEFAULT_MAX_SCAN",
    "DEFAULT_WAIT_TIME", "DEFAULT_WINDOW_DAYS", "ENTITY_TYPES", "ROW_FIELDS",
    "SKIP", "STOP", "TAKE", "bot_send", "chat_type", "collect",
    "desktop_payload", "dialogs_by_name", "flatten_text", "iter_flood_safe",
    "last_seen_msg_id", "make_client", "notify", "notify_with_action",
    "parse_export", "parse_exports", "read_messages", "reactions",
    "resolve_credentials", "resolve_entity", "scan_decision", "scan_messages",
    "scrub_token", "send_async", "sender_name", "text_segments",
    "to_desktop_message", "window_cutoff", "write_export",
]
