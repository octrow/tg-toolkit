## Purpose

Read messages from a Telegram user session in a way that is paced against the
rate limit, bounded in both time and volume, and incremental across runs — so a
repeated collect never degenerates into a full-history backfill and a FloodWait
never loses data.

## Requirements

### Requirement: Reading uses a user session, never a bot token

The read loop MUST operate through a `telethon.sync.TelegramClient` built from a
user `api_id`/`api_hash` and session file, used as a context manager. A bot token
MUST NOT be used, because a bot cannot read arbitrary public channels or a user's
own dialog history.

#### Scenario: The client is built from user credentials

- **WHEN** `make_client(api_id, api_hash, session)` is called
- **THEN** a `telethon.sync.TelegramClient` is constructed against that session
  path, carrying the configured `flood_sleep_threshold`, with no bot token
  involved and no event loop of the library's own
- **Anchor**: `tests/test_reader.py::test_make_client_builds_a_user_session_client`

### Requirement: Requests are paced under the rate limit

Every history request MUST carry a `wait_time` (default `1.0` second) so a run
stays under Telegram's approximately 30-seconds-per-10-requests limit.

#### Scenario: The pace and the cursor reach Telethon

- **WHEN** `scan_messages` is called with default arguments and a `min_id`
- **THEN** `client.iter_messages` receives `wait_time=1.0` and that `min_id`
- **Anchor**: `tests/test_reader.py::test_pacing_and_min_id_reach_telethon`

### Requirement: FloodWait is a rate guard, not an error path

`make_client` MUST set a `flood_sleep_threshold` (default `10`) so short waits
are absorbed inside Telethon. Longer waits surface as `FloodWaitError`, and
`iter_flood_safe` MUST sleep the server-mandated number of seconds and then
resume **the same** iterator, so no message is lost across the wait.

#### Scenario: Messages pass through when nothing floods

- **WHEN** the iterator raises nothing
- **THEN** every message is yielded in order and nothing sleeps
- **Anchor**: `tests/test_reader.py::test_iter_flood_safe_passes_messages_through`

#### Scenario: A FloodWait is slept through and the scan resumes

- **WHEN** the message iterator raises a `FloodWaitError` carrying `seconds=N`
- **THEN** the loop sleeps exactly `N` seconds and continues draining the same
  iterator, yielding every remaining message
- **Anchor**: `tests/test_reader.py::test_flood_wait_sleeps_the_server_mandated_seconds_and_resumes`

#### Scenario: Repeated FloodWaits are each honoured

- **WHEN** the iterator raises `FloodWaitError` more than once during a scan
- **THEN** every wait is slept in turn and the scan still completes
- **Anchor**: `tests/test_reader.py::test_repeated_flood_waits_all_get_slept`

#### Scenario: Other exceptions are not swallowed

- **WHEN** the iterator raises an exception that is not the FloodWait type
- **THEN** it propagates to the caller unchanged
- **Anchor**: `tests/test_reader.py::test_non_flood_exception_is_not_swallowed`

#### Scenario: A full scan survives a FloodWait

- **WHEN** a `scan_messages` run hits a FloodWait partway through
- **THEN** the run completes and yields the messages from both sides of the wait
- **Anchor**: `tests/test_reader.py::test_scan_survives_a_flood_wait`

### Requirement: Scans are bounded by time and by volume

`scan_decision` MUST return exactly one of `take`, `skip`, or `stop` for a
**newest-first** scan, applying: `max_scan` reached → `stop`; message older than
`since` → `stop`; message at or after `until` → `skip`; otherwise `take`.
`max_scan` MUST take precedence over every other rule. Without both bounds a
first run degenerates into a full-history backfill.

#### Scenario: An in-window message is taken

- **WHEN** a message falls inside every configured bound
- **THEN** the verdict is `take`
- **Anchor**: `tests/test_reader.py::test_take_by_default`

#### Scenario: A message older than the window ends the scan

