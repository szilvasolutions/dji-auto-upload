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
    table.add_row("rclone remote", cfg.remote.name)
    table.add_row("path template", cfg.remote.path_template)
    remotes = list_remotes()
    if cfg.remote.name in remotes:
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
    from .logging_setup import tail_log

    last = tail_log(cfg.paths.log_file, n=1).strip()
    table.add_row("last run", last or "[dim](no runs logged yet)[/dim]")
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
def prune(ctx: typer.Context) -> None:
    """Manually prune local stage dirs that are past the retention cutoff."""
    from .cleanup import prune_stage

    cfg: Config = ctx.obj
    n = prune_stage(cfg.paths.stage_dir, cfg.retention.stage_days)
    console.print(f"Pruned {n} stage dir(s).")


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


@app.command(hidden=True)
def _watch(ctx: typer.Context) -> None:
    """Internal: macOS LaunchAgent entry point — resident DiskArbitration watcher."""
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
        log.info("another offload pass is already in flight; exiting silently")
        raise typer.Exit(code=0) from None
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
    from .inventory import find_dcim
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

    dcim = find_dcim(volume_path, cfg.detect.dcim_subdirs)
    if dcim is None:
        raise DetectError(f"could not locate a DCIM folder under {volume_path}")

    OffloadRun(
        config=cfg, notifier=notifier, volume=volume_path, dcim=dcim, dry_run=dry_run
    ).execute()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
