## Purpose

Turn a human-supplied chat reference — an `@handle`, a numeric id, or a dialog
title — into the Telethon entity object the read loop needs, including the
dialog-cache priming that Telegram requires for entities the session has never
seen.

## Requirements

### Requirement: Targets are normalised before lookup

`resolve_entity` MUST accept a target as `@handle`, a bare handle, a numeric
string, or an `int`. A numeric-looking string (optionally sign-prefixed) MUST be
coerced to `int`; a leading `@` MUST be stripped.

#### Scenario: A handle loses its at-sign

- **WHEN** `resolve_entity` is called with `"@afisha"`
- **THEN** `get_entity` is called with `"afisha"`
- **Anchor**: `tests/test_reader.py::test_handle_is_stripped_of_at_sign`

#### Scenario: Numeric strings become ints

- **WHEN** `resolve_entity` is called with `"123"` or `"-100500"`
- **THEN** `get_entity` is called with the corresponding `int`, not the string
- **Anchor**: `tests/test_reader.py::test_numeric_targets_become_ints`

### Requirement: Unseen entities are retried after priming the dialog cache

Telethon raises for an id the session has not encountered. `resolve_entity` MUST
catch `ValueError`/`TypeError`, drain `client.iter_dialogs()` to prime the entity
cache, and retry `get_entity` exactly once. A second failure propagates. Both
source projects this library was extracted from needed this trick.

#### Scenario: The first lookup fails and the retry succeeds

- **WHEN** the first `get_entity` raises and the entity appears in the dialog list
- **THEN** the dialog list is drained exactly once and the retried `get_entity`
  returns the entity
- **Anchor**: `tests/test_reader.py::test_unseen_entity_is_retried_after_priming_the_dialog_cache`

#### Scenario: The retry is not repeated

- **WHEN** the retried `get_entity` also raises
- **THEN** the exception propagates to the caller rather than triggering a third
  attempt
- **Anchor**: `tests/test_reader.py::test_a_still_unknown_entity_is_not_retried_a_third_time`

### Requirement: Dialogs can be matched by display name

`dialogs_by_name` MUST return `[(entity, title)]` for every dialog whose name
case-folds into the requested set. It MUST return a list of pairs rather than a
dict, because Telethon entity objects are not hashable. A dialog with no name is
never matched.

#### Scenario: Matching ignores case and keeps pairs

- **WHEN** `dialogs_by_name` is asked for `"voice product"` and a dialog is named
  `"Voice Product"`
- **THEN** that dialog is returned as an `(entity, title)` pair carrying the
  dialog's original casing
- **Anchor**: `tests/test_reader.py::test_dialogs_by_name_is_case_insensitive_and_keeps_pairs`

#### Scenario: Names that match nothing are silently absent

- **WHEN** a requested name matches no dialog
- **THEN** the result simply omits it — no error is raised and no placeholder is
  returned
- **Anchor**: `tests/test_reader.py::test_dialogs_by_name_is_case_insensitive_and_keeps_pairs`
