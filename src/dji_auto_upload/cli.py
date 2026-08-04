"""Typer CLI surface for dji-auto-upload.

All user-facing commands live here. The orchestrator is intentionally only
called from `run`; everything else (setup, install-trigger, status, …) is a
thin wrapper around helper modules so each command is independently testable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import Config, load
from .errors import AlreadyRunning, OffloadError
from .lock import single_flight
from .logging_setup import configure as configure_logging
from .notifier import Notifier, make_notifier
from .paths import ensure_dirs, get_paths

app = typer.Typer(
    add_completion=False,
    help="Plug in your DJI drone. Walk away. Footage shows up in the cloud.",
    no_args_is_help=True,
)

console = Console()
log = logging.getLogger("dji_auto_upload.cli")

# A run skipped because another is in flight exits with this code — not 0 — so
# the caller (the Windows viewer window) can tell "did nothing" from "did it all".
EXIT_ALREADY_RUNNING = 75


# ---- Global options -------------------------------------------------------

@app.callback()
def _root(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", help="Override the config dir."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    paths = get_paths()
    if config_path is not None:
        # Override config dir; data + log + runtime stay at defaults.
        from .paths import AppPaths
        paths = AppPaths(
            config_dir=config_path,
            data_dir=paths.data_dir,
            log_dir=paths.log_dir,
            runtime_dir=paths.runtime_dir,
        )
    ensure_dirs(paths)
    cfg = load(paths)
    # load() may have applied a user-chosen [paths] stage_dir that ensure_dirs
    # above didn't know about yet, so create the final set of dirs now — else
    # the disk precheck stats a folder that doesn't exist and crashes.
    ensure_dirs(cfg.paths)
    configure_logging(cfg.paths.log_file, cfg.logging, console_verbose=verbose, quiet=quiet)
    ctx.obj = cfg


# ---- Commands -------------------------------------------------------------

@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"dji-auto-upload {__version__}")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show config summary, rclone reachability, Telegram reachability, last log line."""
    from .upload import list_remotes, remote_reachable

    cfg: Config = ctx.obj

    table = Table(title="dji-auto-upload status", show_header=False, box=None)
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("version", __version__)
    table.add_row("config", str(cfg.paths.config_file))
    table.add_row("credentials", str(cfg.paths.credentials_file))
    table.add_row("stage dir", str(cfg.paths.stage_dir))
    table.add_row("log file", str(cfg.paths.log_file))
    table.add_row("retention (stage / drone)", f"{cfg.retention.stage_days}d / {cfg.retention.drone_days}d")
    if not cfg.remote.enabled:
        table.add_row("destination", "[cyan]this computer only[/cyan] (no cloud upload)")
        table.add_row("folder", str(cfg.paths.stage_dir))
    else:
        table.add_row("rclone remote", cfg.remote.name)
        table.add_row("path template", cfg.remote.path_template)
    remotes = list_remotes() if cfg.remote.enabled else []
    if not cfg.remote.enabled:
        pass
    elif cfg.remote.name in remotes:
        # Generous timeout: a cold cloud remote needs an OAuth refresh plus a
        # root listing, and "no" here should mean something, not just "slow".
        ok = remote_reachable(cfg.remote.name, timeout=30)
        table.add_row(
            "rclone reachable",
            "[green]yes[/green]" if ok else "[yellow]no answer (slow or offline)[/yellow]",
        )
    else:
        table.add_row("rclone configured", "[red]no[/red] — run `rclone config` then `dji-auto-upload setup`")
    if cfg.notifier.enabled and cfg.telegram.configured:
        table.add_row("telegram", "[green]configured[/green]")
    else:
        table.add_row("telegram", "[dim]disabled[/dim]")
    from .runstate import read_state

    st = read_state(cfg.paths)
    if st is not None:
        colour = {"done": "green", "failed": "red", "running": "cyan", "skipped": "yellow"}
        label = f"[{colour.get(st.status, 'white')}]{st.status}[/] "
        label += f"({st.stage}, {st.percent:.0f}%)" if st.status == "running" else f"— {st.updated}"
        if st.status == "failed" and st.error:
            label += f"\n  [red]{st.error}[/red]"
        if st.albums:
            label += f"\n  uploaded: {', '.join(st.albums)}"
        table.add_row("last run", label)
    else:
        table.add_row("last run", "[dim](no runs recorded yet)[/dim]")
    console.print(table)


