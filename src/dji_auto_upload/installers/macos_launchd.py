"""macOS auto-trigger: a LaunchAgent that runs a resident DiskArbitration watcher.

There is no native "udev for macOS"; the supported API is DiskArbitration
(`DARegisterDiskAppearedCallback`). We register a per-user LaunchAgent that
runs `dji-auto-upload _watch` (a hidden CLI subcommand). The watcher subscribes to
disk-appeared events, filters for DJI volumes, and shells out to
`dji-auto-upload run --device <mountpoint>` for each.

LaunchAgent runs at user login (RunAtLoad=true) and is kept alive
(KeepAlive=true). Bootstrapped via `launchctl bootstrap gui/$UID …`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from ..config import Config
from ..detect import VolumeInfo, is_dji_volume

console = Console()
log = logging.getLogger(__name__)

LABEL = "com.dji-auto-upload.watcher"
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _render_plist(binary: str, log_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )
    return env.get_template("com.dji-auto-upload.watcher.plist.j2").render(
        label=LABEL,
        binary=binary,
        log_dir=str(log_dir),
    )


def install(cfg: Config, *, force: bool = False) -> None:
    if sys.platform != "darwin":
        console.print("[red]macos_launchd installer called on non-macOS[/red]")
        return

    plist = _plist_path()
    if plist.exists() and not force:
        console.print(f"[yellow]{plist} already exists.[/yellow] Use [cyan]--force[/cyan] to overwrite.")
        return

    binary = shutil.which("dji-auto-upload")
    if binary is None:
        console.print(
            "[red]Could not find `dji-auto-upload` on PATH.[/red] "
            "Make sure your pip install location is on PATH (e.g. /usr/local/bin or ~/.local/bin)."
        )
        return

    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(_render_plist(binary, cfg.paths.log_dir), encoding="utf-8")
    plist.chmod(0o644)
    console.print(f"[green]Wrote[/green] {plist}")

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    proc = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        console.print(f"[yellow]launchctl bootstrap returned {proc.returncode}: {proc.stderr.strip()}[/yellow]")
    else:
        console.print("[bold green]Watcher loaded.[/bold green] Plug in your drone to test.")


def uninstall(cfg: Config) -> None:
    if sys.platform != "darwin":
        console.print("[red]macos_launchd installer called on non-macOS[/red]")
        return
    plist = _plist_path()
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    if plist.exists():
        plist.unlink()
        console.print(f"[green]Removed[/green] {plist}")
    else:
        console.print(f"[dim]{plist} not present — nothing to do.[/dim]")


# ---- Watch loop (the resident `dji-auto-upload _watch` daemon) ----------------


def watch_loop(cfg: Config) -> None:
    """Subscribe to DiskArbitration appearance events and trigger offload runs.

    Uses pyobjc-framework-DiskArbitration. The session runs forever; LaunchAgent
    will restart us if we ever return.
    """
    try:
        from CoreFoundation import (
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            kCFRunLoopDefaultMode,
        )
        from DiskArbitration import (
            DADiskCopyDescription,
            DARegisterDiskAppearedCallback,
            DASessionCreate,
            DASessionScheduleWithRunLoop,
        )
    except ImportError:  # pragma: no cover — only runs on macOS
        log.error("pyobjc-framework-DiskArbitration not installed — falling back to polling")
        _poll_loop(cfg)
        return

    session = DASessionCreate(None)
    DASessionScheduleWithRunLoop(session, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)

    def on_disk(disk: Any, _ctx: Any) -> None:  # pragma: no cover — callback wired into CFRunLoop
        desc = DADiskCopyDescription(disk)
        if desc is None:
            return
        mount_url = desc.get("DAVolumePath")
        if mount_url is None:
            return
        mp = Path(mount_url.path()) if hasattr(mount_url, "path") else Path(str(mount_url))
        label = desc.get("DAVolumeName")
        vol = VolumeInfo(mountpoint=mp, label=str(label) if label else None)
        if is_dji_volume(vol, cfg.detect):
            log.info("DJI volume detected at %s — triggering offload", mp)
            _spawn_offload(mp)

    DARegisterDiskAppearedCallback(session, None, on_disk, None)
    log.info("DiskArbitration watcher running")
    CFRunLoopRun()


def _poll_loop(cfg: Config) -> None:  # pragma: no cover - polling fallback
    """Fallback: poll /Volumes every 3 s. Used only if pyobjc isn't available."""
    seen: set[Path] = set()
    while True:
        try:
            current = {p for p in Path("/Volumes").iterdir() if p.is_dir()}
        except OSError:
            current = set()
        new = current - seen
        for mp in new:
            vol = VolumeInfo(mountpoint=mp, label=mp.name)
            if is_dji_volume(vol, cfg.detect):
                log.info("DJI volume detected at %s — triggering offload", mp)
                _spawn_offload(mp)
        seen = current
        time.sleep(3)


def _spawn_offload(mountpoint: Path) -> None:
    binary = shutil.which("dji-auto-upload") or "dji-auto-upload"
    subprocess.Popen(
        [binary, "run", "--device", str(mountpoint)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
