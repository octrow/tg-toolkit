# Usage

Examples for each module. The first one is also in the [README](../README.md).

Incremental channel collect:

```python
from tg_toolkit import last_seen_msg_id, read_messages, window_cutoff

def to_post(msg):
    text = (msg.message or "").strip()
    if not text:
        return None                       # returning None skips the message
    return RawPost(post_id=f"telegram:{handle}:{msg.id}", text=text, ...)

posts = read_messages(
    api_id=api_id, api_hash=api_hash, session=session, target=f"@{handle}",
    mapper=to_post,
    min_id=last_seen_msg_id(cursor),      # incremental
    since=window_cutoff(60),              # recency window, days
    max_scan=500,                         # backstop
    label=f"@{handle}",
    sort_key=lambda p: int(p.post_id.rsplit(":", 1)[-1]),   # last row = new cursor
)
```

Several chats over one connection. Prefer this to calling `read_messages` once per chat:

```python
from tg_toolkit import collect, dialogs_by_name, make_client

with make_client(api_id, api_hash, session) as client:
    for entity, title in dialogs_by_name(client, chats):
        rows += collect(client, entity, lambda m: to_row(title, m),
                        since=since_dt, until=until_dt)
```

Live fetch in the Desktop export shape, so a parser written for offline exports keeps working:

```python
from tg_toolkit import (desktop_payload, make_client, resolve_entity,
                        scan_messages, to_desktop_message, write_export)

with make_client(api_id, api_hash, session) as client:
    entity = resolve_entity(client, chat)
    # reverse=True gives chronological order; extra kwargs go straight to iter_messages
    msgs = [r for m in scan_messages(client, entity, reverse=True, limit=limit)
            if (r := to_desktop_message(m)) is not None]
write_export(desktop_payload(entity, msgs), out_dir / "result.json")
```

Offline, no API at all:

```python
from tg_toolkit import parse_exports     # -> rows: chat, date, from, from_id, text
rows = parse_exports("data/*/result.json")
```

Push, no telethon needed:

```python
from tg_toolkit import bot_send, notify, notify_with_action

notify("New mention", body, urgent=True, app_name="chat-watch", bot=(token, owner_id))
notify_with_action("New mention", body, action_label="Collect",
                   on_action=lambda: launch_investigate(ref), bot=(token, owner_id))
bot_send(token, owner_id, "plain text")   # -> bool, never raises
```
