"""Per-OS auto-trigger dispatch.

Each platform module exposes `install(cfg, force)` and `uninstall(cfg)`. This
file picks the right one based on sys.platform.
"""

from __future__ import annotations

import sys

from rich.console import Console

from ..config import Config

console = Console()


def install(cfg: Config, *, force: bool = False) -> None:
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
