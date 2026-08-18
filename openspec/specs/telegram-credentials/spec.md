## Purpose

Resolve the Telegram API credentials (`api_id`, `api_hash`) that a user session
needs, from an explicit argument, a JSON cache file, or the environment — so the
first run can be interactive and every later run is silent.

## Requirements

### Requirement: Credential precedence

`resolve_credentials` MUST return `(api_id: int, api_hash: str)` resolved in the
order explicit arguments > cache file > environment, and MUST coerce `api_id` to
`int` regardless of source.

#### Scenario: Explicit arguments win

- **WHEN** both `api_id` and `api_hash` are passed explicitly
- **THEN** they are returned, and neither the cache file nor the environment is
  consulted
- **Anchor**: `tests/test_reader.py::test_explicit_credentials_win_and_are_cached`

#### Scenario: The cache file is the second source

- **WHEN** no explicit arguments are given and `cache_path` points at an existing
  JSON file
- **THEN** `api_id` and `api_hash` are read from that file
- **Anchor**: `tests/test_reader.py::test_explicit_credentials_win_and_are_cached`

#### Scenario: The environment is the last resort

- **WHEN** no explicit arguments are given and no cache file exists
- **THEN** `TG_API_ID` and `TG_API_HASH` are read from the environment
- **Anchor**: `tests/test_reader.py::test_env_is_the_last_resort`

### Requirement: Explicit credentials are cached

When explicit credentials are supplied together with a `cache_path`,
`resolve_credentials` MUST write them to that path as JSON, creating parent
directories as needed, so later runs are non-interactive. Without a `cache_path`
nothing is written to disk.

#### Scenario: Credentials are persisted for later runs

- **WHEN** `resolve_credentials` is called with explicit credentials and a
  `cache_path` whose parent directory does not exist
- **THEN** the directory is created and the file holds `api_id` and `api_hash`,
  which a later call with no arguments reads back
- **Anchor**: `tests/test_reader.py::test_explicit_credentials_win_and_are_cached`

#### Scenario: Nothing is written without a cache path

- **WHEN** `resolve_credentials` is called with explicit credentials and no
  `cache_path`
- **THEN** no file is created anywhere on disk
- **Anchor**: `tests/test_reader.py::test_credentials_without_cache_path_do_not_touch_disk`

### Requirement: Missing credentials fail with an actionable error

When no source yields credentials, `resolve_credentials` MUST raise
`RuntimeError` naming both where to obtain credentials and the environment
variables that would satisfy the call.

#### Scenario: No source has credentials

- **WHEN** no explicit arguments, no cache file, and no `TG_API_ID`/`TG_API_HASH`
  are available
- **THEN** a `RuntimeError` is raised mentioning `my.telegram.org` and the
  environment variable names
- **Anchor**: `tests/test_reader.py::test_missing_credentials_raise_actionable_error`
