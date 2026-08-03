"""Native desktop notifications — the cross-platform 'it finished' / 'it broke'
signal.

Before this, only Windows showed anything (a popup) and Telegram was opt-in and
off by default, so on macOS and Linux a run was completely silent. This posts a
system notification at the end of a run. Strictly best-effort: it never raises,
and it degrades to a no-op when no mechanism is available (e.g. a headless box,
or Linux without a notification daemon).

Windows is intentionally not handled here — its offload runs behind a visible
window that shows its own completion popup (see the dji-run.ps1 template).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

TITLE = "DJI Auto Upload"


def notify(title: str, message: str) -> bool:
    """Post a desktop notification. Returns True if a mechanism accepted it."""
    try:
        if sys.platform == "darwin":
            return _macos(title, message)
        if sys.platform.startswith("linux"):
            return _linux(title, message)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("desktop notify failed: %s", exc)
    return False


def _macos(title: str, message: str) -> bool:
    # osascript is always present on macOS. Quotes are escaped for AppleScript.
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def _linux(title: str, message: str) -> bool:
    if not shutil.which("notify-send"):
        log.debug("notify-send not installed — no desktop notification")
        return False
    r = subprocess.run(
        ["notify-send", "--app-name", TITLE, title, message],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0
