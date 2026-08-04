"""Self-update and uninstall.

Both exist because the alternative is telling a non-developer to work out which
of their several Python installations owns the package, which is exactly the
trap a stale `pip.exe` shim sets: the launcher can point at an interpreter that
has since been removed, so plain `pip` dies with "cannot find the file
specified" while the package sits happily under a different Python.

Everything here therefore drives `sys.executable -m pip`. That is the
interpreter currently running this code, so by construction it is the one the
package is installed in.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .config import Config
from .ledger import files_needing_upload
from .stage import existing_stage_dirs

console = Console()

# Now on PyPI, so pip can compare versions properly and skip when there is
# nothing new — no more force-reinstalling a branch archive on every check.
DEFAULT_SOURCE = "dji-auto-upload"


def is_versioned_source(source: str) -> bool:
    """True for a PyPI requirement, False for a URL or local path.

    Decides whether pip can be trusted to compare versions (PyPI) or has to be
    forced (a branch archive, where every build claims the same version).
    """
    s = source.strip().lower()
    if s.startswith(("http://", "https://", "git+", "file:", ".", "/")):
        return False
    return not s.endswith((".zip", ".tar.gz", ".whl"))


def _pip(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", *args],
        capture_output=True,
        text=True,
    )


def update(source: str = DEFAULT_SOURCE, *, reinstall_trigger: bool = True) -> int:
    """Upgrade in place, then regenerate the trigger with the new templates."""
    console.print(f"[cyan]Current version:[/cyan] {__version__}")
    console.print(f"[cyan]Updating from:[/cyan] {source}")
    console.print(f"[dim]Using interpreter: {sys.executable}[/dim]\n")

    if is_versioned_source(source):
        # A PyPI release has a real version number, so pip can compare honestly
        # and will skip the download when there is genuinely nothing newer.
        proc = _pip("install", "--upgrade", source)
        if proc.returncode != 0:
            console.print("[red]Update failed.[/red]")
            console.print((proc.stderr or proc.stdout).strip()[-1500:])
            return 1
    else:
        # Branch builds all carry the same version string, so a plain --upgrade
        # compares 0.2.0 against 0.2.0, decides the requirement is satisfied,
        # and installs nothing — while printing a wall of "Requirement already
        # satisfied" that reads like success. Force the code in; the pass before
        # it is what picks up any newly added dependency.
        deps = _pip("install", "--upgrade", source)
        if deps.returncode != 0:
            console.print("[red]Update failed while resolving dependencies.[/red]")
            console.print((deps.stderr or deps.stdout).strip()[-1500:])
            return 1

        proc = _pip("install", "--force-reinstall", "--no-deps", "--no-cache-dir", source)
        if proc.returncode != 0:
            console.print("[red]Update failed.[/red]")
            console.print((proc.stderr or proc.stdout).strip()[-1500:])
            return 1

    # This process still holds the OLD code in memory, so ask a fresh one what
    # it is; that is the only honest way to report the installed version.
    new_version = _installed_version()
    console.print(f"[green]Reinstalled.[/green] Version now: {new_version or __version__}")
    if new_version == __version__:
        console.print(
            "[dim]Same version string — branch builds share one, so this is expected; "
            "the code was replaced regardless.[/dim]"
        )

    if reinstall_trigger:
        # Must run in a NEW process so the regenerated trigger comes from the
        # new templates rather than the ones this process imported.
        console.print("\n[cyan]Refreshing the auto-trigger…[/cyan]")
        tr = subprocess.run(
            [sys.executable, "-m", "dji_auto_upload", "install-trigger", "--force"],
            text=True,
        )
        if tr.returncode != 0:
            console.print(
                "[yellow]Could not refresh the trigger automatically.[/yellow] "
                "Run [cyan]dji-auto-upload install-trigger --force[/cyan] yourself."
            )
            return 1
    return 0


def _installed_version() -> str | None:
    probe = subprocess.run(
        [sys.executable, "-c", "import dji_auto_upload as d; print(d.__version__)"],
        capture_output=True,
        text=True,
    )
    return probe.stdout.strip() if probe.returncode == 0 else None


# ---- uninstall ---------------------------------------------------------------


def stop_watchers() -> int:
    """Stop any running watcher process on this machine. Returns how many.

    An uninstall that leaves the watcher running means the next drone plug-in
    still fires a tool the user thinks they removed. Workers are left alone —
    one may be mid-transfer, and killing it is not our call.
    """
    stopped = 0
    if sys.platform == "win32":
        ps = (
            "$p = Get-CimInstance Win32_Process -Filter "
            "\"Name='powershell.exe' OR Name='wscript.exe'\" | "
            "Where-Object { $_.CommandLine -match 'dji-watcher|dji-watcher-launch' }; "
            "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; "
            "@($p).Count"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=30,
            )
            out = proc.stdout.strip()
            stopped = int(out) if out.isdigit() else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            stopped = 0
        return stopped

    # macOS/Linux: the resident watcher is `... _watch`.
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "dji_auto_upload.*_watch"], capture_output=True, text=True, timeout=15
        )
        pids = [int(x) for x in proc.stdout.split() if x.isdigit()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0
    for pid in pids:
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            pass
    return stopped


def unuploaded_files(cfg: Config) -> list[str]:
    """Staged files with no ledger entry — footage that is NOT in the cloud yet."""
    pending: list[str] = []
    for d in existing_stage_dirs(cfg.paths.stage_dir):
        pending.extend(f"{d.name}/{n}" for n in files_needing_upload(d))
    return pending


def rclone_forget(remote: str) -> None:
    """Revoke the remote's OAuth token and delete it from rclone.conf."""
    if not shutil.which("rclone"):
        console.print("[yellow]rclone not on PATH — skipping remote removal.[/yellow]")
        return
    # `disconnect` revokes the token at the provider; it is a no-op (and often an
    # error) for backends without OAuth, so its failure is not worth reporting.
    subprocess.run(
        ["rclone", "config", "disconnect", f"{remote}:"],
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        ["rclone", "config", "delete", remote],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        console.print(f"[green]Removed rclone remote[/green] {remote!r} (access revoked).")
    else:
        console.print(
            f"[yellow]Could not remove rclone remote {remote!r}:[/yellow] "
            f"{(proc.stderr or proc.stdout).strip()[:200]}"
        )


def remove_paths(paths: list[Path]) -> None:
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                continue
            console.print(f"[green]Removed[/green] {p}")
        except OSError as exc:
            console.print(f"[yellow]Could not remove {p}:[/yellow] {exc}")


def pip_uninstall_command() -> str:
    """The command that removes the package itself.

    Deliberately not executed: pip cannot cleanly delete the very package whose
    code is mid-execution, and the console shim would be yanked out from under
    the running process on Windows.
    """
    return f'"{sys.executable}" -m pip uninstall -y dji-auto-upload'
