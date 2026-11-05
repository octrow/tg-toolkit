# Plan: logic map findings

Findings from writing `docs/logic-map.md` on 2026-11-05. Independent review done 2026-11-05: #1-#7 re-checked, #8-#13 added (checked against Telethon 1.44 source, notify-send 0.8.3 and a real Desktop `result.json`).
The map describes the code as it is; this file says what is wrong with it and what we propose. New findings are appended.
A fixed finding gets ✅ and the map is updated in the same commit.

Severity: HIGH: wrong result for the user. MEDIUM: worse result than it could be. LOW: noise, debt, docs.
"Question": not sure if bug or intent; owner decides. "Not confirmed": needs a live run.

## Findings

| # | Map step | What is wrong | We propose | Severity |
|---|---|---|---|---|
| 1 | C4 | `push.py:91-120`: docstring says it degrades to a plain toast on notify-send without `-A`. The code runs `notify-send -A` once; on a rejecting version no toast shows and nothing retries. | Retry without `-A` when returncode != 0 and stdout is empty, or fix the docstring. | MEDIUM ✅ 2026-11-05: retries once without `-A` on any non-zero exit |
| 2 | C3 | `push.py:84`: `subprocess.run(..., check=False)` has no `timeout`; a hung notify-send (stuck D-Bus or notification daemon) blocks the caller, against "best-effort, never blocks". The D-Bus call itself usually times out (~25 s GDBus default), so "forever" needs a hung notify-send process. | Add a timeout and catch `TimeoutExpired`. | MEDIUM ✅ 2026-11-05: `timeout=NOTIFY_TIMEOUT_S` (10 s), `TimeoutExpired` logged |
| 3 | A5 | `reader.py:188-198`: resume after `FloodWaitError` assumes the Telethon iterator can be pulled again; tests only use `FloodingIterator` (`tests/fakes.py:139`). Review read Telethon 1.44: `requestiter.py:55-89` resets `buffer=[]`, `index=0` before `_load_next_chunk`, and `messages.py` `_update_offset` runs only after a successful call, so the next pull repeats the same chunk: resume works. Gap left: a FloodWait inside `_init` (only when `entity` still needs a network resolve) leaves `buffer=[]`, `_init` never re-runs, the next pull hits a missing `self.request`. Repeats are uncapped. | Optional max-retries; pass resolved entities only (already true for `read_messages`). Live check still useful. | LOW (confirmed by reading Telethon 1.44) |
| 4 | A1 | `reader.py:65-79`: only one of `api_id`/`api_hash` falls through silently; corrupt cache raises `JSONDecodeError`/`KeyError`/`ValueError`. The cache beats env, so a bad cache also hides good env vars. | Raise `RuntimeError` with the cache path. | LOW |
| 5 | Entry points | `DEFAULT_MAX_SCAN` (`reader.py:50`) is exported but applied by nothing; `DEFAULT_WINDOW_DAYS` (`reader.py:48`) only as the default of `window_cutoff` (`reader.py:146`). The module docstring says first runs are bounded by both; a bare `collect`/`read_messages` does a full backfill. | Document as caller constants, or apply in `read_messages`. Question for the owner. | LOW (Question) |
| 6 | B2 | `export.py:117`: sender of class `Chat` gets prefix `user`. `text_link` entities drop their URL (`export.py:76`); a real Desktop export (chat-watch `data_example`, 71 `text_link` items) carries `href` on every one, so this part is confirmed. | Add `href` from `e.url` for `MessageEntityTextUrl`; map `Chat` to its Desktop prefix. | LOW |
| 7 | Package | `pyproject.toml:18` lists telethon as a hard dependency, so a push-only project still installs it. The docstrings' claims (no import at load time, no user session needed) are true; the cost is install weight only. | Move telethon to an extra, or keep and say so. | LOW |

| 8 | A2 | `reader.py:99` sets `client.session.flood_sleep_threshold`; Telethon 1.x reads `client.flood_sleep_threshold` (`telethon/client/telegrambaseclient.py:314,488`, `client/users.py:37-52,121`), so the value is dead and the client keeps 60 s. FloodWaits of 11-60 s are slept silently inside Telethon instead of reaching `iter_flood_safe`'s log. `tests/test_reader.py:322-334` fakes `session` as a namespace and asserts the dead attribute, so the suite enshrines the bug. | Set `client.flood_sleep_threshold`, or pass `flood_sleep_threshold=` to the constructor; fix the test. | MEDIUM |
| 9 | A1 | `reader.py:70-72`: `os.open(..., O_TRUNC)` empties the cache before `int(api_id)` runs inside `json.dumps`; a non-numeric explicit `api_id` raises `ValueError` and leaves an empty cache file. Every later call then hits the cache first and raises `JSONDecodeError`, env vars included. | Convert `int(api_id)` before opening the file. | LOW |
| 10 | A5, A6 | `reader.py:228-240`, `243-256`: when `max_scan` stops a newest-first scan, `collect` returns the newest N rows with no flag (a log warning only). A caller storing the last row id as next `min_id` (what `sort_key` and `last_seen_msg_id` are built for) skips every message between the old cursor and the oldest one taken, for good. | Return or expose a "truncated" signal (e.g. a callback or attribute), or document that the cursor must not advance after a cap hit. | MEDIUM |
| 11 | A5 | `reader.py:171,173`: `since`/`until` are compared with Telethon's UTC-aware `msg.date` without a check; a naive datetime raises `TypeError` on the first message, mid-generator. | Reject naive `since`/`until` up front with a clear `ValueError`. | LOW |
| 12 | B1, B2, B3 | Shape differs from a real Desktop `result.json` (chat-watch `data_example`, 11 496 messages): Desktop `text_entities` are all dicts, plain text as `{"type":"plain"}` (0 bare strings), while `export.py:73-77` emits bare `str`; Desktop `text` is a list when entities exist, here always a str; channel `from_id` here is `channel-100…` because Telethon's `sender_id` is marked (`telethon/tl/custom/message.py:301`), Desktop uses the bare id; `chat_type` knows only `personal_chat`/`group_chat` (Desktop wrote `private_group`); `media_type` is a Telethon class name; hidden forwards get `"unknown"` instead of `forward.from_name`. dialogue-lens tolerates bare strings, so no known consumer breaks today. | Emit `{"type":"plain"}` segments, unmark channel ids (`utils.resolve_id`), widen `chat_type`; or narrow the "exactly the JSON Desktop produces" claim in `export.py:5-8`. | LOW |
| 13 | C3, C4 | `push.py:125-129`: argv has no `--` before title and body. A title or body starting with `-` is parsed as an option: `notify-send "-x probe" body` → `Unknown option -x probe`, exit 1 (notify-send 0.8.3). `notify` ignores the exit code and does not capture stderr, so no toast shows and nothing is logged; chat text like `- item` or `-5%` hits it. | Insert `"--"` before title and body; log a non-zero exit. | MEDIUM ✅ 2026-11-05: `--` added; a non-zero exit is still not logged |

## Order of work

1. #13, #1, #2 (all in `push.py`, share tests).
2. #8 (one line plus its test), then #10.
3. #4, #9, #11, #5.
4. #12, #6 if a consumer needs byte-for-byte Desktop shape.

## Done when

- [ ] every finding has ✅ or a "keep as is" decision with a reason;
- [ ] Known gaps in the map lists every open HIGH and MEDIUM finding.