@app.command()
def test_notify(ctx: typer.Context) -> None:
    """Send a test message via the configured notifier."""
    cfg: Config = ctx.obj
    n = make_notifier(cfg)
    n.send("info", "✅ dji-auto-upload test message — notifications are working.")
    console.print("[green]Sent.[/green] Check your Telegram (or wherever notifications go).")


@app.command()
def setup(ctx: typer.Context) -> None:
    """Interactive setup: rclone remote, retention, optional Telegram, install trigger."""
    from .wizard import run_wizard

    run_wizard(ctx.obj)


@app.command()
def install_trigger(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Overwrite an existing trigger."),
) -> None:
    """Install the per-OS auto-trigger (udev / launchd / Scheduled Task)."""
    from .errors import ConfigError
    from .installers import install

    try:
        install(ctx.obj, force=force)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None


@app.command()
def uninstall_trigger(ctx: typer.Context) -> None:
    """Remove the per-OS auto-trigger."""
    from .installers import uninstall

    uninstall(ctx.obj)


@app.command()
def update(
    source: str = typer.Option(
        None, "--source", help="Where to install from. Defaults to the latest main."
    ),
    skip_trigger: bool = typer.Option(
        False, "--skip-trigger", help="Don't refresh the auto-trigger afterwards."
    ),
) -> None:
    """Update to the latest version and refresh the auto-trigger."""
    from .selfmanage import DEFAULT_SOURCE
    from .selfmanage import update as do_update

    code = do_update(source or DEFAULT_SOURCE, reinstall_trigger=not skip_trigger)
    if code != 0:
        raise typer.Exit(code=code)


@app.command()
def uninstall(
    ctx: typer.Context,
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Don't ask. Never deletes unuploaded files or the rclone remote."
    ),
    keep_config: bool = typer.Option(False, "--keep-config", help="Leave config in place."),
    forget_remote: bool = typer.Option(
        False,
        "--forget-remote",
        help="Also delete the rclone remote and revoke its access token.",
    ),
) -> None:
    """Remove the auto-trigger, and optionally the config and staged files."""
    from rich.prompt import Confirm

    from .installers import uninstall as uninstall_trigger_impl
    from .selfmanage import (
        pip_uninstall_command,
        rclone_forget,
        remove_paths,
        unuploaded_files,
    )

    cfg: Config = ctx.obj

    console.print("[bold]Removing the auto-trigger…[/bold]")
    uninstall_trigger_impl(cfg)

    # Staged footage that never reached the cloud is the one thing here that is
    # irreplaceable, so it gets its own explicit, defaulted-to-no question.
    pending = unuploaded_files(cfg)
    stage = cfg.paths.stage_dir
    if pending:
        console.print(
            f"\n[bold yellow]{len(pending)} staged file(s) are NOT confirmed uploaded[/bold yellow] "
            f"in {stage}:"
        )
        for name in pending[:10]:
            console.print(f"  [yellow]{name}[/yellow]")
        if len(pending) > 10:
            console.print(f"  [dim]…and {len(pending) - 10} more[/dim]")
        console.print("[dim]Copy them somewhere safe before deleting.[/dim]")
        # --yes must not silently destroy footage that is nowhere else. Keeping
        # it is recoverable; deleting it is not, so this one always asks.
        drop_stage = False if yes else Confirm.ask("Delete them anyway?", default=False)
        if yes:
            console.print("[dim]Keeping the stage dir (--yes never deletes unuploaded files).[/dim]")
    elif yes:
        drop_stage = True
    else:
        drop_stage = Confirm.ask(
            f"\nDelete the local staging dir ({stage})? Everything in it is uploaded.",
            default=True,
        )

    to_remove: list[Path] = []
    if drop_stage:
        to_remove.append(stage)

    if not keep_config:
        drop_cfg = yes or Confirm.ask(
            f"Delete config and credentials ({cfg.paths.config_dir})?", default=True
        )
        if drop_cfg:
            to_remove += [cfg.paths.config_file, cfg.paths.credentials_file]

    if to_remove:
        remove_paths(to_remove)

    # Deliberately NOT covered by --yes. The remote belongs to rclone, not to
    # us: other tools and backup jobs on the same machine may depend on it, and
    # `rclone config disconnect` revokes the OAuth token server-side, which no
    # amount of restoring rclone.conf will undo. It takes its own explicit flag.
    if cfg.remote.name and forget_remote:
        console.print(
            f"\n[bold yellow]About to remove the rclone remote {cfg.remote.name!r}.[/bold yellow]\n"
            "This revokes its access token at the provider — anything else using "
            "this remote (backup jobs, other tools) will stop working and will "
            "need re-authorising."
        )
        if yes or Confirm.ask("Continue?", default=False):
            rclone_forget(cfg.remote.name)
    elif cfg.remote.name:
        console.print(
            f"\n[dim]Left the rclone remote {cfg.remote.name!r} alone. "
            "Use --forget-remote to remove it and revoke its access.[/dim]"
        )

    console.print(
        "\n[bold green]Done.[/bold green] To remove the program itself, run:\n"
        f"  [cyan]{pip_uninstall_command()}[/cyan]"
    )


