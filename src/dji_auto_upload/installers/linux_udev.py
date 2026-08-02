"""Linux auto-trigger: a udev rule that dispatches via systemd-run.

Why systemd-run:
- udev workers run under a seccomp filter that blocks the legacy mount(2)
  syscall, so anything spawned via RUN+= inherits the filter and can't mount.
  Dispatching via `systemd-run` (no --scope, with --collect) launches the
  script as a child of PID 1, escaping the filter.
- The transient unit gets logged to journald automatically.
- --collect garbage-collects the unit on exit so a previous failure doesn't
  block the next replug.

The udev rule matches three signals: USB vendor 2ca3 (DJI), volume label DJI,
volume label DJIMEDIA. Whichever fires first wins; the unit name is keyed on
%k so the same drone replugged twice produces a fresh unit name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console

from ..config import Config

console = Console()

UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-dji-auto-upload.rules")
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _render_rule(binary_path: str, vendor_ids: tuple[str, ...], labels: tuple[str, ...]) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(disabled_extensions=("rules", "j2", "plist", "ps1", "xml")),
        keep_trailing_newline=True,
    )
    return env.get_template("99-dji-auto-upload.rules.j2").render(
        binary=binary_path,
        vendor_ids=list(vendor_ids),
        labels=list(labels),
    )


def install(cfg: Config, *, force: bool = False) -> None:
    if sys.platform != "linux" and not sys.platform.startswith("linux"):
        console.print("[red]linux_udev installer called on non-Linux[/red]")
        return
    if os.geteuid() != 0:
        console.print(
            f"[yellow]Need root to write {UDEV_RULE_PATH}.[/yellow]\n"
            "Re-run with: [cyan]sudo dji-auto-upload install-trigger[/cyan]"
        )
        return

    if UDEV_RULE_PATH.exists() and not force:
        console.print(
            f"[yellow]{UDEV_RULE_PATH} already exists.[/yellow] "
            "Use [cyan]--force[/cyan] to overwrite."
        )
        return

    binary = shutil.which("dji-auto-upload") or "/usr/local/bin/dji-auto-upload"
    rule_text = _render_rule(binary, cfg.detect.vendor_ids, cfg.detect.volume_labels)
    UDEV_RULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UDEV_RULE_PATH.write_text(rule_text, encoding="utf-8")
    UDEV_RULE_PATH.chmod(0o644)
    console.print(f"[green]Wrote[/green] {UDEV_RULE_PATH}")

    _reload_udev()
    console.print(
        "[bold green]Done.[/bold green] Plug in your drone — "
        "watch with [cyan]journalctl -t systemd[/cyan] or "
        "[cyan]journalctl --user -t dji-auto-upload[/cyan]."
    )


def uninstall(cfg: Config) -> None:
    if sys.platform != "linux" and not sys.platform.startswith("linux"):
        console.print("[red]linux_udev installer called on non-Linux[/red]")
        return
    if os.geteuid() != 0:
        console.print(
            f"[yellow]Need root to remove {UDEV_RULE_PATH}.[/yellow] "
            "Re-run with [cyan]sudo[/cyan]."
        )
        return
    if not UDEV_RULE_PATH.exists():
        console.print(f"[dim]{UDEV_RULE_PATH} not present — nothing to do.[/dim]")
        return
    UDEV_RULE_PATH.unlink()
    console.print(f"[green]Removed[/green] {UDEV_RULE_PATH}")
    _reload_udev()


def _reload_udev() -> None:
    if shutil.which("udevadm") is None:
        console.print("[yellow]udevadm not found — reload manually.[/yellow]")
        return
    subprocess.run(["udevadm", "control", "--reload"], check=False)
    subprocess.run(["udevadm", "trigger"], check=False)
