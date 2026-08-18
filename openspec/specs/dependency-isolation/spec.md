## Purpose

Keep the single third-party dependency (`telethon`) out of every import path
that does not need a live user session — so a project that only pushes
notifications or parses archived exports can depend on this toolkit on a machine
where telethon is not installed at all.

## Requirements

### Requirement: The package imports without telethon installed

Importing `tg_toolkit` and any of its modules MUST succeed when `telethon` is
absent from the environment. No module may import telethon at module scope.

#### Scenario: The package and the reader import with telethon blocked

- **WHEN** `telethon` is made unimportable and `tg_toolkit` and
  `tg_toolkit.reader` are imported
- **THEN** both imports succeed and the public names are available
- **Anchor**: `tests/test_no_telethon.py::test_the_package_and_reader_import_without_telethon`

#### Scenario: Importing the reader does not pull telethon in

- **WHEN** `tg_toolkit.reader` is imported in a normal environment
- **THEN** telethon is not bound in the module namespace
- **Anchor**: `tests/test_reader.py::test_importing_reader_does_not_import_telethon`

#### Scenario: The block itself is verified

- **WHEN** the test harness blocks `telethon` in the import system
- **THEN** importing telethon directly fails, proving the other assertions mean
  what they claim
- **Anchor**: `tests/test_no_telethon.py::test_telethon_is_really_blocked`

### Requirement: Export and push never import telethon at all

`tg_toolkit.export` and `tg_toolkit.push` MUST be usable end to end with
telethon absent. `push` MUST use only the standard library, and `export` MUST
recognise Telethon objects by class name and attribute rather than by import.

#### Scenario: Push works with telethon blocked

- **WHEN** telethon is unimportable
- **THEN** `tg_toolkit.push` imports and sends notifications normally
- **Anchor**: `tests/test_no_telethon.py::test_push_imports_and_works_without_telethon`

#### Scenario: Export works with telethon blocked

- **WHEN** telethon is unimportable
- **THEN** `tg_toolkit.export` imports and converts and parses messages normally
- **Anchor**: `tests/test_no_telethon.py::test_export_is_pure_and_imports_without_telethon`

#### Scenario: Export does not pull telethon in

- **WHEN** `tg_toolkit.export` is imported in a normal environment
- **THEN** telethon is not bound in the module namespace
- **Anchor**: `tests/test_export.py::test_import_does_not_pull_telethon`

### Requirement: The reader imports telethon lazily, at call time

Functions in `tg_toolkit.reader` that genuinely need telethon MUST import it
inside the function body. The absence of telethon MUST surface only when such a
function is actually called, and MUST then fail loudly rather than degrading
silently.

#### Scenario: Client construction fails only when called

- **WHEN** telethon is unimportable
- **THEN** importing `make_client` succeeds and calling it raises an import error
- **Anchor**: `tests/test_no_telethon.py::test_make_client_fails_loudly_only_when_called`

#### Scenario: Flood handling can be driven without telethon

- **WHEN** a caller supplies its own `flood_error` type
- **THEN** `iter_flood_safe` runs without importing telethon
- **Anchor**: `tests/test_reader.py::test_flood_wait_sleeps_the_server_mandated_seconds_and_resumes`

### Requirement: Consumers can be tested without telethon or a session

The client, entity, and message objects this library consumes MUST be duck-typed
so a consumer's tests can substitute plain fake objects. Tests MUST NOT require
patching telethon, a session file, or network access.

#### Scenario: A fake client drives a full scan

- **WHEN** a scan runs against an object exposing `iter_messages`, `get_entity`,
  and `iter_dialogs`
- **THEN** it behaves as it would against a real Telethon client
- **Anchor**: `tests/test_reader.py::test_read_messages_opens_and_closes_the_client`
