# Behavior and rules

How each module acts, and why.

## Reader

- **User session, never a bot token.** A bot can't read arbitrary public channels or your own dialogs.
- **Every request is paced.** `wait_time=1.0` between GetHistory calls stays under Telegram's limit of
  about 10 requests per 30 s. `flood_sleep_threshold=10` lets Telethon absorb short waits. Longer ones
  raise `FloodWaitError`; `iter_flood_safe` sleeps the seconds the server asked for and resumes the
  same iterator, so no messages are lost across the wait.
- **Scans are bounded.** Iteration goes newest-first and stops at the first message older than `since`,
  because everything after it is older too. `max_scan` is the backstop for a very busy chat. Without
  both, a first run turns into a full-history backfill.
- **Incremental by `min_id`.** Telegram filters on the server side. `last_seen_msg_id` accepts a bare id
  or a namespaced one (`"telegram:<handle>:<msg_id>"`) and turns an unparseable cursor into `0` (a full
  windowed scan) instead of raising, so a corrupt cursor doesn't stop collection.
- **Unseen entity ids get one retry.** `resolve_entity` calls `get_entity` again after priming the
  dialog cache with `client.iter_dialogs()`.
- **No event loop of its own.** It uses `telethon.sync`.

## Export rules

- Entity offsets are UTF-16 code units, as Telegram defines them. A plain `str` slice misaligns every
  message that contains an emoji.
- Overlapping or nested entities are skipped, not merged. Merging would duplicate the covered text.
- Entity names match Desktop exactly (`code`, `pre`, `link`, `mention`, `bot_command`, `hashtag`, …),
  because downstream parsers strip noise by name. A test pins them.
- Telethon objects are matched by class name (`Message`, `Channel`, `User`), which keeps the module free
  of telethon imports and testable with plain fakes. Service messages (class name other than `Message`)
  become `None`.

## Push rules

- Push is best-effort: every failure is logged and swallowed, never raised. The desktop toast is the
  primary channel and the bot is a mirror.
- Bot tokens are scrubbed from every log line (`scrub_token`), because urllib puts the full URL, token
  included, into its exception messages:
  `https://api.telegram.org/bot***/sendMessage`.
- Plain text, no `parse_mode`, which avoids markdown-escaping bugs. Text over 4000 characters is
  truncated, never split.
- The bot mirror runs in a daemon thread, so a slow or dead Bot API can't block your loop. `notify`
  returns that thread (or `None`) if you want to join it.
- Send only to your own private chat with the bot (`chat_id`), never to a chat you are monitoring.

## Migration notes for the projects it was extracted from

The code was pulled out of four copies of the same logic in the author's projects (dialogue-lens,
get_cool_event and chat-watch). Differences from those copies:

* **`scan_messages` counts differently from get_cool_event's loop.** The old code incremented `scanned`
  and then broke on `>=`, so `MAX_SCAN_PER_RUN=500` yielded 499 messages; here `max_scan=500` yields
  exactly 500.
* **`resolve_credentials` gained the env fallback** (`TG_API_ID`/`TG_API_HASH`) that dialogue-lens'
  docstring promised but its code never implemented. Pass `cache_path` to keep the
  `data/.telegram.json` behaviour.
* **`export` no longer imports telethon**; `Channel`/`User`/`Message` are matched by class name. Exotic
  entity subclasses that used to satisfy `isinstance` now take the `user` / `group_chat` branch. No
  known Telethon type does this.
* Consumer tests fake the client object (see `tests/fakes.py`); nothing needs `telethon` patched.

## Tests

`tests/test_no_telethon.py` blocks `telethon` in the import system to prove that `push`, `export` and `reader` all import without it.
