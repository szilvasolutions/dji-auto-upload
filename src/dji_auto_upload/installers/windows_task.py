"""Windows auto-trigger: Scheduled Task that runs a PowerShell WMI watcher.

The watcher subscribes to Win32_VolumeChangeEvent (EventType=2 = arrival),
filters arrivals by checking for a `DCIM\\` directory or DJI vendor ID, and
shells out to `dji-auto-upload run --device <drive>`.

We use `schtasks /create /xml` so we don't need PowerShell scheduled-jobs
modules. The task runs *at logon* of the current user, so the watcher is
running whenever the user is logged in. No admin needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from ..config import Config

console = Console()

TASK_NAME = "DJI Auto Upload Watcher"
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _watcher_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base is None:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "dji-auto-upload" / "dji-watcher.ps1"


def _render(name: str, **ctx) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), keep_trailing_newline=True)
    return env.get_template(name).render(**ctx)


def install(cfg: Config, *, force: bool = False) -> None:
    if sys.platform != "win32":
        console.print("[red]windows_task installer called on non-Windows[/red]")
        return

    watcher = _watcher_path()
    watcher.parent.mkdir(parents=True, exist_ok=True)

    binary = shutil.which("dji-auto-upload") or "dji-auto-upload"
    watcher.write_text(
        _render(
            "dji-watcher.ps1.j2",
            binary=binary,
            vendor_ids=list(cfg.detect.vendor_ids),
            labels=list(cfg.detect.volume_labels),
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Wrote[/green] {watcher}")

    task_xml_path = watcher.with_name("DjiAutoUploadTask.xml")
    task_xml_path.write_text(
        _render(
            "DjiAutoUploadTask.xml.j2",
            powershell="powershell.exe",
            watcher_path=str(watcher),
            user=os.environ.get("USERNAME", ""),
        ),
        encoding="utf-16",
    )

    if force:
        subprocess.run(
            ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
            capture_output=True,
            text=True,
        )

    proc = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(task_xml_path), "/f"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        console.print(
            f"[red]schtasks /create failed (rc={proc.returncode}):[/red] {proc.stderr.strip()}"
        )
        console.print(
            "[yellow]Retry from an elevated PowerShell, or import the XML manually:[/yellow]\n"
            f"  [cyan]schtasks /create /tn \"{TASK_NAME}\" /xml \"{task_xml_path}\" /f[/cyan]"
        )
        return
    console.print(
        "[bold green]Scheduled Task installed.[/bold green] Sign out and back in to start the watcher, "
        "or run [cyan]schtasks /run /tn \"DJI Auto Upload Watcher\"[/cyan]."
    )


def uninstall(cfg: Config) -> None:
    proc = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        console.print(f"[green]Removed[/green] Scheduled Task '{TASK_NAME}'.")
    else:
        console.print(f"[dim]No '{TASK_NAME}' task found ({proc.stderr.strip()}).[/dim]")

    watcher = _watcher_path()
    if watcher.exists():
        watcher.unlink()
        console.print(f"[green]Removed[/green] {watcher}")
