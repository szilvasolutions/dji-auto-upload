"""Native desktop notifications and the per-OS progress window.

Windows gets its progress from a viewer console the watcher launches. This
module is the equivalent for macOS and Linux, where a triggered run is
otherwise completely invisible.

The Linux case has a wrinkle worth stating plainly: the udev trigger runs the
offload as **root**, with no connection to the logged-in user's desktop. Simply
executing `notify-send` there sends a notification to nobody — which is what an
earlier version of this file did. When running as root we locate the user's
session bus and post the notification as that user.

Everything here is best-effort: it never raises, and it degrades to silence on a
headless machine.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

TITLE = "DJI Auto Upload"


# ---- session discovery (Linux, when we are root) ------------------------------


def _desktop_session() -> tuple[str, int] | None:
    """(username, uid) of a logged-in user with a session bus, or None.

    Each logged-in user gets /run/user/<uid> with a DBus socket, so this needs
    no loginctl output parsing (whose columns differ between versions).
    """
    if sys.platform == "win32":
        return None
    try:
        import pwd
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    try:
        candidates = sorted(Path("/run/user").iterdir())
    except OSError:
        return None
    for d in candidates:
        if not d.name.isdigit():
            continue
        uid = int(d.name)
        if uid < 1000:  # skip system users
            continue
        if not (d / "bus").exists():
            continue
        try:
            return pwd.getpwuid(uid).pw_name, uid
        except KeyError:
            continue
    return None


def _run_as_user(user: str, uid: int, argv: list[str], *, timeout: int = 10) -> bool:
    """Run argv as `user`, wired to their session bus and display."""
    env_bits = [
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DISPLAY={os.environ.get('DISPLAY', ':0')}",
    ]
    for runner in (["runuser", "-u", user, "--"], ["sudo", "-u", user]):
        if not shutil.which(runner[0]):
            continue
        try:
            proc = subprocess.run(
                [*runner, "env", *env_bits, *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# ---- notifications -------------------------------------------------------------


def notify(title: str, message: str) -> bool:
    """Post a desktop notification. True if something accepted it."""
    try:
        if sys.platform == "darwin":
            return _macos_notify(title, message)
        if sys.platform.startswith("linux"):
            return _linux_notify(title, message)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("desktop notify failed: %s", exc)
    return False


def _macos_notify(title: str, message: str) -> bool:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def _linux_notify(title: str, message: str) -> bool:
    if not shutil.which("notify-send"):
        log.debug("notify-send not installed — no desktop notification")
        return False

    argv = [
        "notify-send", "--app-name", TITLE,
        # Replace the previous one in place rather than stacking a new banner
        # every few seconds during a long transfer.
        "-h", "string:x-canonical-private-synchronous:dji-auto-upload",
        title, message,
    ]

    # Running as root (the udev trigger) means there is no session bus of our
    # own; post it as the logged-in user instead, or it goes nowhere.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        session = _desktop_session()
        if session is None:
            log.debug("no logged-in desktop session — skipping notification")
            return False
        return _run_as_user(session[0], session[1], argv)

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---- progress window -----------------------------------------------------------


def open_progress_window(argv: list[str]) -> bool:
    """Open a terminal window running `argv` (the read-only progress viewer).

    macOS: a LaunchAgent runs inside the user's GUI session, so Terminal opens
    normally. Linux: the trigger is root and outside any session, so this is
    genuinely best-effort — the notification path above is the reliable channel
    there.
    """
    try:
        if sys.platform == "darwin":
            return _macos_window(argv)
        if sys.platform.startswith("linux"):
            return _linux_window(argv)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("could not open progress window: %s", exc)
    return False


def _macos_window(argv: list[str]) -> bool:
    cmd = " ".join(_sh_quote(a) for a in argv)
    script = f'tell application "Terminal" to do script "{cmd}"'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        log.debug("osascript Terminal failed: %s", r.stderr.strip())
    return r.returncode == 0


_TERMINALS: tuple[tuple[str, list[str]], ...] = (
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("xterm", ["-e"]),
)


def _linux_window(argv: list[str]) -> bool:
    for exe, flag in _TERMINALS:
        if not shutil.which(exe):
            continue
        launch = [exe, *flag, *argv]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            session = _desktop_session()
            if session is None:
                return False
            return _run_as_user(session[0], session[1], launch, timeout=15)
        try:
            subprocess.Popen(launch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
