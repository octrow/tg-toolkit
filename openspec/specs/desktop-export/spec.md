## Purpose

Convert between live Telethon messages and the JSON shape that Telegram
Desktop's "Export chat history" writes, in both directions — so a parser written
against Desktop exports keeps working unchanged against a live fetch, and
archived exports can be read back into flat rows with no API access at all.

## Requirements

### Requirement: Entity offsets are UTF-16 code units

Telegram reports entity `offset` and `length` in UTF-16 code units, so
`text_segments` MUST slice through a `utf-16-le` encoding of the text. Slicing
the Python `str` directly MUST NOT be used: it misaligns every message
containing an emoji or other astral-plane character.

#### Scenario: Text around an emoji stays aligned

- **WHEN** a message contains an emoji before an entity
- **THEN** the entity's text is sliced correctly, matching what Telegram Desktop
  would emit rather than the shifted result a plain `str` slice gives
- **Anchor**: `tests/test_export.py::test_offsets_are_utf16_code_units_not_python_chars`

### Requirement: Message text splits into typed and plain segments

`text_segments` MUST return a list mixing plain `str` chunks and
`{"type": ..., "text": ...}` dicts, covering the whole message with no gaps and
no empty padding at the edges. Entities MUST be applied in ascending offset
order regardless of the order they arrive in.

#### Scenario: Empty text yields no segments

- **WHEN** the message text is empty
- **THEN** the result is `[]`
- **Anchor**: `tests/test_export.py::test_empty_text_has_no_segments`

#### Scenario: Text with no entities is a single plain chunk

- **WHEN** the message has no entities
- **THEN** the result is the whole text as one plain string
- **Anchor**: `tests/test_export.py::test_no_entities_is_one_plain_chunk`

#### Scenario: An entity splits the text into plain and typed chunks

- **WHEN** an entity covers the middle of the text
- **THEN** the result is the leading plain chunk, the typed dict, and the
  trailing plain chunk
- **Anchor**: `tests/test_export.py::test_entity_splits_into_plain_and_typed_chunks`

#### Scenario: An entity at the edge adds no empty padding

- **WHEN** an entity starts at offset 0 or ends at the last code unit
- **THEN** no empty string is emitted before or after it
- **Anchor**: `tests/test_export.py::test_entity_at_start_and_end_needs_no_empty_padding`

#### Scenario: Entities are sorted by offset

- **WHEN** entities are supplied out of order
- **THEN** the segments still appear in text order
- **Anchor**: `tests/test_export.py::test_entities_are_sorted_by_offset`

### Requirement: Overlapping entities are skipped, not merged

When an entity begins before the end of an already-emitted entity,
`text_segments` MUST skip it. Merging or nesting MUST NOT be attempted, because
duplicating the covered text would corrupt the reconstruction.

#### Scenario: A nested entity is dropped

- **WHEN** two entities overlap
- **THEN** only the first is annotated and the covered text appears exactly once
- **Anchor**: `tests/test_export.py::test_overlapping_entities_are_skipped_not_duplicated`

### Requirement: Entity type names match Telegram Desktop exactly

Telethon entity class names MUST map to Desktop's `text_entities` type names
through `ENTITY_TYPES`. The noise types a downstream parser strips by name —
`code`, `pre`, `link`, `mention`, `bot_command`, `hashtag` — MUST match
Desktop's spelling exactly, or the strip silently stops working. An entity class
absent from the mapping MUST degrade to plain text rather than raising.

#### Scenario: The strippable type names are pinned

- **WHEN** the mapping is inspected
- **THEN** it yields exactly Desktop's names for the strippable entity classes
- **Anchor**: `tests/test_export.py::test_noise_entity_names_match_desktop_exactly`

#### Scenario: An unmapped entity type degrades to plain text

- **WHEN** a message carries an entity class absent from `ENTITY_TYPES`
- **THEN** its text appears as a plain string chunk with no `type` annotation
- **Anchor**: `tests/test_export.py::test_unknown_entity_type_degrades_to_plain_text`

### Requirement: Telethon objects are duck-typed by class name

Message, entity, and chat objects MUST be recognised by class name and attribute
presence rather than by `isinstance`, so this capability imports no telethon and
is exercisable with plain fake objects.

#### Scenario: Plain fakes convert end to end

- **WHEN** conversion is driven by objects whose class names are `Message`,
  `Channel`, or `User` but which are not Telethon types
- **THEN** conversion, writing, and re-parsing all succeed
- **Anchor**: `tests/test_export.py::test_round_trip_telethon_to_rows`

### Requirement: Service messages convert to None

Any object whose class name is not exactly `Message` MUST convert to `None`,
which callers treat as a skip.

#### Scenario: A service message is dropped

- **WHEN** `to_desktop_message` receives a service-message object
- **THEN** it returns `None`
- **Anchor**: `tests/test_export.py::test_service_message_is_dropped`

### Requirement: A converted message carries Desktop's record shape

`to_desktop_message` MUST emit `id`, `type`, `date` (local time,
`%Y-%m-%dT%H:%M:%S`), `date_unixtime` (UTC seconds as a string), `from`,
`from_id`, `text`, and `text_entities`. `from_id` MUST be prefixed `channel` for
a channel sender and `user` otherwise, and MUST be `None` when there is no
sender id.

#### Scenario: A minimal message produces the base fields

- **WHEN** a message with text and a user sender is converted
- **THEN** the record carries every base field, with `from_id` of the form
  `user<id>`
