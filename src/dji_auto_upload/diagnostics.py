"""Support bundle: one file a user can send us when something goes wrong.

The people running this are drone pilots, not developers. "Send me the output
of X" is a bad ask; "run this one command and attach the file" is a good one.

Everything here is read-only and redacted: the Telegram bot token and chat ID
never make it in, and credentials.toml is reported as present/absent only. The
bundle does contain local paths and media filenames, which is stated in the
header so nobody is surprised by what they are handing over.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import Config
from .logging_setup import tail_log

REDACTED = "<redacted>"


def _run(cmd: list[str], timeout: int = 15) -> str:
    if not shutil.which(cmd[0]):
        return f"({cmd[0]} not found on PATH)"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"({' '.join(cmd)} failed: {exc})"
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or f"(no output, rc={proc.returncode})"


def _redact_config(text: str, secrets: list[str]) -> str:
    """Blank out any secret value that appears in the text."""
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, REDACTED)
    return text


def _trigger_state() -> str:
    if sys.platform.startswith("linux"):
        rule = Path("/etc/udev/rules.d/99-dji-auto-upload.rules")
        if not rule.is_file():
            return "udev rule NOT installed (run: sudo dji-auto-upload install-trigger)"
        # The RUN+= line shows which config and rclone.conf the trigger will use,
        # which is the single most common Linux misconfiguration.
        try:
            body = rule.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"udev rule present but unreadable: {exc}"
        runs = [ln.strip() for ln in body.splitlines() if "RUN+=" in ln]
        state = "udev rule installed:\n" + "\n".join(runs[:4])
        # The Windows bundle carries watcher.log; the Linux equivalent is the
        # journal for the transient units the rule dispatches.
        journal = _run(
            ["journalctl", "--no-pager", "-n", "40",
             "--identifier", "dji-auto-upload", "--identifier", "systemd"],
            timeout=20,
        )
        if journal and not journal.startswith("("):
            state += "\n\nrecent journal:\n" + journal[-2000:]
        return state
    if sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/com.dji-auto-upload.watcher.plist"
        state = "installed" if plist.is_file() else "NOT installed"
        return f"LaunchAgent {state} ({plist})\n" + _run(["launchctl", "list"])[:400]
    if sys.platform == "win32":
        return _run(["schtasks", "/query", "/tn", "DJI Auto Upload Watcher", "/v", "/fo", "list"])
    return f"(no trigger support for {sys.platform})"


def _windows_watcher_log() -> str:
    if sys.platform != "win32":
        return ""
    import os

    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return ""
    log = Path(base) / "dji-auto-upload" / "watcher.log"
    if not log.is_file():
        return "(watcher.log not found — the Scheduled Task may never have started)"
    return tail_log(log, n=100)


def _stage_summary(stage_dir: Path) -> str:
    if not stage_dir.is_dir():
        return f"(no stage dir at {stage_dir})"
    lines = []
    for d in sorted(p for p in stage_dir.iterdir() if p.is_dir()):
        files = [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")]
        total_mb = sum(f.stat().st_size for f in files) // 1024 // 1024
        ledger = d / ".uploaded"
        n_led = len(ledger.read_text(encoding="utf-8").split()) if ledger.is_file() else 0
        lines.append(f"  {d.name}: {len(files)} file(s), {total_mb} MB, {n_led} ledgered")
    return "\n".join(lines) or "  (stage dir is empty)"


def build_report(cfg: Config) -> str:
    """Assemble the support bundle text."""
    secrets = [cfg.telegram.bot_token, cfg.telegram.chat_id]

    try:
        config_text = cfg.paths.config_file.read_text(encoding="utf-8")
    except OSError as exc:
        config_text = f"(could not read config: {exc})"
    config_text = _redact_config(config_text, secrets)

    sections: list[tuple[str, str]] = [
        (
            "environment",
            f"dji-auto-upload {__version__}\n"
            f"python           {sys.version.split()[0]} ({sys.executable})\n"
            f"platform         {platform.platform()}\n"
            f"machine          {platform.machine()}\n"
            f"generated        {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
        ),
        (
            "paths",
            f"config      {cfg.paths.config_file}\n"
            f"credentials {cfg.paths.credentials_file} "
            f"({'present' if cfg.paths.credentials_file.is_file() else 'absent'})\n"
            f"stage       {cfg.paths.stage_dir}\n"
            f"log         {cfg.paths.log_file}",
        ),
        ("config.toml (secrets redacted)", config_text),
        (
            "telegram",
            f"notifier enabled: {cfg.notifier.enabled}\n"
            f"credentials configured: {cfg.telegram.configured}\n"
            f"(token and chat id are deliberately not included)",
        ),
        ("rclone", _run(["rclone", "version"]) + "\n\nremotes:\n" + _run(["rclone", "listremotes"])),
        ("configured remote", f"{cfg.remote.name}: -> {cfg.remote.path_template}"),
        ("auto-trigger", _trigger_state()),
        ("local stage", _stage_summary(cfg.paths.stage_dir)),
        ("last 200 log lines", tail_log(cfg.paths.log_file, n=200) or "(log is empty)"),
    ]

    win_log = _windows_watcher_log()
    if win_log:
        sections.append(("windows watcher log (last 100 lines)", win_log))

    for label, path in (
        ("macos watcher stderr", cfg.paths.log_dir / "watcher.err.log"),
        ("macos watcher stdout", cfg.paths.log_dir / "watcher.out.log"),
    ):
        if path.is_file():
            sections.append((f"{label} (last 100 lines)", tail_log(path, n=100)))

    parts = [
        "dji-auto-upload support bundle",
        "=" * 70,
        "Secrets (Telegram bot token / chat id) are redacted. This file DOES",
        "contain local paths and media filenames — check it before sharing.",
        "",
    ]
    for title, body in sections:
        parts.append(f"----- {title} " + "-" * max(0, 63 - len(title)))
        parts.append(_redact_config(body, secrets))
        parts.append("")
    return "\n".join(parts)


def write_report(cfg: Config, output: Path | None = None) -> Path:
    """Write the bundle next to the log file (or wherever asked) and return the path."""
    if output is None:
        stamp = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        output = cfg.paths.log_dir / f"dji-auto-upload-diagnostics-{stamp}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(cfg), encoding="utf-8")
    return output
