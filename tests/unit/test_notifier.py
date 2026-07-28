from __future__ import annotations

from unittest.mock import patch

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
