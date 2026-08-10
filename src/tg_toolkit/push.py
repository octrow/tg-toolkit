"""Best-effort push notifications: Telegram Bot API + desktop notify-send.

**stdlib only** — this module never imports telethon (nor anything else
third-party), so a project that only wants "ping my phone" can depend on
tg-toolkit without a live user session. Extracted from chat-watch
(``bot_send`` / ``notify`` / ``_notify_collect`` / ``_scrub_token``).

Invariants kept from the source:

* Push is **best-effort**: every failure is logged and swallowed, never raised.
  The desktop toast is the primary channel; the bot is a mirror.
* Bot tokens are **scrubbed** out of every log line (``scrub_token``) — urllib
  puts the full URL in its exception messages.
* Plain text, no ``parse_mode`` — sidesteps markdown-escaping bugs.
* The bot mirror runs in a **daemon thread**, so a slow/dead Bot API can never
  block the caller's loop.
* HARD RULE (caller's): ``chat_id`` is the owner's private-bot chat, never a
  monitored chat.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

BOT_API = "https://api.telegram.org"

#: Bot API sendMessage hard limit; longer text is truncated, not split.
MAX_TEXT = 4000

#: Desktop toasts get a shorter body — notify-send silently drops long ones.
MAX_BODY = 500

_TOKEN_RE = re.compile(r"bot\d+:[\w-]+")


def scrub_token(s: str) -> str:
    """Mask a bot token wherever it might surface in a log line / error message."""
    return _TOKEN_RE.sub("bot***", s)


def bot_send(token: str, chat_id: int, text: str, *,
             api_base: str = BOT_API, timeout: float = 10) -> bool:
    """POST one plain-text message via the Bot API. Never raises; True on success."""
    import urllib.request

    body = json.dumps({"chat_id": chat_id, "text": text[:MAX_TEXT]}).encode()
    req = urllib.request.Request(
        f"{api_base}/bot{token}/sendMessage", data=body,
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception as exc:  # noqa: BLE001 — push is best-effort
        log.warning("bot_send failed: %s: %s", type(exc).__name__, scrub_token(str(exc)))
        return False


def send_async(token: str, chat_id: int, text: str, **kw) -> threading.Thread:
    """Fire-and-forget :func:`bot_send` in a daemon thread. Returns the thread."""
    t = threading.Thread(target=bot_send, args=(token, chat_id, text),
                         kwargs=kw, daemon=True)
    t.start()
    return t


def notify(title: str, body: str, *, urgent: bool = False,
           app_name: str = "tg-toolkit",
           bot: tuple[str, int] | None = None) -> threading.Thread | None:
    """Desktop toast (blocking, cheap) + optional threaded phone mirror.

    ``bot`` is ``(token, chat_id)`` or None to skip the mirror. Returns the
    mirror thread (or None), so a caller that wants to wait can join it.
    """
    log.debug("notify urgent=%s title=%r", urgent, title)
    thread = send_async(bot[0], bot[1], f"{title}\n{body[:MAX_BODY]}") if bot else None
    subprocess.run(_notify_send_argv(title, body, urgent=urgent, app_name=app_name),
                   check=False)
    return thread


def notify_with_action(title: str, body: str, *, action_label: str,
                       on_action: Callable[[], None], urgent: bool = False,
                       app_name: str = "tg-toolkit",
                       bot: tuple[str, int] | None = None) -> threading.Thread:
    """:func:`notify` plus one action button that runs ``on_action`` when clicked.

    ``notify-send -A`` blocks until the toast is dismissed or clicked, so the
    whole thing runs in a daemon thread (returned, for tests). Degrades to a
    plain toast on an old notify-send without ``-A``; never crashes, and
    ``on_action`` failures are logged, not raised. The phone mirror gets no
    button.
    """
    if bot:
        send_async(bot[0], bot[1], f"{title}\n{body[:MAX_BODY]}")

    argv = _notify_send_argv(title, body, urgent=urgent, app_name=app_name,
                             action=f"action={action_label}")

    def _run() -> None:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, check=False)
            if r.stdout.strip() == "action":
                on_action()
        except Exception as exc:  # noqa: BLE001 — the button is a convenience
            log.warning("notify action %r failed: %s: %s",
                        action_label, type(exc).__name__, exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _notify_send_argv(title: str, body: str, *, urgent: bool, app_name: str,
                      action: str | None = None) -> list[str]:
    argv = ["notify-send", f"--app-name={app_name}",
            f"--urgency={'critical' if urgent else 'normal'}"]
    if action:
        argv += ["-A", action]
    return [*argv, title, body[:MAX_BODY]]
