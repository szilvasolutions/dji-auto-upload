"""Per-OS auto-trigger dispatch.

Each platform module exposes `install(cfg, force)` and `uninstall(cfg)`. This
file picks the right one based on sys.platform.
"""

from __future__ import annotations

import re
import sys

from rich.console import Console

from ..config import Config
from ..errors import ConfigError

console = Console()

# Config values are rendered into privileged trigger files: the udev rule runs
# as root and the PowerShell watcher runs at every logon. Refuse anything that
# isn't unambiguously safe so a tampered config can't smuggle commands in.
_VENDOR_ID_RE = re.compile(r"^[0-9a-fA-F]{4}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,32}$")


def validate_trigger_inputs(cfg: Config) -> None:
    """Raise ConfigError if any detect value is unsafe to embed in a trigger."""
    for vid in cfg.detect.vendor_ids:
        if not _VENDOR_ID_RE.match(vid):
            raise ConfigError(
                f"refusing to install trigger: vendor_id {vid!r} is not 4 hex digits"
            )
    for label in cfg.detect.volume_labels:
        if not _LABEL_RE.match(label):
            raise ConfigError(
                f"refusing to install trigger: volume label {label!r} contains "
                "characters outside [A-Za-z0-9 _.-] or is longer than 32 chars"
            )


def install(cfg: Config, *, force: bool = False) -> None:
    validate_trigger_inputs(cfg)
    if sys.platform.startswith("linux"):
        from . import linux_udev as impl
    elif sys.platform == "darwin":
        from . import macos_launchd as impl
    elif sys.platform == "win32":
        from . import windows_task as impl
    else:
        console.print(f"[red]Unsupported platform: {sys.platform}[/red]")
        return
    impl.install(cfg, force=force)


def uninstall(cfg: Config) -> None:
    if sys.platform.startswith("linux"):
        from . import linux_udev as impl
    elif sys.platform == "darwin":
        from . import macos_launchd as impl
    elif sys.platform == "win32":
        from . import windows_task as impl
    else:
        console.print(f"[red]Unsupported platform: {sys.platform}[/red]")
        return
    impl.uninstall(cfg)