- **WHEN** a message's date is earlier than `since`
- **THEN** the verdict is `stop`, because in a newest-first scan everything after
  it is older too
- **Anchor**: `tests/test_reader.py::test_message_older_than_since_stops_the_scan`

#### Scenario: A message exactly at the window edge is kept

- **WHEN** a message's date equals `since`
- **THEN** the verdict is `take`
- **Anchor**: `tests/test_reader.py::test_message_at_since_is_taken`

#### Scenario: A message at or after the upper bound is skipped, not stopped

- **WHEN** a message's date is at or after `until`
- **THEN** the verdict is `skip` and the scan continues on to older messages
- **Anchor**: `tests/test_reader.py::test_message_at_or_after_until_is_skipped_not_stopped`

#### Scenario: The backstop stops the scan at exactly the cap

- **WHEN** `max_scan` messages have already been taken
- **THEN** the verdict is `stop`, so a scan with `max_scan=500` yields exactly
  500 messages and logs that the cap was hit
- **Anchor**: `tests/test_reader.py::test_max_scan_stops_exactly_at_the_cap`

#### Scenario: The backstop outranks the other bounds

- **WHEN** the cap is reached on a message that would otherwise be taken or
  skipped
- **THEN** the verdict is `stop`
- **Anchor**: `tests/test_reader.py::test_max_scan_wins_over_everything_else`

#### Scenario: A dateless message never ends a scan

- **WHEN** a message has no date
- **THEN** the verdict is `take`, so a malformed record cannot terminate
  collection
- **Anchor**: `tests/test_reader.py::test_dateless_message_is_never_a_stop`

#### Scenario: The bounds compose in a real scan

- **WHEN** a scan is run with `since`, `min_id`, and `max_scan` together
- **THEN** each bound applies and the result respects all three
- **Anchor**: `tests/test_reader.py::test_since_and_min_id_and_max_scan_compose`

### Requirement: Scans are incremental by server-side min_id

The cursor from the previous run MUST be passed to Telethon as `min_id` so the
server does the filtering, not the client. `last_seen_msg_id` MUST accept a bare
id or a namespaced cursor (`"telegram:<handle>:<msg_id>"`, where the trailing
`:`-token wins) and MUST degrade an unparseable or empty cursor to `0` rather
than raising — a corrupt cursor must not stop collection.

#### Scenario: A namespaced cursor yields its message id

- **WHEN** `last_seen_msg_id("telegram:afisha:1234")` is called
- **THEN** it returns `1234`
- **Anchor**: `tests/test_reader.py::test_last_seen_msg_id`

#### Scenario: A corrupt or empty cursor degrades to a full windowed scan

- **WHEN** the stored cursor is unparseable, empty, or `None`
- **THEN** `last_seen_msg_id` returns `0` and no exception is raised
- **Anchor**: `tests/test_reader.py::test_last_seen_msg_id`

### Requirement: A recency window bounds the first run

`window_cutoff(days)` MUST return an aware `now - days` datetime suitable as
`since`, defaulting to 60 days — wide enough for announcements posted weeks
ahead, narrow enough that a first run is not a full backfill.

#### Scenario: The cutoff is now minus the requested days

- **WHEN** `window_cutoff` is called with an explicit `now`
- **THEN** it returns `now - days`, defaulting to the 60-day window
- **Anchor**: `tests/test_reader.py::test_window_cutoff_is_now_minus_days`

### Requirement: The upper bound is pushed server-side

When `until` is given and the caller has not supplied its own `offset_date`,
`scan_messages` MUST pass `until` to Telethon as `offset_date` while still
applying the local `skip` verdict, because the server-side bound is only
approximately inclusive.

#### Scenario: until becomes offset_date and still filters locally

- **WHEN** `scan_messages` is called with an `until` bound
- **THEN** `iter_messages` receives it as `offset_date`, and messages at or after
  it that still arrive are skipped locally
