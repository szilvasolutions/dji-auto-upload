"""Interactive setup wizard.

Walks a non-IT user through:
- detecting/installing rclone
- picking or creating an rclone remote (with a Google Photos cheat sheet)
- testing the remote
- choosing retention defaults
- (optional) Telegram bot setup, with auto-discovery of chat_id
- (optional) installing the per-OS auto-trigger

Re-runnable. Saves writes atomically (tempfile + os.replace).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import tomlkit
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .config import (
    Config,
    TelegramCredentials,
    load_config_doc,
    save_config_doc,
    save_credentials,
    write_default_config,
    write_default_credentials,
)
from .upload import list_remotes, remote_reachable

log = logging.getLogger(__name__)
console = Console()


def run_wizard(cfg: Config) -> None:
    console.print(
        Panel.fit(
            "[bold]dji-auto-upload setup[/bold]\n"
            "We'll walk through rclone, retention, and (optionally) Telegram.\n"
            "Re-run anytime — your edits to the TOML files are preserved.",
            border_style="cyan",
        )
    )
    console.print(f"Config:       [dim]{cfg.paths.config_file}[/dim]")
    console.print(f"Credentials:  [dim]{cfg.paths.credentials_file}[/dim]")
    console.print(f"Stage dir:    [dim]{cfg.paths.stage_dir}[/dim]")
    console.print()

    write_default_config(cfg.paths.config_file)
    write_default_credentials(cfg.paths.credentials_file)
    doc = load_config_doc(cfg.paths.config_file)

    _check_rclone()
    remote_name, path_template = _pick_remote(doc)
    _retention(doc)
    save_config_doc(cfg.paths.config_file, doc)
    console.print(f"[green]Saved[/green] {cfg.paths.config_file}")

    creds = _telegram(doc, cfg)
    if creds is not None:
        save_credentials(cfg.paths.credentials_file, creds)
        console.print(f"[green]Saved[/green] {cfg.paths.credentials_file}")
    save_config_doc(cfg.paths.config_file, doc)

    _install_trigger_prompt()
    _summary(remote_name, path_template, doc)


# ---- Wizard sections ------------------------------------------------------


def _check_rclone() -> None:
    if shutil.which("rclone"):
        return
    url = "https://rclone.org/install/"
    console.print(
        Panel(
            f"[red]rclone not found on PATH[/red]\n"
            f"Install it from: [link={url}]{url}[/link]\n\n"
            "macOS: [cyan]brew install rclone[/cyan]\n"
            "Linux: [cyan]curl https://rclone.org/install.sh | sudo bash[/cyan]\n"
            "Windows: [cyan]winget install Rclone.Rclone[/cyan]",
            title="Missing dependency",
            border_style="red",
        )
    )
    console.print("Re-run [cyan]dji-auto-upload setup[/cyan] once rclone is installed.")
    sys.exit(1)


def _pick_remote(doc: tomlkit.TOMLDocument) -> tuple[str, str]:
    remote_table = doc.setdefault("remote", tomlkit.table())
    current = str(remote_table.get("name", "gphotos"))
    current_template = str(remote_table.get("path_template", "album/DJI-{date}"))

    remotes = list_remotes()
    if remotes:
        console.print("\n[bold]Configured rclone remotes:[/bold]")
        for i, r in enumerate(remotes, 1):
            tag = "  [dim](current)[/dim]" if r == current else ""
            console.print(f"  {i}. {r}{tag}")
        console.print(f"  {len(remotes) + 1}. Add a new one with [cyan]rclone config[/cyan]")
        choice = IntPrompt.ask(
            "Which remote?",
            default=remotes.index(current) + 1 if current in remotes else 1,
            choices=[str(i) for i in range(1, len(remotes) + 2)],
        )
        if choice == len(remotes) + 1:
            _run_rclone_config()
            return _pick_remote(doc)
        chosen = remotes[choice - 1]
    else:
        console.print("\n[yellow]No rclone remotes configured yet.[/yellow]")
        console.print(
            Panel(
                "dji-auto-upload uploads through [bold]rclone[/bold], which speaks Google\n"
                "Drive, Dropbox, OneDrive, S3, a NAS, and ~70 other backends. Pick whichever\n"
                "you like — [cyan]rclone config[/cyan] walks you through it, and most are a single\n"
                "browser sign-in.\n\n"
                "[dim]See the README “Setting up rclone” section for a per-provider guide,\n"
                "including the extra step Google Photos needs for its own upload quota.[/dim]",
                title="Choose your cloud",
                border_style="blue",
            )
        )
        if Confirm.ask("Run [cyan]rclone config[/cyan] now?", default=True):
            _run_rclone_config()
        return _pick_remote(doc)

    # Test reachability.
    console.print(f"Testing [cyan]{chosen}:[/cyan] ", end="")
    if remote_reachable(chosen, timeout=10):
        console.print("[green]OK[/green]")
    else:
        console.print("[yellow]could not list — credentials may need a refresh[/yellow]")

    # Path template.
    suggested = "album/DJI-{date}" if "photos" in chosen.lower() else "DJI/{date}"
    if current_template:
        suggested = current_template
    template = Prompt.ask(
        "Remote path template ([dim]{date} expands to YYYY-MM-DD[/dim])",
        default=suggested,
    )

    remote_table["name"] = chosen
    remote_table["path_template"] = template
    return chosen, template


def _run_rclone_config() -> None:
    console.print("\n[dim]Launching `rclone config` — return here when done.[/dim]\n")
    subprocess.run(["rclone", "config"], check=False)


def _retention(doc: tomlkit.TOMLDocument) -> None:
    retention = doc.setdefault("retention", tomlkit.table())
    behaviour = doc.setdefault("behaviour", tomlkit.table())
    console.print()

    # --- Local copies on this computer ---
    if Confirm.ask(
        "Keep a permanent copy of your footage on this computer?",
        default=True,
    ):
        retention["stage_days"] = 0
    else:
        retention["stage_days"] = IntPrompt.ask(
            "Delete the local copy how many days after it's uploaded?",
            default=int(retention.get("stage_days", 2)) or 2,
        )

    # --- Footage on the drone itself (destructive — opt-in) ---
    console.print(
        "\n[dim]By default we never delete anything from the drone. You can let it "
        "auto-trim old clips once they're safely backed up to the cloud.[/dim]"
    )
    if Confirm.ask(
        "Delete footage off the drone after it's uploaded?",
        default=False,
    ):
        days = IntPrompt.ask(
            "Delete drone files older than how many days? ([dim]e.g. 1, 2, 3, 4[/dim])",
            default=int(retention.get("drone_days", 2)) or 2,
        )
        retention["drone_days"] = days
        behaviour["delete_drone_files"] = True
        console.print(
            f"[yellow]Drone auto-trim ON:[/yellow] clips older than {days} day(s) "
            "are removed from the drone only after a confirmed upload."
        )
    else:
        retention["drone_days"] = 0
        behaviour["delete_drone_files"] = False
        console.print("[green]Drone stays untouched[/green] — you delete clips yourself.")


def _telegram(doc: tomlkit.TOMLDocument, cfg: Config) -> TelegramCredentials | None:
    console.print()
    enable = Confirm.ask("Enable Telegram notifications?", default=cfg.notifier.enabled)
    notifier_table = doc.setdefault("notifier", tomlkit.table())
    notifier_table["enabled"] = enable

    if not enable:
        return None

    console.print(
        Panel(
            "[bold]Get a Telegram bot token:[/bold]\n"
            "  1. Open [link=https://t.me/BotFather]@BotFather[/link] in Telegram\n"
            "  2. Send [cyan]/newbot[/cyan] and follow the prompts\n"
            "  3. Copy the token BotFather gives you (looks like [dim]123456:ABC-DEF…[/dim])",
            border_style="cyan",
        )
    )

    token = Prompt.ask("Bot token", default=cfg.telegram.bot_token, password=True).strip()
    if not token:
        console.print("[yellow]Skipping Telegram setup.[/yellow]")
        notifier_table["enabled"] = False
        return None

    chat_id = cfg.telegram.chat_id
    if Confirm.ask(
        "Auto-discover your chat ID? ([dim]we'll ask you to message your bot[/dim])",
        default=True,
    ):
        chat_id = _discover_chat_id(token) or chat_id

    if not chat_id:
        chat_id = Prompt.ask("Chat ID (paste manually)").strip()

    creds = TelegramCredentials(bot_token=token, chat_id=chat_id)
    if not _send_telegram_test(creds):
        console.print("[yellow]Test message failed. Saving anyway — re-run setup to fix.[/yellow]")
    else:
        console.print("[green]✓ Test message delivered.[/green]")
    return creds


def _discover_chat_id(token: str) -> str | None:
    console.print(
        "\n[bold]Now message your bot from Telegram[/bold] "
        "(any text — say 'hi'). I'll watch for 30 seconds…\n"
    )
    deadline = time.time() + 30
    last_offset = 0
    while time.time() < deadline:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": last_offset + 1, "timeout": 5},
                timeout=10,
            )
        except requests.RequestException:
            return None
        if not r.ok:
            console.print(f"[yellow]Telegram API error: {r.text[:200]}[/yellow]")
            return None
        data = r.json()
        updates = data.get("result", [])
        for u in updates:
            last_offset = max(last_offset, u.get("update_id", 0))
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id") is not None:
                cid = str(chat["id"])
                who = chat.get("first_name") or chat.get("username") or "?"
                # Anyone can message a bot. Confirm this sender is actually the
                # user before wiring every future notification to that chat.
                if Confirm.ask(
                    f"Got a message from [bold]{who}[/bold] (chat ID {cid}). Is that you?",
                    default=True,
                ):
                    return cid
                console.print("[yellow]Ignoring that sender — still watching…[/yellow]")
        time.sleep(2)
    console.print("[yellow]Timed out without seeing a message.[/yellow]")
    return None


def _send_telegram_test(creds: TelegramCredentials) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{creds.bot_token}/sendMessage",
            data={
                "chat_id": creds.chat_id,
                "text": "✅ dji-auto-upload setup complete. You'll get notifications here.",
            },
            timeout=10,
        )
        return r.ok
    except requests.RequestException:
        return False


def _install_trigger_prompt() -> None:
    console.print()
    if not Confirm.ask("Install the auto-trigger now (so plug-in events fire offload)?", default=True):
        console.print("[dim]Skip. Run [cyan]dji-auto-upload install-trigger[/cyan] later when you're ready.[/dim]")
        return
    # We don't have cfg threaded in here; callers will pass it in. For now,
    # re-load and call install.
    from .config import load
    from .errors import ConfigError
    from .installers import install

    try:
        install(load(), force=False)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")


def _summary(remote: str, template: str, doc: tomlkit.TOMLDocument) -> None:
    table = Table(title="Setup summary", show_header=False, box=None)
    table.add_column("key", style="cyan")
    table.add_column("value")
    retention = doc.get("retention", {})
    table.add_row("rclone remote", remote)
    table.add_row("path template", template)
    table.add_row("stage retention", f"{retention.get('stage_days', 2)} day(s)")
    table.add_row("drone retention", f"{retention.get('drone_days', 1)} day(s)")
    console.print()
    console.print(table)
    console.print(
        "\n[bold]Next:[/bold] plug in your DJI drone, "
        "or run [cyan]dji-auto-upload run --device <path>[/cyan] to test."
    )


def _ensure_default_files(p: Path) -> None:
    """Public for tests."""
    p.parent.mkdir(parents=True, exist_ok=True)
