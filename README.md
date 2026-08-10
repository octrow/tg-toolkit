# tg-toolkit

Telethon **user-session** reading + Telegram push, extracted from four
independent copies of the same code (dialogue-lens `tg.py` /
`sources/tg_fetch.py`, get_cool_event `adapters/telegram.py` — whose docstring
already admitted it was a port — and chat-watch's Bot API push).

Python ≥3.12. One runtime dep (`telethon`), imported **lazily**: `tg_toolkit.push`
and `tg_toolkit.export` work on a machine with no telethon installed at all.

Three modules, deliberately independent:

| module | needs telethon | what it is |
|---|---|---|
| `tg_toolkit.reader` | at call time only | the paced, incremental, bounded read loop |
| `tg_toolkit.export` | no | Telegram-Desktop-export conversion, both directions |
| `tg_toolkit.push`   | no (stdlib urllib) | Bot API message + desktop `notify-send` |

## Invariants (the reason this library exists)

**reader**

* **User session, never a bot token.** A bot cannot read arbitrary public
  channels or your own dialog history.
* **Pace every request.** `wait_time=1.0` between GetHistory calls keeps under
  Telegram's ~30s/10-request limit. `flood_sleep_threshold=10` lets Telethon
  swallow short waits; longer ones raise `FloodWaitError`, and `iter_flood_safe`
  sleeps the **server-mandated** seconds and resumes **the same iterator**. A
  FloodWait is a rate guard, not an error path — nothing is lost across the wait.
* **Bounded scans.** Newest-first iteration stops at the first message older than
  `since` (everything after it is older too), and `max_scan` is the backstop so a
  pathologically busy chat can't scan forever *inside* the window. Without both,
  the first run degenerates into a full-history backfill (it did, and it hung a
  collect for a minute).
* **Incremental by `min_id`.** The server filters; we don't. The cursor comes from
  `last_seen_msg_id`, which accepts a bare id or a namespaced one
  (`"telegram:<handle>:<msg_id>"`) and degrades an unparseable cursor to `0`
  (full windowed scan) instead of raising — a corrupt cursor must not stop
  collection.
* **Unseen entity ids need the dialog cache primed.** `resolve_entity` retries
  `get_entity` once after `list(client.iter_dialogs())`; both source projects
  needed this trick.
* `telethon.sync` on purpose — no event loop of our own.

**export**

* Entity **offsets are UTF-16 code units** (Telegram's convention). Slicing goes
  through `utf-16-le`; a plain `str` slice misaligns every message with an emoji.
* Overlapping/nested entities are **skipped**, not merged — duplicating covered
  text would corrupt the reconstruction.
* Entity names must match Desktop **exactly** (`code`, `pre`, `link`, `mention`,
  `bot_command`, `hashtag`, …), because downstream parsers strip noise *by name*.
  A test pins them.
* Telethon objects are duck-typed by class name (`Message`, `Channel`, `User`),
  which is what keeps this module import-free and testable with plain fakes.
  Service messages (class name ≠ `Message`) become `None`.

**push**

* Push is **best-effort**: every failure is logged and swallowed, never raised.
  Desktop toast is primary, the bot is a mirror.
* **Tokens are scrubbed from every log line** (`scrub_token`) — urllib puts the
  full URL, token included, in its exception messages.
* Plain text, no `parse_mode` — sidesteps markdown-escaping bugs. Text is
  truncated at 4000 chars, never split.
* The mirror runs in a **daemon thread**, so a slow/dead Bot API can't block the
  caller's loop. `notify` returns that thread (or `None`) for callers who want to
  join it.
* HARD RULE for the caller: `chat_id` is the owner's private-bot chat, never a
  monitored chat.

## Usage

Incremental channel collect (get_cool_event's adapter, now ~10 lines):

```python
from tg_toolkit import last_seen_msg_id, read_messages, window_cutoff

def to_post(msg):
    text = (msg.message or "").strip()
    if not text:
        return None                       # returning None is the skip hook
    return RawPost(post_id=f"telegram:{handle}:{msg.id}", text=text, ...)

posts = read_messages(
    api_id=api_id, api_hash=api_hash, session=session, target=f"@{handle}",
    mapper=to_post,
    min_id=last_seen_msg_id(cursor),      # incremental
    since=window_cutoff(60),              # recency window
    max_scan=500,                         # backstop
    label=f"@{handle}",
    sort_key=lambda p: int(p.post_id.rsplit(":", 1)[-1]),   # last row = new cursor
)
```

Several chats over **one** connection (dialogue-lens' `fetch_live`) — do this
rather than calling `read_messages` per chat:

```python
from tg_toolkit import collect, dialogs_by_name, make_client

with make_client(api_id, api_hash, session) as client:
    for entity, title in dialogs_by_name(client, chats):
        rows += collect(client, entity, lambda m: to_row(title, m),
                        since=since_dt, until=until_dt)
```

Live fetch in the Desktop export shape (dialogue-lens' `fetch_chat`), so an
offline parser keeps working unchanged:

```python
from tg_toolkit import (desktop_payload, make_client, resolve_entity,
                        scan_messages, to_desktop_message, write_export)

with make_client(api_id, api_hash, session) as client:
    entity = resolve_entity(client, chat)
    # reverse=True → chronological; extra kwargs go straight to iter_messages
    msgs = [r for m in scan_messages(client, entity, reverse=True, limit=limit)
            if (r := to_desktop_message(m)) is not None]
write_export(desktop_payload(entity, msgs), out_dir / "result.json")
```

Offline, no API at all:

```python
from tg_toolkit import parse_exports     # -> rows: chat, date, from, from_id, text
rows = parse_exports("data/*/result.json")
```

Push (no telethon needed):

```python
from tg_toolkit import bot_send, notify, notify_with_action

notify("New mention", body, urgent=True, app_name="chat-watch", bot=(token, owner_id))
notify_with_action("New mention", body, action_label="Collect",
                   on_action=lambda: launch_investigate(ref), bot=(token, owner_id))
bot_send(token, owner_id, "plain text")   # -> bool, never raises
```

## API

```
reader:  resolve_credentials(api_id=None, api_hash=None, *, cache_path=None, env=None) -> (int, str)
         make_client(api_id, api_hash, session, *, flood_sleep_threshold=10)
         resolve_entity(client, target)                # @handle | id | title, dialog-primed retry
         dialogs_by_name(client, names) -> [(entity, title)]
         last_seen_msg_id(cursor) -> int
         window_cutoff(days=60, *, tz=None, now=None) -> datetime
         scan_decision(msg_date, scanned, *, since, until, max_scan) -> "take"|"skip"|"stop"
         iter_flood_safe(iterator, *, sleep, flood_error=None, label="") -> Iterator
         scan_messages(client, entity, *, min_id=0, since=None, until=None, max_scan=None,
                       wait_time=1.0, progress=None, progress_every=25, label="",
                       **iter_kwargs) -> Iterator
         collect(client, entity, mapper, *, sort_key=None, **scan_kwargs) -> list
         read_messages(*, api_id, api_hash, session, target, mapper, **collect_kwargs) -> list

export:  text_segments(raw, entities) -> list        sender_name(sender) -> str | None
         reactions(msg) -> list                      to_desktop_message(msg) -> dict | None
         chat_type(entity) -> str                    desktop_payload(entity, messages, *, title=None)
         write_export(payload, path) -> Path         flatten_text(text) -> str
         parse_export(path) -> [row]                 parse_exports(glob_or_paths) -> [row]
         ENTITY_TYPES, ROW_FIELDS

push:    scrub_token(s) -> str
         bot_send(token, chat_id, text, *, api_base=BOT_API, timeout=10) -> bool
         send_async(token, chat_id, text, **kw) -> Thread
         notify(title, body, *, urgent=False, app_name=..., bot=None) -> Thread | None
         notify_with_action(title, body, *, action_label, on_action, urgent=False,
                            app_name=..., bot=None) -> Thread
```

## Migration notes

* **`scan_messages` counts differently from get_cool_event's loop.** The old code
  incremented `scanned` and then broke on `>=`, so `MAX_SCAN_PER_RUN=500` yielded
  499 messages; here `max_scan=500` yields exactly 500.
* **`resolve_credentials` gained an env fallback** (`TG_API_ID`/`TG_API_HASH`) that
  dialogue-lens' docstring promised but its code never implemented. Order:
  explicit args (cached) > cache file > env. Pass `cache_path` to keep the
  `data/.telegram.json` behaviour; without it nothing is written to disk.
* **`export` no longer imports telethon**; `Channel`/`User`/`Message` are matched
  by class name. Exotic entity subclasses that used to satisfy `isinstance` will
  now take the `user` / `group_chat` branch — no known Telethon type does.
* Consumer tests fake the client object (see `tests/fakes.py`); nothing needs
  `telethon` patched.

## Tests

```bash
cd /home/octrow/dev/pylibs && uv run --package tg-toolkit --extra dev pytest tg-toolkit/tests -q
```

No network, no session file, no real `TelegramClient`. `tests/test_no_telethon.py`
blocks `telethon` in the import system to prove `push`/`export`/`reader` all
*import* without it.
