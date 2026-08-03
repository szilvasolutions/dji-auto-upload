"""Turning rclone's stderr into something a drone pilot can act on.

A real field failure read only "DJI offload FAILED (exit code 1)" in the popup;
the one line that mattered — an expired Google token — was buried in a log.
"""

from __future__ import annotations

import pytest

from dji_auto_upload.upload import _explain

EXPIRED = (
    '2026/08/03 12:24:15 NOTICE: test: This remote uses rclone\'s shared Google Drive '
    "client_id, which is being retired\n"
    '2026/08/03 12:24:15 CRITICAL: Failed to create file system for "test:album/DJI": '
    'couldn\'t fetch token: invalid_grant: maybe token expired? - try refreshing with '
    '"rclone config reconnect test:"'
)


def test_expired_token_becomes_actionable_advice() -> None:
    out = _explain(EXPIRED)
    assert "expired" in out
    assert "rclone config reconnect" in out


def test_a_notice_alone_is_never_reported_as_the_failure() -> None:
    notice_only = "2026/08/03 NOTICE: this remote uses rclone's shared client_id"
    assert _explain(notice_only) == ""


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("2026/01/01 ERROR : quota exceeded for this user", "quota"),
        ("2026/01/01 CRITICAL: dial tcp: lookup drive.google.com: no such host", "network"),
    ],
)
def test_common_causes_are_translated(stderr: str, expected: str) -> None:
    assert expected in _explain(stderr)


def test_unknown_error_falls_back_to_rclones_own_line() -> None:
    out = _explain("2026/01/01 CRITICAL: something unusual went wrong")
    assert "something unusual went wrong" in out


def test_empty_stderr_yields_no_reason() -> None:
    assert _explain("") == ""
