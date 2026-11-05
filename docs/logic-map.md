# Logic map

How a Telegram chat becomes rows (live Telethon read, or a Desktop export file) and how a message becomes a push notification.
The map describes the code as it is today, step by step.
Each step says what it decides, where the flow branches, and which module does it.

tg-toolkit is a library: three independent modules, no CLI, no entry script. Callers pick the flow they need.
Known gaps and fixes in progress are in `docs/plans/2026-11-05-logic-map-fixes.md`.

Rule: if you change a step or a branch, update this file in the same commit.
`tests/test_logic_map.py` checks that every `Module:` line points to a real module.

## The flow at a glance

```
Flow A: live read                         Flow B: export <-> rows             Flow C: push
caller                                    Telethon Message / result.json      caller
  |                                         |                                   |
  |- A1 resolve_credentials                 |- B1 text_segments                 |- C1 bot_send
  |- A2 make_client                         |- B2 to_desktop_message            |- C2 send_async
  |- A3 resolve_entity                      |- B3 desktop_payload/write_export  |- C3 notify
  |- A4 last_seen_msg_id / window_cutoff    |- B4 parse_export(s)               '- C4 notify_with_action
  |- A5 scan_messages (iter_flood_safe)   rows (dict) / result.json
  |- A6 collect
  '- A7 read_messages (one-shot)
  |
list of caller-mapped rows
```

Flow A and B share nothing but the caller: `reader` never calls `export`, `push` imports neither.
Only `reader` imports telethon (lazily, inside functions). `export` duck-types Telethon objects by class name.

## Where the flow splits by kind

No kind-based split: nothing in the library varies by chat, tenant or channel. Variation is by caller arguments and by Telethon class name (`Message`, `Channel`, `User`, entity class names), listed in B1, B2, B3.

## Entry points

No CLI, GUI or worker. The public API is `tg_toolkit/__init__.py` (re-exports, `__all__`). Importing it loads all three modules but not telethon.

| Entry | Defaults | Notes |
|---|---|---|
| `read_messages` | wait_time 1.0 s, `flood_sleep_threshold=10` passed but not applied (A2: Telethon keeps 60 s), no `since`, no `max_scan`, `min_id=0` | opens and closes one client per call; `with` runs Telethon `start()`: first run prompts phone and code on stdin; creates/updates `<session>.session` (SQLite) |
| `collect` / `scan_messages` | `min_id=0`, `since/until/max_scan=None`, `wait_time=1.0`, `progress_every=25` | `DEFAULT_WINDOW_DAYS=60` is applied only as the default of `window_cutoff`; `DEFAULT_MAX_SCAN=500` is applied by nothing. A bare call scans the full history |
| `notify` | `urgent=False`, `bot=None`, `app_name="tg-toolkit"` | desktop toast plus optional phone mirror; returns the mirror thread or None |
| `notify_with_action` | `urgent=False`, `bot=None`, `app_name="tg-toolkit"` | mirror thread first, then notify-send in a daemon thread |
| `bot_send` / `send_async` | `timeout=10`, `api_base=BOT_API` | never raises for str text and an http(s) `api_base` (slicing, `json.dumps` and `Request()` sit outside the `try`) |
| `parse_exports` | none | glob string or iterable of paths; a glob matching nothing → `[]`, no warning |

Module: `reader`, `export`, `push`

## Flow A: live read (`reader`)

### A1. resolve_credentials

