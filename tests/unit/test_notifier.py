from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from dji_auto_upload.config import Config, NotifierConfig, TelegramCredentials
from dji_auto_upload.notifier import (
    NullNotifier,
    TelegramNotifier,
    escape,
    make_notifier,
)


def test_make_notifier_null_when_disabled() -> None:
    cfg = Config()
    assert isinstance(make_notifier(cfg), NullNotifier)


def test_make_notifier_null_when_no_credentials() -> None:
    cfg = Config(notifier=NotifierConfig(enabled=True))
    assert isinstance(make_notifier(cfg), NullNotifier)


def test_make_notifier_telegram_when_configured() -> None:
    cfg = Config(
        notifier=NotifierConfig(enabled=True),
        telegram=TelegramCredentials(bot_token="t", chat_id="c"),
    )
    assert isinstance(make_notifier(cfg), TelegramNotifier)


def test_telegram_notifier_skips_unenabled_events() -> None:
    n = TelegramNotifier(bot_token="t", chat_id="c", enabled_events={"fail"})
    with patch("dji_auto_upload.notifier.requests.post") as post:
        n.send("info", "hi")
        post.assert_not_called()


def test_telegram_notifier_posts_enabled_events() -> None:
    n = TelegramNotifier(bot_token="t", chat_id="c", enabled_events={"fail"})
    with patch("dji_auto_upload.notifier.requests.post") as post:
        post.return_value.ok = True
        n.send("fail", "boom")
        post.assert_called_once()


def test_telegram_notifier_swallows_network_errors() -> None:
    import requests

    n = TelegramNotifier(bot_token="t", chat_id="c", enabled_events={"fail"})
    with patch("dji_auto_upload.notifier.requests.post", side_effect=requests.RequestException("net")):
        n.send("fail", "boom")  # must not raise


def test_escape_preserves_safe_chars() -> None:
    assert escape("plain") == "plain"
    assert "&lt;" in escape("<script>")


def test_bot_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    """requests embeds the request URL (and thus the token) in its exception
    strings. Those log lines are world-readable and get tailed into the `fail`
    notification, so the token must be redacted before it is logged."""
    import requests

    token = "123456:SUPERSECRETTOKEN"
    n = TelegramNotifier(bot_token=token, chat_id="c", enabled_events={"fail"})
    boom = requests.RequestException(
        f"Max retries exceeded with url: /bot{token}/sendMessage"
    )
    with caplog.at_level(logging.WARNING), patch(
        "dji_auto_upload.notifier.requests.post", side_effect=boom
    ):
        n.send("fail", "boom")

    assert "SUPERSECRETTOKEN" not in caplog.text
    assert "<redacted>" in caplog.text


def test_bot_token_is_redacted_from_error_response_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "123456:SUPERSECRETTOKEN"
    n = TelegramNotifier(bot_token=token, chat_id="c", enabled_events={"fail"})
    with caplog.at_level(logging.WARNING), patch(
        "dji_auto_upload.notifier.requests.post"
    ) as post:
        post.return_value.ok = False
        post.return_value.status_code = 401
        post.return_value.text = f"Unauthorized for bot{token}"
        n.send("fail", "boom")

    assert "SUPERSECRETTOKEN" not in caplog.text