- **Anchor**: `tests/test_reader.py::test_until_becomes_offset_date_and_still_filters_locally`

#### Scenario: A since bound halts at the first older message

- **WHEN** a scan with a `since` bound reaches a message older than it
- **THEN** the scan stops there and later messages are never requested
- **Anchor**: `tests/test_reader.py::test_since_stops_the_scan_at_the_first_older_message`

#### Scenario: The backstop bounds a busy chat

- **WHEN** a chat has more in-window messages than `max_scan`
- **THEN** the scan yields exactly `max_scan` messages and stops
- **Anchor**: `tests/test_reader.py::test_max_scan_backstop_bounds_a_busy_chat`

### Requirement: Reverse scans reject newest-first bounds

`since` and `until` assume a newest-first scan, so a reverse scan combined with
either would stop on its very first message. `scan_messages` MUST raise
`ValueError` for that combination rather than failing silently.

#### Scenario: A reverse scan with a newest-first bound is rejected

- **WHEN** `scan_messages` is called with `reverse=True` and `since` or `until`
- **THEN** a `ValueError` is raised naming `offset_date`/`limit` as the
  alternative
- **Anchor**: `tests/test_reader.py::test_reverse_scan_rejects_newest_first_bounds`

### Requirement: Extra keyword arguments reach Telethon unchanged

Keyword arguments `scan_messages` does not model MUST be forwarded verbatim to
`client.iter_messages`, so callers can use Telethon features the wrapper does not
cover.

#### Scenario: An unmodelled option is forwarded

- **WHEN** `scan_messages` is called with an extra `iter_messages` keyword
- **THEN** `iter_messages` receives it unchanged
- **Anchor**: `tests/test_reader.py::test_extra_kwargs_are_forwarded_to_iter_messages`

### Requirement: Long scans report progress

When a `progress` callback is supplied, `scan_messages` MUST call it with the
running count of taken messages every `progress_every` messages (default 25).
Skipped messages MUST NOT advance the count.

#### Scenario: Progress fires on every nth taken message

- **WHEN** a scan takes messages past a multiple of `progress_every`
- **THEN** the callback is called with each multiple in turn
- **Anchor**: `tests/test_reader.py::test_progress_fires_every_n_taken_messages`

### Requirement: Collecting maps messages to rows and drops skips

`collect` MUST apply a `mapper` to each scanned message and drop `None` results —
returning `None` is the caller's skip hook for service messages and empty text.
When `sort_key` is given the result MUST be re-sorted, so the caller can arrange
for the **last** row to carry the highest id as the next run's cursor.

#### Scenario: A mapper returning None drops the message

- **WHEN** the mapper returns `None` for some messages
- **THEN** those rows are absent from the result and no placeholder is inserted
- **Anchor**: `tests/test_reader.py::test_collect_maps_and_drops_none`

#### Scenario: A sort key puts the new cursor last

- **WHEN** `collect` is given a `sort_key` over message id
- **THEN** the returned rows ascend by id, so the last row is the next cursor
- **Anchor**: `tests/test_reader.py::test_collect_sort_key_puts_the_new_cursor_last`

#### Scenario: An empty chat collects to an empty list

- **WHEN** the scan yields no messages
- **THEN** `collect` returns `[]`
- **Anchor**: `tests/test_reader.py::test_collect_of_an_empty_chat_is_empty`

### Requirement: One-shot reads open and close their own client

`read_messages` MUST connect, resolve the target, collect, and disconnect in a
single call. Callers reading several targets are directed to open one client
themselves and loop over `collect`, because one connection per channel wastes the
connection and invites FloodWaits.

#### Scenario: The client is entered and exited around a one-shot read

- **WHEN** `read_messages` completes
- **THEN** the client has been both entered and exited, and the mapped rows are
  returned
- **Anchor**: `tests/test_reader.py::test_read_messages_opens_and_closes_the_client`