- **Anchor**: `tests/test_export.py::test_minimal_message_record`

#### Scenario: A channel sender is prefixed accordingly

- **WHEN** the sender's class name is `Channel`
- **THEN** `from_id` has the form `channel<id>`
- **Anchor**: `tests/test_export.py::test_channel_sender_gets_channel_prefix`

#### Scenario: A missing sender id yields a null from_id

- **WHEN** the message has no sender id
- **THEN** `from_id` is `None`
- **Anchor**: `tests/test_export.py::test_missing_sender_id_yields_none_from_id`

### Requirement: Optional message fields appear only when present

`reply_to_message_id`, `edited`, `forwarded_from`, `media_type`, and `reactions`
MUST be emitted only when the source message carries the corresponding data,
matching Desktop's behaviour of omitting rather than nulling. A forward whose
original sender cannot be named MUST become `"unknown"`.

#### Scenario: Present optional data is emitted

- **WHEN** the message is a reply, is edited, is forwarded, carries media, and
  has reactions
- **THEN** all five optional fields appear in the record
- **Anchor**: `tests/test_export.py::test_optional_fields_are_emitted_when_present`

#### Scenario: An unnameable forward source becomes unknown

- **WHEN** the message is forwarded but the original sender resolves to no name
- **THEN** `forwarded_from` is `"unknown"`
- **Anchor**: `tests/test_export.py::test_forward_from_unknown_sender`

### Requirement: Senders resolve to a human name by precedence

`sender_name` MUST resolve a name in the order full name (first and/or last) >
title > username > string id, and MUST return `None` when the sender is `None`
or nothing identifies it.

#### Scenario: Each fallback applies in turn

- **WHEN** a sender carries a personal name, only a title, only a username, or
  only an id
- **THEN** the first available option in that order is returned, and `None` when
  there is no sender at all
- **Anchor**: `tests/test_export.py::test_sender_name`

### Requirement: Reactions collapse custom emoji

`reactions` MUST return `[{"count": int, "emoji": str}]`, using the reaction's
`emoticon` where present and the literal `"custom"` for a custom emoji reaction.
A message with no reactions MUST yield `[]`.

#### Scenario: A custom emoji reaction collapses

- **WHEN** a reaction carries no `emoticon`
- **THEN** its emoji is reported as `"custom"`
- **Anchor**: `tests/test_export.py::test_reactions_custom_emoji_collapses`

#### Scenario: No reactions yields an empty list

- **WHEN** the message has no reactions data
- **THEN** the result is `[]`
- **Anchor**: `tests/test_export.py::test_reactions_empty_when_absent`

### Requirement: A whole export is written in Desktop's envelope

`desktop_payload` MUST wrap messages in Desktop's `name`, `type`, `id`,
`messages` envelope, where `type` is `personal_chat` for a `User` entity and
`group_chat` otherwise. `write_export` MUST create parent directories and write
UTF-8 JSON with `indent=1` and unescaped non-ASCII, as Desktop does.

#### Scenario: Chat type follows the entity class

- **WHEN** the entity's class name is `User`
- **THEN** `type` is `personal_chat`; for any other entity class it is
  `group_chat`
- **Anchor**: `tests/test_export.py::test_chat_type_by_entity_class`

#### Scenario: The envelope carries name, type, id and messages

- **WHEN** `desktop_payload` wraps a list of converted messages
- **THEN** the payload has Desktop's four top-level keys
- **Anchor**: `tests/test_export.py::test_desktop_payload_shape`

#### Scenario: The export file is written where asked

- **WHEN** `write_export` targets a path whose directory does not exist
- **THEN** the directory is created and the returned path holds the JSON payload
- **Anchor**: `tests/test_export.py::test_round_trip_telethon_to_rows`

### Requirement: Exports parse back into flat rows

`parse_export` MUST read one Desktop `result.json` into rows of `ROW_FIELDS`
(`chat`, `date`, `from`, `from_id`, `text`), dropping any record whose `type` is
not `message`. `flatten_text` MUST reduce Desktop's `text` — a string, or a list
of strings and `{"text": ...}` dicts — to one plain string.

#### Scenario: Service records are dropped on parse

- **WHEN** an export contains records whose `type` is not `message`
- **THEN** those records produce no rows
- **Anchor**: `tests/test_export.py::test_parse_export_drops_service_messages`

#### Scenario: Segmented text flattens to one string

- **WHEN** a record's `text` is a list mixing plain strings and typed dicts
- **THEN** the row's `text` is their concatenation in order
- **Anchor**: `tests/test_export.py::test_flatten_text`

### Requirement: Many exports merge into one date-ordered result

`parse_exports` MUST accept either a glob pattern string or an iterable of
paths, concatenate their rows, and sort by `date`. A missing date MUST sort as
an empty string rather than raising.

#### Scenario: Rows from several files interleave by date

- **WHEN** two exports are parsed together
- **THEN** the combined rows are ordered by their `date` field
- **Anchor**: `tests/test_export.py::test_parse_exports_merges_and_sorts_by_date`

#### Scenario: A record without a date does not break the sort

- **WHEN** a record has no `date`
- **THEN** it sorts as if its date were empty and no exception is raised
- **Anchor**: `tests/test_export.py::test_parse_exports_tolerates_missing_date`

#### Scenario: Parsing nothing yields nothing

- **WHEN** the glob matches no files or the iterable is empty
- **THEN** the result is `[]`
- **Anchor**: `tests/test_export.py::test_parse_exports_of_nothing_is_empty`
