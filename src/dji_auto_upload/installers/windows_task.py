"""Windows auto-trigger: a Scheduled Task that runs a polling volume watcher.

Four files are generated into %LOCALAPPDATA%\\dji-auto-upload:

- dji-watcher-launch.vbs — what the task actually runs. It spawns the watcher
  with no window at all. `powershell.exe -WindowStyle Hidden` allocates a
  console *before* parsing that flag, so a window appeared and a user who
  closed it killed the watcher.
- dji-watcher.ps1 — polls for a DJI volume; holds a single-instance mutex so the
  task's 5-minute repetition revives a dead watcher without stacking copies.
- dji-run.ps1 — the WORKER: runs the offload in its own hidden console, so
  closing the progress window cannot interrupt a transfer. Shows the result
  popup and logs its start/exit.
- dji-view.ps1 — the VIEWER: a visible, read-only progress window. Safe to close.

`schtasks /create /xml` avoids needing the PowerShell scheduled-jobs module. The
task runs at logon of the current user; no admin needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
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


def _render(name: str, **ctx: Any) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # The task XML interpolates a username and a path. An `&` in either (or
        # `<`, `>`, quotes) makes the document malformed and schtasks fails with
        # an opaque error, so escape XML output. The .ps1 is not XML and must not
        # be escaped — it is quoted separately, see _ps_single_quote.
        # Note the ".xml.j2" entry: select_autoescape matches the *final*
        # extension, so "xml" alone would never match "…Task.xml.j2".
        autoescape=select_autoescape(enabled_extensions=("xml", "xml.j2"), default=False),
        keep_trailing_newline=True,
    )
    return env.get_template(name).render(**ctx)


def _ps_single_quote(value: str) -> str:
    """Escape a value for a PowerShell single-quoted string ('' is a literal ')."""
    return value.replace("'", "''")


def install(cfg: Config, *, force: bool = False) -> None:
    if sys.platform != "win32":
        console.print("[red]windows_task installer called on non-Windows[/red]")
        return

    watcher = _watcher_path()
    watcher.parent.mkdir(parents=True, exist_ok=True)

    # Prefer the console-script shim, but pip --user installs often leave the
    # Scripts dir off PATH — in that case launch via the interpreter itself
    # (`python -m dji_auto_upload`), which needs no PATH at all.
    exe = shutil.which("dji-auto-upload")
    if exe:
        binary, pre_args = exe, []
    else:
        binary, pre_args = sys.executable, ["-m", "dji_auto_upload"]

    q_pre = [_ps_single_quote(a) for a in pre_args]
    worker = watcher.with_name("dji-run.ps1")
    worker.write_text(
        _render(
            "dji-run.ps1.j2",
            binary=_ps_single_quote(binary),
            pre_args=q_pre,
            log_file=str(cfg.paths.log_file),
            state_file=str(cfg.paths.log_dir / "last-run.json"),
            stage_hint=str(cfg.paths.stage_dir),
        ),
        encoding="utf-8-sig",
    )
    viewer = watcher.with_name("dji-view.ps1")
    viewer.write_text(
        _render("dji-view.ps1.j2", binary=_ps_single_quote(binary), pre_args=q_pre),
        encoding="utf-8-sig",
    )
    watcher.write_text(
        _render(
            "dji-watcher.ps1.j2",
            worker=_ps_single_quote(str(worker)),
            viewer=_ps_single_quote(str(viewer)),
            vendor_ids=list(cfg.detect.vendor_ids),
            labels=list(cfg.detect.volume_labels),
        ),
        encoding="utf-8-sig",
    )
    # The task launches this, not PowerShell — see the template for why.
    launcher = watcher.with_name("dji-watcher-launch.vbs")
    # ASCII, and crucially NO BOM: VBScript cannot parse a UTF-8 BOM (wscript
    # dies with "Invalid character" on line 1, and //B hides the dialog, so it
    # fails in total silence). The .ps1 files above DO need the BOM; this must
    # not have one.
    launcher.write_text(
        _render("dji-watcher-launch.vbs.j2", watcher_path=str(watcher).replace('"', '""')),
        encoding="ascii",
        errors="replace",
    )
    for f in (watcher, worker, viewer, launcher):
        console.print(f"[green]Wrote[/green] {f}")

    task_xml_path = watcher.with_name("DjiAutoUploadTask.xml")

    # A watcher already running holds the previous version of the script in
    # memory, so it would keep behaving like the old build after an update.
    stale = _kill_stale_watchers()
    if stale:
        console.print(f"[dim]Stopped {stale} watcher process(es) from the previous version.[/dim]")

    if force:
        subprocess.run(
            ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
            capture_output=True,
            text=True,
        )

    # Try the self-healing version (a repetition that restarts a dead watcher),
    # and fall back to the plain trigger if Task Scheduler's schema validator
    # rejects it. A task without the repetition is far better than no task.
    proc = None
    for repeat in (True, False):
        task_xml_path.write_text(
            _render(
                "DjiAutoUploadTask.xml.j2",
                powershell="powershell.exe",
                watcher_path=str(watcher),
                launcher_path=str(launcher),
                user=os.environ.get("USERNAME", ""),
                repeat=repeat,
            ),
            encoding="utf-16",
        )
        proc = subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(task_xml_path), "/f"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            if not repeat:
                console.print(
                    "[yellow]Installed without the 5-minute self-restart[/yellow] "
                    "(Task Scheduler rejected it); the watcher will still start at logon."
                )
            break

    assert proc is not None
    if proc.returncode != 0:
        console.print(
            f"[red]schtasks /create failed (rc={proc.returncode}):[/red] {proc.stderr.strip()}"
        )
        console.print(
            "[yellow]Retry from an elevated PowerShell, or import the XML manually:[/yellow]\n"
            f"  [cyan]schtasks /create /tn \"{TASK_NAME}\" /xml \"{task_xml_path}\" /f[/cyan]"
        )
        return
    # The logon trigger only arms the watcher at the NEXT sign-in; start it now
    # so "install, plug in drone" works without a logout in between.
    started = subprocess.run(
        ["schtasks", "/run", "/tn", TASK_NAME],
        capture_output=True,
        text=True,
    )
    if started.returncode == 0:
        # schtasks /run reports success even when the launched process dies
        # immediately, so confirm the watcher really came up rather than
        # printing a reassurance we cannot stand behind.
        import time

        time.sleep(4)
        alive = _watcher_is_running()
        if alive:
            console.print(
                "[bold green]Watcher installed and running.[/bold green] Plug in your drone — "
                "a progress window opens (safe to close), and you'll get a popup when it's done."
            )
        else:
            log_file = watcher.with_name("watcher.log")
            console.print(
                "[yellow]The task started but no watcher process is running.[/yellow]\n"
                f"Check [cyan]{log_file}[/cyan], or run the watcher by hand to see the error:\n"
                f"  [cyan]powershell -ExecutionPolicy Bypass -File \"{watcher}\"[/cyan]"
            )
    else:
        console.print(
            "[bold green]Scheduled Task installed[/bold green], but it could not be started "
            f"right away ({started.stderr.strip()}).\n"
            "Sign out and back in, or run "
            "[cyan]schtasks /run /tn \"DJI Auto Upload Watcher\"[/cyan]."
        )


def _kill_stale_watchers() -> int:
    """Stop any watcher already running, and report how many were stopped.

    An update rewrites dji-watcher.ps1, but a watcher process that is already
    running holds the OLD script in memory and keeps behaving like the old
    version — which made several updates look like they had changed nothing.
    Only watchers are killed; a WORKER may be mid-upload and is left alone.
    """
    ps = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
        "Where-Object { $_.CommandLine -like '*dji-watcher.ps1*' }; "
        "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; "
        "@($p).Count"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
        out = proc.stdout.strip()
        return int(out) if out.isdigit() else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _watcher_is_running() -> bool:
    """True if a powershell process is running our watcher script."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
             "Where-Object { $_.CommandLine -like '*dji-watcher.ps1*' }).Count"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0 and proc.stdout.strip().isdigit() and int(proc.stdout.strip()) > 0
    except (OSError, subprocess.SubprocessError):
        return False


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
    for f in (
        watcher,
        watcher.with_name("dji-run.ps1"),
        watcher.with_name("dji-view.ps1"),
        watcher.with_name("dji-watcher-launch.vbs"),
    ):
        if f.exists():
            f.unlink()
            console.print(f"[green]Removed[/green] {f}")
