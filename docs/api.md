# API

Signatures of the public functions.

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

`resolve_credentials` looks in this order: explicit args (cached if you pass `cache_path`), then the
cache file, then `TG_API_ID` / `TG_API_HASH`. Without `cache_path` nothing is written to disk.
