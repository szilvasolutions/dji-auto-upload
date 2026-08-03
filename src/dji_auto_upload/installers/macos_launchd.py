"""macOS auto-trigger: a LaunchAgent that runs a resident watcher.

There is no native "udev for macOS", so the watcher polls /Volumes for a
newly-mounted DJI volume and runs `dji-auto-upload run --device <mountpoint>`
for each. (An earlier version used DiskArbitration via pyobjc; it was dropped in
favour of polling — the same approach Windows uses — because it needed a heavy
native dependency, could not be exercised in CI, and the callback path was
never actually reached in the field.)

The LaunchAgent is invoked as `<python> -m dji_auto_upload _watch`, NOT via the
`dji-auto-upload` console script: a LaunchAgent inherits launchd's stripped PATH
(`/usr/bin:/bin:/usr/sbin:/sbin`), and the console script lives in a Homebrew or
`pip --user` bin dir that is not on it. Invoking the interpreter by absolute path
needs no PATH at all.

Runs at user login (RunAtLoad) and is kept alive (KeepAlive). Bootstrapped via
`launchctl bootstrap gui/$UID …`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console

from ..config import Config
from ..detect import VolumeInfo, is_dji_volume

console = Console()
log = logging.getLogger(__name__)

LABEL = "com.dji-auto-upload.watcher"
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _watch_argv() -> list[str]:
    """argv for the watcher: prefer the console script, else the interpreter."""
    exe = shutil.which("dji-auto-upload")
    if exe:
        return [exe, "_watch"]
    return [sys.executable, "-m", "dji_auto_upload", "_watch"]


def _render_plist(argv: list[str], log_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # The plist is XML; a path or label containing & or < would corrupt it.
        autoescape=select_autoescape(enabled_extensions=("plist", "plist.j2"), default=False),
        keep_trailing_newline=True,
    )
    return env.get_template("com.dji-auto-upload.watcher.plist.j2").render(
        label=LABEL,
        argv=argv,
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

    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(_render_plist(_watch_argv(), cfg.paths.log_dir), encoding="utf-8")
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
        return

    # bootstrap succeeds even if the job then dies on launch, so confirm it is
    # actually alive rather than printing a success we can't stand behind.
    time.sleep(2)
    check = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True
    )
    if check.returncode == 0 and ("state = running" in check.stdout or "pid = " in check.stdout):
        console.print("[bold green]Watcher loaded and running.[/bold green] Plug in your drone to test.")
    else:
        err_log = cfg.paths.log_dir / "watcher.err.log"
        tail = ""
        try:
            tail = "\n".join(err_log.read_text().splitlines()[-10:])
        except OSError:
            pass
        console.print(
            "[yellow]Watcher was loaded but is not running.[/yellow] "
            f"Check [cyan]{err_log}[/cyan]" + (f":\n{tail}" if tail else ".")
        )


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

POLL_SECONDS = 3


def watch_loop(cfg: Config) -> None:
    """Poll /Volumes and trigger an offload for each DJI volume that appears.

    Runs forever; the LaunchAgent restarts us if we ever return. Anything already
    mounted at start counts as an arrival (plug-in-then-install is normal), and
    the ledger dedupes so a re-run is harmless. Every iteration is guarded so one
    bad poll can't kill the watcher.
    """
    log.info("macOS watcher started (poll %ss)", POLL_SECONDS)
    seen: set[Path] = set()
    while True:
        try:
            current = {p for p in Path("/Volumes").iterdir() if p.is_dir()}
        except OSError as exc:
            log.warning("could not list /Volumes: %s", exc)
            current = set(seen)
        for mp in current - seen:
            try:
                vol = VolumeInfo(mountpoint=mp, label=mp.name)
                if is_dji_volume(vol, cfg.detect):
                    log.info("DJI volume detected at %s — triggering offload", mp)
                    _spawn_offload(mp, cfg)
                else:
                    log.debug("volume %s is not a DJI volume — ignoring", mp)
            except Exception as exc:  # never let one volume kill the loop
                log.warning("error handling volume %s: %s", mp, exc)
        seen = current
        time.sleep(POLL_SECONDS)


def _spawn_offload(mountpoint: Path, cfg: Config) -> None:
    """Launch an offload detached from the watcher, PATH-independently.

    Uses the running interpreter (`sys.executable -m dji_auto_upload`) so it works
    under launchd's stripped PATH, in its own session so it outlives the watcher,
    with output to a log so a launch failure is not silently swallowed.
    """
    exe = shutil.which("dji-auto-upload")
    cmd = [exe, "run", "--device", str(mountpoint)] if exe else [
        sys.executable, "-m", "dji_auto_upload", "run", "--device", str(mountpoint)
    ]
    try:
        logf = open(cfg.paths.log_dir / "watcher.err.log", "a")  # noqa: SIM115
        subprocess.Popen(cmd, stdout=logf, stderr=logf, start_new_session=True)
    except OSError as exc:
        log.error("could not launch offload for %s: %s", mountpoint, exc)