@app.command()
def prune(ctx: typer.Context) -> None:
    """Manually prune local stage dirs that are past the retention cutoff."""
    from .cleanup import prune_stage

    cfg: Config = ctx.obj
    n = prune_stage(cfg.paths.stage_dir, cfg.retention.stage_days)
    console.print(f"Pruned {n} stage dir(s).")


@app.command("watch-run")
def watch_run(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once", help="Print current state once and exit."),
) -> None:
    """Show the live progress of the offload (read-only).

    This tails the run-state file; it never touches the offload process, so the
    window running it can be closed at any time without affecting the transfer.
    """
    import time

    from rich.progress import BarColumn, Progress, TextColumn

    from .runstate import read_state

    cfg: Config = ctx.obj

    def render_once() -> str | None:
        st = read_state(cfg.paths)
        return st.status if st else None

    if once:
        st = read_state(cfg.paths)
        console.print(st if st else "[dim]No run recorded yet.[/dim]")
        return

    console.print("[bold]DJI Auto Upload[/bold] — you can close this window any time; "
                  "the upload keeps going.\n")
    with Progress(
        TextColumn("[cyan]{task.fields[stage]}[/cyan]"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[msg]}"),
        console=console,
    ) as progress:
        task = progress.add_task("run", total=100, stage="…", msg="")
        idle = 0
        while True:
            st = read_state(cfg.paths)
            if st is None:
                idle += 1
                if idle > 10:
                    console.print("[dim]No active run.[/dim]")
                    return
                time.sleep(1)
                continue
            idle = 0
            progress.update(
                task, completed=min(100.0, st.percent),
                stage=st.stage or st.status,
                msg=(st.current or st.message or "")[:48],
            )
            if st.status in ("done", "failed", "skipped"):
                break
            time.sleep(1)

    st = read_state(cfg.paths)
    if st and st.status == "done":
        console.print(f"\n[bold green]✅ Done.[/bold green] {st.message}")
    elif st and st.status == "failed":
        console.print(f"\n[bold red]❌ Failed[/bold red] in {st.stage}: {st.error}")
        console.print(f"[dim]Log: {cfg.paths.log_file}  •  run `dji-auto-upload diagnose`[/dim]")
    else:
        console.print("\n[yellow]Run ended without a clear result — check the log.[/yellow]")


@app.command()
def diagnose(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Where to write the bundle. Default: next to the log file."
    ),
    show: bool = typer.Option(False, "--show", help="Print the bundle instead of writing it."),
) -> None:
    """Collect a support bundle (versions, config, logs) into one shareable file.

    Secrets are redacted. Attach the file when reporting a problem.
    """
    from .diagnostics import build_report, write_report

    cfg: Config = ctx.obj
    if show:
        console.print(build_report(cfg))
        return
    path = write_report(cfg, output)
    console.print(f"[green]Wrote support bundle to[/green] {path}")
    console.print(
        "[dim]Telegram token and chat id are redacted. It does contain local paths "
        "and media filenames — have a look before sharing it.[/dim]"
    )