Decides: where `(api_id, api_hash)` come from.
Branches:
- explicit `api_id` and `api_hash` → used; written to `cache_path` (mode 0600) if given;
- else cache file exists → read from it;
- else env `TG_API_ID` + `TG_API_HASH` → used;
- else `RuntimeError` with the my.telegram.org hint.
Cache wins over env: a stale or corrupt cache file shadows good `TG_API_*` env vars.
Fails: only one explicit arg falls through to cache/env without a warning. A corrupt cache raises `JSONDecodeError`/`KeyError`/`ValueError`, not `RuntimeError`. A non-numeric explicit `api_id` with `cache_path` truncates the cache file (`O_TRUNC`, `reader.py:70`) before `int(api_id)` raises, leaving an empty cache that breaks every later call (#9).
Module: `reader`

### A2. make_client

Decides: builds `telethon.sync.TelegramClient(str(session), int(api_id), api_hash)` and sets `client.session.flood_sleep_threshold` (default 10 s).
That attribute is dead: Telethon 1.x reads `client.flood_sleep_threshold` (`telegrambaseclient.py:314,488`), not the session's. The real threshold stays Telethon's default 60 s: waits up to 60 s are slept inside Telethon without reaching `iter_flood_safe` (#8).
No telethon: `ImportError` at call time.
Module: `reader`

### A3. resolve_entity

Decides: target string/int to a Telethon entity.
Branches:
- str of digits (optional `-`) → int; else leading `@` stripped;
- `get_entity` raises `ValueError`/`TypeError` → `iter_dialogs()` primes the cache, one retry; a second failure propagates.
Any failed lookup, a typo included, lists every dialog once before failing. A chat title resolves only from the session entity cache, so a title usually needs that retry. `"--5"` passes the digit check and `int()` raises `ValueError` outside the `try`.
Module: `reader`

`dialogs_by_name` is a helper: dialogs whose casefolded name equals one of the casefolded names (exact match, not substring), returned as `[(entity, title)]`.

### A4. Cursor and window

Decides: the incremental floor and the time floor.
- `last_seen_msg_id`: falsy → 0; takes the last `:`-token as int; unparseable → 0 (fail-open: full windowed scan);
- `window_cutoff(days=60)`: `now - days`, aware (UTC unless `tz`).
Module: `reader`

### A5. scan_messages

Decides: which messages leave the scan and when it stops.
In: client, entity, `min_id`, `since`, `until`, `max_scan`, extra `iter_kwargs`. Out: generator of Telethon messages, newest first.
Branches:
- `reverse=True` plus `since` or `until` → `ValueError` before any request (a generator: raised on the first `next`, which `collect` does at once);
- `until` set → `offset_date=until` unless the caller passed `offset_date`;
- verdict per message (`scan_decision`, in order): `scanned >= max_scan` → STOP; date `< since` → STOP; date `>= until` → SKIP; no date → TAKE;
- STOP at `max_scan` logs a warning; STOP by `since` is silent;
- `progress(scanned)` every `progress_every` (25) taken messages.
`scanned` counts only TAKE messages: SKIPped ones do not count toward `max_scan`, so a caller `offset_date` later than `until` makes the SKIPs unbounded.
No `limit` is passed to Telethon: the STOP at `max_scan` is reached by pulling message `max_scan+1`, which can cost one more GetHistory chunk (100 messages).
FloodWait: `iter_flood_safe` catches `FloodWaitError`, sleeps `exc.seconds`, calls `next` on the same iterator again. No cap on repeats. With real Telethon 1.44 this resumes: `RequestIter.__anext__` resets the buffer before `_load_next_chunk`, and the request offsets move only after a successful call, so the next pull repeats the same chunk (#3).
Fails: any other Telethon error propagates. A naive `since`/`until` against Telethon's aware `msg.date` raises `TypeError` on the first message (#11).
Module: `reader`

### A6. collect

Decides: which rows survive and their order.
`mapper(msg)` returning None drops the message, after the scan: dropped messages still count toward `max_scan`. `sort_key` re-sorts ascending so the last row carries the highest id.
The result carries no sign that `max_scan` cut the scan (only a log warning). A caller that stores the last row's id as the next `min_id` then skips, for good, every message between the old cursor and the oldest one taken (#10).
Module: `reader`

### A7. read_messages

Decides: nothing new; wires `make_client`, `resolve_entity`, `collect` for one target. Credentials and cursor are the caller's job. `with client` runs Telethon `start()` (interactive login on the first run, session file written) and disconnects on exit.
Module: `reader`

## Flow B: export conversion (`export`)

### B1. text_segments

Decides: how message text splits into Desktop `text_entities`.
Branches:
- empty text → `[]`; no entities → `[raw]`;
- entities sorted by offset; offsets are UTF-16 units (text sliced via `utf-16-le`);
- entity starting before the previous end → skipped;
- class name in `ENTITY_TYPES` (9 types) → `{"type","text"}`; unknown class → plain text segment.
Plain text is a bare `str` item. A real Desktop export writes `{"type": "plain", "text": ...}` dicts only, and `text_link` carries `href`, which is dropped here (#6, #12).
Module: `export`

### B2. to_desktop_message

Decides: one Telethon `Message` to one Desktop record.
Branches:
- class name not `Message` (service) → None;
- `from_id` = `channel<sender_id>` if sender class is `Channel`, else `user<sender_id>`; None when `sender_id` is falsy. Telethon's `sender_id` is the marked id (`-100…` for channels, `message.py:301`), so a channel becomes `channel-100…` (#12);
- optional keys only when present: `reply_to_message_id`, `edited`, `forwarded_from` (`"unknown"` if `forward.sender` is None, `from_name` unused), `media_type` (Telethon class name, e.g. `MessageMediaPhoto`), `reactions` (custom emoji → `"custom"`).
- `date` is local time without zone; `date_unixtime` treats `msg.date` as UTC.
`text` is always the raw string; `text_entities` holds the segments.
`sender_name`: full name, else title, else username, else id, else None.
Module: `export`

### B3. desktop_payload, write_export

Decides: envelope `{name,type,id,messages}`; `type` is `personal_chat` for a `User`, else `group_chat` (Desktop also writes `private_group`, `*_supergroup`, `*_channel`, #12); name is `title`, else sender name, else id. `write_export` creates parent dirs, writes JSON with `indent=1`, `ensure_ascii=False`.
Module: `export`

### B4. parse_export, parse_exports

Decides: Desktop JSON to flat rows (`ROW_FIELDS`: chat, date, from, from_id, text).
Branches:
- only records with `type == "message"` survive;
- `flatten_text` joins str or list-of-segment text;
- `parse_exports`: str → `glob.glob` (no `~`, no recursion), else the iterable as given; all rows sorted by `date` string (local time of the exporting machine), missing date sorts first; ties keep glob order, which is filesystem order.
Fails: bad JSON or a missing file raises.
Module: `export`

## Flow C: push (`push`)

### C1. bot_send

Decides: POST one plain-text message to Bot API `sendMessage`, text cut to 4000 chars.
Fails: any exception is logged with the token masked (`bot***`) and the call returns False.
Module: `push`

### C2. send_async

Daemon thread running `bot_send`; returns the thread.
Module: `push`

### C3. notify

Decides: toast plus optional mirror.
Branches:
- `bot=(token, chat_id)` → mirror thread started first (`title\nbody[:500]`);
- then `notify-send` runs blocking, body cut to 500, urgency critical or normal.
Fails: `OSError` (no notify-send) is logged; other errors propagate. No timeout on the subprocess. A non-zero exit is ignored (`check=False`, stderr not captured, nothing logged).
argv has no `--` before title and body: a title or body that starts with `-` (chat text like `- item`, `-5%`) is parsed as an option, notify-send exits 1 with `Unknown option`, and no toast shows (#13).
Module: `push`

### C4. notify_with_action

Decides: toast with one button.
Branches:
- mirror sent without a button;
- thread runs `notify-send -A action=<label>` and captures stdout; stdout `action` → `on_action()`;
- any exception in the thread, including a failing `on_action`, is logged.
Fails: a notify-send that rejects `-A` exits non-zero with empty stdout; the code does not retry without `-A`. The same `-`-leading title/body failure as C3 applies (#13).
Module: `push`

## Output

Flow A returns a list of caller-defined rows. Flow B returns dict records or `ROW_FIELDS` rows. Flow C returns a bool or a thread.
Side effects: `resolve_credentials` writes the cache file (and creates its parent dir); Telethon writes `<session>.session` when a client from `make_client` connects (`read_messages`); `write_export` writes JSON (and creates parent dirs); `notify*` start threads and run a subprocess; `bot_send` makes a network call. Nothing else writes to disk.

## Known gaps

- `notify_with_action` has no plain-toast fallback on a notify-send without `-A`, though its docstring promises one (#1).
- `notify` can block the caller: `subprocess.run` has no timeout (#2).
- `flood_sleep_threshold` is set on the session, which Telethon ignores; the real threshold is 60 s (#8).
- A `max_scan` cut is invisible to `collect` callers; advancing the cursor skips the gap for good (#10).
- A title or body that starts with `-` makes notify-send fail; no toast, nothing logged (#13).
