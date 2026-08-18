## Purpose

Deliver a short alert to the operator through a desktop toast and an optional
Telegram Bot API mirror, using only the standard library — so a project that
merely wants "ping me" can depend on this toolkit without a live user session.

## Requirements

### Requirement: Push is best-effort and never raises

Every delivery path MUST log its failures and swallow them. A failed push MUST
NOT propagate an exception into the caller's loop: notification is a side
channel, and the caller's work matters more. The desktop toast is the primary
channel and the bot is a mirror.

#### Scenario: A transport failure returns false instead of raising

- **WHEN** the Bot API call fails for any reason — network error, HTTP error, or
  a malformed response
- **THEN** `bot_send` logs a warning and returns `False`
- **Anchor**: `tests/test_push.py::test_bot_send_never_raises`

#### Scenario: A broken notify-send never crashes the caller

- **WHEN** `notify-send` is missing or exits with an error
- **THEN** the call completes and the failure is logged rather than raised
- **Anchor**: `tests/test_push.py::test_a_broken_notify_send_never_crashes_the_caller`

#### Scenario: A raising action callback never escapes

- **WHEN** the `on_action` callback raises
- **THEN** the exception is logged and does not surface on any caller thread
- **Anchor**: `tests/test_push.py::test_a_raising_callback_never_escapes`

### Requirement: Bot tokens are scrubbed from every log line

`scrub_token` MUST mask any `bot<digits>:<token>` sequence as `bot***`, and MUST
be applied to every message logged from a failed send. This is mandatory because
`urllib` embeds the full request URL — token included — in its exception
messages.

#### Scenario: A token in a URL is masked

- **WHEN** a string contains a Bot API URL with a real token
- **THEN** the token is replaced with `bot***`
- **Anchor**: `tests/test_push.py::test_scrub_token_masks_the_token_in_a_url`

#### Scenario: Every occurrence is masked

- **WHEN** a string contains more than one token
- **THEN** all of them are masked
- **Anchor**: `tests/test_push.py::test_scrub_token_masks_every_occurrence`

#### Scenario: Ordinary text is untouched

- **WHEN** a string contains no token pattern
- **THEN** it is returned unchanged
- **Anchor**: `tests/test_push.py::test_scrub_token_leaves_ordinary_text_alone`

#### Scenario: A failing send never logs the token

- **WHEN** `bot_send` fails with an exception whose message contains the request
  URL
- **THEN** no log record emitted by the call contains the token
- **Anchor**: `tests/test_push.py::test_a_failing_send_never_logs_the_token`

### Requirement: Messages are sent as plain text and truncated, not split

`bot_send` MUST POST JSON to `<api_base>/bot<token>/sendMessage` carrying
`chat_id` and `text` only. It MUST NOT set `parse_mode`, which sidesteps
markdown-escaping bugs, and text longer than 4000 characters MUST be truncated
rather than split across messages.

#### Scenario: The request body carries plain text

- **WHEN** `bot_send` posts a message
- **THEN** the JSON body contains `chat_id` and `text` with no `parse_mode`
- **Anchor**: `tests/test_push.py::test_bot_send_posts_plain_text_json`

#### Scenario: Overlong text is truncated

- **WHEN** the text exceeds the 4000-character limit
- **THEN** exactly the first 4000 characters are sent as a single message
- **Anchor**: `tests/test_push.py::test_bot_send_truncates_instead_of_splitting`

#### Scenario: The endpoint and timeout are configurable

- **WHEN** `bot_send` is given a custom `api_base` and `timeout`
- **THEN** the request targets that base and uses that timeout
- **Anchor**: `tests/test_push.py::test_bot_send_honours_a_custom_api_base_and_timeout`

### Requirement: The bot mirror never blocks the caller

The Bot API mirror MUST run in a daemon thread, so a slow or dead Bot API can
neither stall the caller's loop nor keep the process alive at exit. `send_async`
MUST return that thread, so a caller who wants to wait can join it.

#### Scenario: The mirror thread is a joinable daemon

- **WHEN** `send_async` is called
- **THEN** it returns a started daemon thread that can be joined
- **Anchor**: `tests/test_push.py::test_send_async_returns_a_joinable_daemon_thread`

### Requirement: The desktop toast is the primary channel

`notify` MUST emit a desktop toast via `notify-send`, tagged with an app name and
an urgency of `critical` when `urgent` is set and `normal` otherwise. The body
MUST be truncated to 500 characters, because `notify-send` silently drops
overlong bodies. `notify` MUST return the mirror thread, or `None` when no bot is
configured.

#### Scenario: A toast is shelled out with app name and urgency

- **WHEN** `notify` is called
- **THEN** `notify-send` is invoked with the app name, a `normal` urgency, and
  the title and body
- **Anchor**: `tests/test_push.py::test_notify_shells_out_to_notify_send`

#### Scenario: An urgent notification is critical

- **WHEN** `notify` is called with `urgent=True`
- **THEN** the urgency argument is `critical`
- **Anchor**: `tests/test_push.py::test_urgent_notify_is_critical`

#### Scenario: An overlong body is truncated for the toast

- **WHEN** the body exceeds 500 characters
- **THEN** only the first 500 characters reach `notify-send`
- **Anchor**: `tests/test_push.py::test_notify_truncates_the_toast_body`

#### Scenario: Without a bot, nothing is sent to Telegram

- **WHEN** `notify` is called with no bot configured
- **THEN** no Bot API request is made and the return value is `None`
- **Anchor**: `tests/test_push.py::test_notify_without_bot_sends_nothing`

#### Scenario: With a bot, the toast is mirrored

- **WHEN** `notify` is called with a `(token, chat_id)` pair
- **THEN** the same title and truncated body are also sent to the Bot API
- **Anchor**: `tests/test_push.py::test_notify_mirrors_to_the_bot_when_configured`

### Requirement: An action button can run a callback

`notify_with_action` MUST add one `notify-send -A` button labelled by the caller
and invoke `on_action` when the toast reports that button was clicked. Because
`-A` blocks until the toast is dismissed or clicked, the whole toast MUST run in
a daemon thread. A `notify-send` too old to support `-A` MUST degrade to a plain
toast. The phone mirror gets no button.

#### Scenario: Clicking the button runs the callback

- **WHEN** the toast subprocess reports that the action was selected
- **THEN** `on_action` is called exactly once
- **Anchor**: `tests/test_push.py::test_action_button_runs_the_callback_when_clicked`

#### Scenario: Dismissing the toast does not run the callback

- **WHEN** the toast is dismissed without the button being clicked
- **THEN** `on_action` is not called
- **Anchor**: `tests/test_push.py::test_action_button_ignored_when_the_toast_is_dismissed`

### Requirement: The bot chat id is the owner's private chat

The `chat_id` a caller supplies MUST be the owner's private bot chat and MUST NOT
be a monitored chat. This is a caller-side contract the library cannot enforce;
violating it leaks monitoring output back into the conversation being monitored.

#### Scenario: The constraint is stated wherever the push API is documented

- **WHEN** a caller reads the push API documentation
- **THEN** the rule that `chat_id` is the owner's private bot chat is stated as a
  hard rule