# Explicit name: Typer would otherwise turn `_watch` into the command `-watch`
# (underscores -> hyphens), which the macOS LaunchAgent's argv `_watch` could
# never invoke — the reason the macOS trigger never once ran.
@app.command("_watch", hidden=True)
def _watch(ctx: typer.Context) -> None:
    """Internal: macOS LaunchAgent entry point — resident volume watcher."""
    from .installers.macos_launchd import watch_loop

    watch_loop(ctx.obj)


@app.command()
def run(
    ctx: typer.Context,
    device: str | None = typer.Option(
        None,
        "--device",
        help="Mount point or block device path. If omitted, autodetect.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be copied, uploaded, and deleted. Changes nothing.",
    ),
) -> None:
    """Run one offload pass against the given device, or autodetect."""
    from .notifier import NullNotifier

    cfg: Config = ctx.obj
    notifier: Notifier = NullNotifier() if dry_run else make_notifier(cfg)

    try:
        with single_flight(cfg.paths.lock_file):
            _run_inner(cfg, notifier, device, dry_run=dry_run)
    except AlreadyRunning:
        # Distinct from success: "another window is already doing it" must NOT be
        # reported to the user as "offload complete, footage in the cloud".
        log.info("another offload pass is already in flight; exiting")
        console.print("[yellow]An offload is already running — this pass did nothing.[/yellow]")
        raise typer.Exit(code=EXIT_ALREADY_RUNNING) from None
    except OffloadError as exc:
        from .offload import report_failure

        log.error("offload failed at stage=%s: %s", exc.stage, exc)
        report_failure(notifier, exc, cfg.paths.log_file)
        raise typer.Exit(code=1) from None
    except KeyboardInterrupt:
        from .offload import report_failure

        log.warning("interrupted by user")
        report_failure(notifier, KeyboardInterrupt("interrupted"), cfg.paths.log_file)
        raise typer.Exit(code=130) from None
    except Exception as exc:
        from .offload import report_failure

        log.exception("unexpected failure")
        report_failure(notifier, exc, cfg.paths.log_file)
        raise typer.Exit(code=1) from None


def _run_inner(cfg: Config, notifier: Notifier, device: str | None, *, dry_run: bool = False) -> None:
    from .detect import describe_volume, find_dji_volume, has_dji_dcim, is_dji_volume
    from .errors import DetectError, MountError
    from .inventory import find_dcim_dirs
    from .offload import OffloadRun
    from .platform_glue import resolve_volume

    if device is None:
        vol = find_dji_volume(cfg.detect)
        if vol is None:
            raise DetectError("no DJI volume detected — plug in your drone or pass --device")
        volume_path = vol.mountpoint
    else:
        volume_path = resolve_volume(device, cfg)

    if not volume_path or not volume_path.exists():
        raise MountError(f"{volume_path} is not accessible")

    # The triggers hand us --device, so strict_detect has to be enforced here too
    # or "only ever touch real DJI volumes" would not hold for the automatic path.
    if (
        device is not None
        and cfg.detect.strict_detect
        and not is_dji_volume(describe_volume(volume_path), cfg.detect)
    ):
        raise DetectError(
            f"{volume_path} is not a DJI volume by vendor ID or label, and "
            "strict_detect is on — refusing to offload it"
        )

    if not has_dji_dcim(volume_path, cfg.detect.dcim_subdirs):
        raise DetectError(
            f"{volume_path} doesn't look like a DJI media volume (no DCIM with expected layout)"
        )

    dcim_dirs = find_dcim_dirs(volume_path, cfg.detect.dcim_subdirs)
    if not dcim_dirs:
        raise DetectError(f"could not locate a DCIM folder under {volume_path}")
    if len(dcim_dirs) > 1:
        log.info("found %d media folders: %s", len(dcim_dirs),
                 ", ".join(d.name for d in dcim_dirs))

    OffloadRun(
        config=cfg,
        notifier=notifier,
        volume=volume_path,
        dcim=dcim_dirs[0],
        dcim_dirs=dcim_dirs,
        dry_run=dry_run,
    ).execute()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
