"""Notification dispatch: Telegram (HTML) and a Null backend.

The pipeline emits semantic events (`start`, `done_copy`, `done_upload`, ...).
The notifier filters these against `config.notifier.events` so users can mute
chatter in their config without code changes. Failures are logged, never
raised — a notification problem must never tank an offload run.
"""

from __future__ import annotations

import html
import logging
from typing import Protocol

import requests

from .config import Config

log = logging.getLogger(__name__)

ALL_EVENTS = (
    "start",
    "copy",
    "done_copy",
    "upload",
    "done_upload",
    "cleanup",
    "done",
    "fail",
    "info",
)


class Notifier(Protocol):
    def send(self, event: str, message: str) -> None: ...


class NullNotifier:
    def send(self, event: str, message: str) -> None:
        return None


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, enabled_events: set[str]) -> None:
        self._token = bot_token
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._enabled = enabled_events

    def _redact(self, text: str) -> str:
        """Strip the bot token out of anything we are about to log.

        requests puts the full request URL in its exception strings, and that
        URL embeds the token. Those log lines are world-readable and get tailed
        into the `fail` notification, so an unredacted token would leak twice.
        """
        if self._token:
            text = text.replace(self._token, "<redacted>")
        return text

    def send(self, event: str, message: str) -> None:
        if event not in self._enabled:
            return
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        try:
            r = requests.post(self._url, data=payload, timeout=10)
            if not r.ok:
                log.warning(
                    "telegram send failed: HTTP %s — %s",
                    r.status_code,
                    self._redact(r.text[:200]),
                )
        except requests.RequestException as exc:
            log.warning("telegram send failed: %s", self._redact(str(exc)))


def make_notifier(config: Config) -> Notifier:
    if not config.notifier.enabled or config.notifier.backend != "telegram":
        return NullNotifier()
    if not config.telegram.configured:
        return NullNotifier()
    return TelegramNotifier(
        bot_token=config.telegram.bot_token,
        chat_id=config.telegram.chat_id,
        enabled_events=set(config.notifier.events),
    )


def escape(s: str) -> str:
    """HTML-escape a string for inclusion in a Telegram message."""
    return html.escape(s)
