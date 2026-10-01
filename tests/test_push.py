"""Bot API push + desktop toasts. No network: urllib and subprocess are faked."""

from __future__ import annotations

import json
import urllib.request

import pytest

from tg_toolkit import push
from tg_toolkit.push import (
    MAX_BODY,
    MAX_TEXT,
    bot_send,
    notify,
    notify_with_action,
    scrub_token,
)

TOKEN = "123456789:AAEeSomeSecretTokenValue-x"


class Capture:
    """Stand-in for urllib.request.urlopen: records the request, or blows up."""

    def __init__(self, exc: Exception | None = None):
        self.exc, self.req, self.timeout = exc, None, None

    def __call__(self, req, timeout=None):
        self.req, self.timeout = req, timeout
        if self.exc:
            raise self.exc
        return self

    def read(self):
        return b'{"ok":true}'


@pytest.fixture
def urlopen(monkeypatch):
    cap = Capture()
    monkeypatch.setattr(urllib.request, "urlopen", cap)
    return cap


# --- token scrubbing ---------------------------------------------------------

def test_scrub_token_masks_the_token_in_a_url():
    msg = f"HTTP Error 401: Unauthorized for url https://api.telegram.org/bot{TOKEN}/sendMessage"
    out = scrub_token(msg)
    assert "AAEeSomeSecretTokenValue" not in out
    assert "123456789" not in out
    assert "bot***" in out
    assert out.endswith("/sendMessage")


def test_scrub_token_leaves_ordinary_text_alone():
    assert scrub_token("connection refused") == "connection refused"
    assert scrub_token("") == ""


def test_scrub_token_masks_every_occurrence():
    out = scrub_token(f"bot{TOKEN} then bot{TOKEN}")
    assert out == "bot*** then bot***"


def test_a_failing_send_never_logs_the_token(monkeypatch, caplog):
    err = RuntimeError(f"boom https://api.telegram.org/bot{TOKEN}/sendMessage")
    monkeypatch.setattr(urllib.request, "urlopen", Capture(err))
    with caplog.at_level("WARNING"):
        assert bot_send(TOKEN, 5, "hi") is False
    assert caplog.text
    assert TOKEN not in caplog.text
    assert "bot***" in caplog.text


# --- bot_send ----------------------------------------------------------------

def test_bot_send_posts_plain_text_json(urlopen):
    assert bot_send(TOKEN, 42, "hello") is True
    req = urlopen.req
    assert req.full_url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert req.headers["Content-type"] == "application/json"
    body = json.loads(req.data)
    assert body == {"chat_id": 42, "text": "hello"}
    assert "parse_mode" not in body      # plain text on purpose
    assert urlopen.timeout == 10


def test_bot_send_truncates_instead_of_splitting(urlopen):
    bot_send(TOKEN, 1, "x" * (MAX_TEXT + 500))
    assert len(json.loads(urlopen.req.data)["text"]) == MAX_TEXT


def test_bot_send_honours_a_custom_api_base_and_timeout(urlopen):
    bot_send(TOKEN, 1, "hi", api_base="http://127.0.0.1:1", timeout=0.5)
    assert urlopen.req.full_url.startswith("http://127.0.0.1:1/bot")
    assert urlopen.timeout == 0.5


@pytest.mark.parametrize("exc", [OSError("unreachable"), ValueError("bad"),
                                 RuntimeError("nope")])
def test_bot_send_never_raises(monkeypatch, exc):
    monkeypatch.setattr(urllib.request, "urlopen", Capture(exc))
    assert bot_send(TOKEN, 1, "hi") is False


# --- notify ------------------------------------------------------------------

@pytest.fixture
def argv(monkeypatch):
    calls = []

    class Result:
        stdout = ""

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return Result()

    monkeypatch.setattr(push.subprocess, "run", fake_run)
    return calls


def test_notify_shells_out_to_notify_send(argv):
    notify("Title", "Body", app_name="chat-watch")
    cmd, kw = argv[0]
    assert cmd[:1] == ["notify-send"]
    assert "--app-name=chat-watch" in cmd
    assert "--urgency=normal" in cmd
    assert cmd[-2:] == ["Title", "Body"]
    assert kw["check"] is False        # a missing notify-send must not crash us


def test_urgent_notify_is_critical(argv):
    notify("T", "B", urgent=True)
    assert "--urgency=critical" in argv[0][0]


def test_notify_truncates_the_toast_body(argv):
    notify("T", "b" * (MAX_BODY + 100))
    assert len(argv[0][0][-1]) == MAX_BODY


def test_notify_without_bot_sends_nothing(argv, urlopen):
    notify("T", "B")
    assert urlopen.req is None


def test_notify_mirrors_to_the_bot_when_configured(argv, urlopen):
    notify("T", "B", bot=(TOKEN, 77)).join(timeout=2)   # returns the mirror thread
    body = json.loads(urlopen.req.data)
    assert body == {"chat_id": 77, "text": "T\nB"}


def test_send_async_returns_a_joinable_daemon_thread(urlopen):
    t = push.send_async(TOKEN, 1, "hi")
    assert t.daemon
    t.join(timeout=2)
    assert not t.is_alive()
    assert json.loads(urlopen.req.data)["text"] == "hi"


# --- notify_with_action ------------------------------------------------------

def _action_argv(monkeypatch, stdout: str):
    calls = []

    class Result:
        def __init__(self, out):
            self.stdout = out

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Result(stdout)

    monkeypatch.setattr(push.subprocess, "run", fake_run)
    return calls


def test_action_button_runs_the_callback_when_clicked(monkeypatch):
    calls = _action_argv(monkeypatch, "action\n")
    fired = []
    notify_with_action("T", "B", action_label="Collect",
                       on_action=lambda: fired.append(1)).join(timeout=2)
    assert fired == [1]
    assert "-A" in calls[0]
    assert "action=Collect" in calls[0]


def test_action_button_ignored_when_the_toast_is_dismissed(monkeypatch):
    _action_argv(monkeypatch, "")
    fired = []
    notify_with_action("T", "B", action_label="Collect",
                       on_action=lambda: fired.append(1)).join(timeout=2)
    assert fired == []


def test_a_broken_notify_send_never_crashes_the_caller(monkeypatch, caplog):
    def boom(cmd, **kw):
        raise FileNotFoundError("notify-send")

    monkeypatch.setattr(push.subprocess, "run", boom)
    with caplog.at_level("WARNING"):
        notify_with_action("T", "B", action_label="X", on_action=lambda: None).join(timeout=2)
    assert "failed" in caplog.text


def test_a_raising_callback_never_escapes(monkeypatch, caplog):
    _action_argv(monkeypatch, "action")

    def boom():
        raise ValueError("callback bug")

    with caplog.at_level("WARNING"):
        notify_with_action("T", "B", action_label="X", on_action=boom).join(timeout=2)
    assert "callback bug" in caplog.text


def test_notify_without_notify_send_installed_never_raises(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", "notify-send")

    monkeypatch.setattr(push.subprocess, "run", missing)
    assert notify("Title", "Body") is None
