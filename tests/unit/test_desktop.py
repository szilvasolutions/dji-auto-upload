"""Desktop notification / progress window plumbing.

The bug this pins down: a udev-triggered run executes as root with no
connection to the logged-in user's desktop, so plainly running `notify-send`
there notifies nobody. Linux notifications from the trigger never worked until
the session lookup below existed.
"""

from __future__ import annotations

import pytest

from dji_auto_upload import desktop


def test_never_raises_and_returns_a_bool() -> None:
    assert desktop.notify("t", "m") in (True, False)
    assert desktop.open_progress_window(["true"]) in (True, False)


def test_root_posts_notification_as_the_desktop_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(desktop.os, "geteuid", lambda: 0)
    monkeypatch.setattr(desktop, "_desktop_session", lambda: ("adam", 1000))

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        calls.append(argv)
        return R()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.notify("DJI", "50%") is True

    argv = calls[0]
    # Ran as the user, with their session bus — otherwise it goes nowhere.
    assert argv[0] in ("runuser", "sudo")
    assert "adam" in argv
    joined = " ".join(argv)
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in joined
    assert "notify-send" in joined


def test_root_without_a_desktop_session_stays_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(desktop.os, "geteuid", lambda: 0)
    monkeypatch.setattr(desktop, "_desktop_session", lambda: None)
    assert desktop.notify("DJI", "50%") is False


def test_notifications_replace_in_place_rather_than_stacking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-GB transfer updates every few seconds; without this the user gets
    a wall of banners."""
    seen: list[list[str]] = []

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(desktop.os, "geteuid", lambda: 1000)

    class R:
        returncode = 0

    monkeypatch.setattr(
        desktop.subprocess, "run", lambda argv, **kw: (seen.append(argv), R())[1]
    )
    desktop.notify("DJI", "50%")
    assert "x-canonical-private-synchronous:dji-auto-upload" in " ".join(seen[0])


def test_macos_uses_osascript_for_both_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class R:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(
        desktop.subprocess, "run", lambda argv, **kw: (seen.append(argv), R())[1]
    )
    assert desktop.notify("DJI", "done") is True
    assert desktop.open_progress_window(["dji-auto-upload", "watch-run"]) is True

    assert seen[0][0] == "osascript" and "display notification" in seen[0][2]
    assert seen[1][0] == "osascript" and "Terminal" in seen[1][2]
    assert "watch-run" in seen[1][2]
