"""The Telethon user-session read loop: client, entity, and a paced scan.

Extracted from two independently-written copies of the same loop
(dialogue-lens ``tg.py`` / ``sources/tg_fetch.py`` and get_cool_event
``adapters/telegram.py``, whose docstring already admitted the port). What they
actually shared is the *mechanics*; what differed is the target list and the
message→row mapping, so those are parameters.

``telethon`` is imported **lazily inside the functions**, so importing
``tg_toolkit`` costs nothing and ``tg_toolkit.push`` works on a machine without
telethon at all.

Invariants (the reason this module exists):

* **User session, never a bot token** — a bot cannot read arbitrary public
  channels or your own dialog history.
* **Pace requests**: ``wait_time=1.0`` between GetHistory calls keeps under
  Telegram's ~30s/10-request limit. ``flood_sleep_threshold`` lets Telethon
  swallow short waits; longer ones raise ``FloodWaitError`` and
  :func:`iter_flood_safe` sleeps the *server-mandated* seconds and resumes the
  **same** iterator. A FloodWait is a rate guard, not an error path.
* **Bounded scans**: newest-first iteration stops at the first message older
  than ``since`` (everything after it is older too), and ``max_scan`` is the
  backstop so a pathologically busy chat cannot scan forever inside the window.
  Without both, a first run degenerates into a full-history backfill.
* **Incremental**: ``min_id`` derived from the last id seen in the previous run
  (:func:`last_seen_msg_id`) — the server does the filtering, not us.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

#: Seconds Telethon waits between history requests (~30s/10-request limit).
DEFAULT_WAIT_TIME = 1.0
#: FloodWaits shorter than this are slept inside Telethon; longer ones raise.
DEFAULT_FLOOD_SLEEP_THRESHOLD = 10
#: Recency window for a channel collect — wide enough for announcements posted
#: weeks ahead, narrow enough that the first run is not a full backfill.
DEFAULT_WINDOW_DAYS = 60
#: Backstop: a busy chat cannot scan more than this per run even inside the window.
DEFAULT_MAX_SCAN = 500


# --- credentials -------------------------------------------------------------

def resolve_credentials(api_id: int | str | None = None, api_hash: str | None = None, *,
                        cache_path: str | Path | None = None,
                        env: dict | None = None) -> tuple[int, str]:
    """``(api_id, api_hash)`` from explicit args > cache file > env.

    Explicit args are cached to ``cache_path`` (JSON) so later runs are
    non-interactive. Env keys are ``TG_API_ID`` / ``TG_API_HASH``.
    """
    env = os.environ if env is None else env
    cache = Path(cache_path) if cache_path else None
    if api_id and api_hash:
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"api_id": int(api_id), "api_hash": api_hash}),
                             encoding="utf-8")
        return int(api_id), api_hash
    if cache and cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return int(d["api_id"]), d["api_hash"]
    if env.get("TG_API_ID") and env.get("TG_API_HASH"):
        return int(env["TG_API_ID"]), env["TG_API_HASH"]
    raise RuntimeError(
        "no Telegram api_id/api_hash — get them at https://my.telegram.org (Apps), "
        "then pass them once (or set TG_API_ID / TG_API_HASH)."
    )


# --- client & entities -------------------------------------------------------

def make_client(api_id: int, api_hash: str, session: str | Path, *,
                flood_sleep_threshold: int = DEFAULT_FLOOD_SLEEP_THRESHOLD):
    """A ``telethon.sync.TelegramClient`` (no event loop of our own).

    Use it as a context manager: ``with make_client(...) as client:``. First run
    is interactive (phone + login code); the session file makes later runs
    silent.
    """
    from telethon.sync import TelegramClient

    client = TelegramClient(str(session), int(api_id), api_hash)
    client.session.flood_sleep_threshold = flood_sleep_threshold
    return client


def resolve_entity(client, target: str | int):
    """Entity for a ``@handle``, a numeric id, or a chat title.

    Numeric-looking strings become ints; a leading ``@`` is stripped. An unseen
    id raises from ``get_entity``, so we prime the entity cache from the dialog
    list and retry once — the trick both source projects needed.
    """
    if isinstance(target, str):
        target = int(target) if target.lstrip("-").isdigit() else target.lstrip("@")
    try:
        return client.get_entity(target)
    except (ValueError, TypeError):
        list(client.iter_dialogs())  # prime the entity cache
        return client.get_entity(target)


def dialogs_by_name(client, names: Iterable[str]) -> list[tuple[object, str]]:
    """``[(entity, title)]`` for dialogs whose name case-folds into ``names``.

    A list of pairs, not a dict: Telethon entity objects are not hashable.
    """
    want = {n.casefold() for n in names}
    return [(d.entity, d.name) for d in client.iter_dialogs()
            if (d.name or "").casefold() in want]


# --- incrementality ----------------------------------------------------------

def last_seen_msg_id(last_seen_id: str | int | None) -> int:
    """Numeric ``min_id`` floor from a stored cursor. 0 means "from the start".

    Accepts a bare id or a namespaced one (``"telegram:<handle>:<msg_id>"`` —
    the trailing ``:``-token wins). Anything unparseable degrades to 0 (a full
    windowed scan) rather than raising: a corrupt cursor must not stop collection.
    """
    if not last_seen_id:
        return 0
    try:
        return int(str(last_seen_id).rsplit(":", 1)[-1])
    except ValueError:
        return 0


def window_cutoff(days: int = DEFAULT_WINDOW_DAYS, *, tz=None,
                  now: datetime | None = None) -> datetime:
    """``now - days`` as an aware datetime, for use as ``since``."""
    now = now or datetime.now(tz or timezone.utc)
    return now - timedelta(days=days)


# --- the paced scan ----------------------------------------------------------

TAKE, SKIP, STOP = "take", "skip", "stop"


def scan_decision(msg_date: datetime | None, scanned: int, *,
                  since: datetime | None = None, until: datetime | None = None,
                  max_scan: int | None = None) -> str:
    """Pure per-message verdict for a **newest-first** scan: TAKE / SKIP / STOP.

    * ``max_scan`` reached → STOP (backstop).
    * older than ``since`` → STOP (newest-first: everything after is older too).
    * at/after ``until`` → SKIP (server-side ``offset_date`` is inclusive-ish).
    * a message with no date is always TAKE — never let a weird record end a scan.
    """
    if max_scan is not None and scanned >= max_scan:
        return STOP
    if msg_date is not None:
        if since is not None and msg_date < since:
            return STOP
        if until is not None and msg_date >= until:
            return SKIP
    return TAKE


def iter_flood_safe(iterator: Iterator, *, sleep: Callable[[float], None] = time.sleep,
                    flood_error: type[BaseException] | None = None,
                    label: str = "") -> Iterator:
    """Drain ``iterator``, sleeping through FloodWaits and resuming the same one.

    ``flood_error`` defaults to ``telethon.errors.FloodWaitError`` (imported
    lazily); tests inject their own class.
    """
    if flood_error is None:
        from telethon.errors import FloodWaitError as flood_error  # noqa: N813
    while True:
        try:
            msg = next(iterator, None)
        except flood_error as exc:  # deliberate rate guard, not an error path
            secs = getattr(exc, "seconds", 0) or 0
            log.warning("%sFloodWait: sleeping %ss", f"{label} " if label else "", secs)
            sleep(secs)
            continue
        if msg is None:
            return
        yield msg


def scan_messages(client, entity, *, min_id: int = 0, since: datetime | None = None,
                  until: datetime | None = None, max_scan: int | None = None,
                  wait_time: float = DEFAULT_WAIT_TIME,
                  progress: Callable[[int], None] | None = None,
                  progress_every: int = 25, label: str = "",
                  sleep: Callable[[float], None] = time.sleep,
                  flood_error: type[BaseException] | None = None,
                  **iter_kwargs) -> Iterator:
    """Yield messages newest-first, incrementally, paced, and bounded.

    ``since``/``until`` bound the time window, ``min_id`` skips what a previous
    run already saw, ``max_scan`` is the hard backstop. Extra ``iter_kwargs`` go
    straight to ``client.iter_messages``.
    """
    if iter_kwargs.get("reverse") and (since is not None or until is not None):
        # Silent-failure guard: the since/until verdicts assume newest-first, so a
        # reverse (oldest-first) scan with since= would STOP on its very first message.
        raise ValueError(
            "since=/until= assume a newest-first scan; with reverse=True pass "
            "offset_date/limit via iter_kwargs instead"
        )
    kwargs = {"min_id": min_id, "wait_time": wait_time, **iter_kwargs}
    if until is not None:
        kwargs.setdefault("offset_date", until)
    it = iter(client.iter_messages(entity, **kwargs))
    scanned = 0
    for msg in iter_flood_safe(it, sleep=sleep, flood_error=flood_error, label=label):
        verdict = scan_decision(getattr(msg, "date", None), scanned,
                                since=since, until=until, max_scan=max_scan)
        if verdict == STOP:
            if max_scan is not None and scanned >= max_scan:
                log.warning("%shit max_scan=%d — stopping early", f"{label} " if label else "",
                            max_scan)
            break
        if verdict == SKIP:
            continue
        scanned += 1
        if progress and scanned % progress_every == 0:
            progress(scanned)
        yield msg


def collect(client, entity, mapper: Callable[[object], object | None], *,
            sort_key: Callable[[object], object] | None = None,
            **scan_kwargs) -> list:
    """:func:`scan_messages` → ``[mapper(msg)]``, dropping ``None`` results.

    ``mapper`` returning None is the skip hook (service messages, empty text).
    ``sort_key`` re-sorts the newest-first result — pass it when the caller needs
    the **last** row to carry the highest id (the next run's cursor).
    """
    rows = [row for msg in scan_messages(client, entity, **scan_kwargs)
            if (row := mapper(msg)) is not None]
    if sort_key is not None:
        rows.sort(key=sort_key)
    return rows


def read_messages(*, api_id: int, api_hash: str, session: str | Path,
                  target: str | int, mapper: Callable[[object], object | None],
                  flood_sleep_threshold: int = DEFAULT_FLOOD_SLEEP_THRESHOLD,
                  **collect_kwargs) -> list:
    """One-shot: connect, resolve ``target``, :func:`collect`, disconnect.

    The convenience path for a single chat. Callers reading **several** targets
    should open one client themselves (:func:`make_client`) and loop over
    :func:`collect` — one connection per channel is a waste and invites
    FloodWaits.
    """
    with make_client(api_id, api_hash, session,
                     flood_sleep_threshold=flood_sleep_threshold) as client:
        return collect(client, resolve_entity(client, target), mapper, **collect_kwargs)
