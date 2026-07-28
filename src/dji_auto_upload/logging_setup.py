"""Logging configuration: rotating file + Rich console (TTY) or plain stream.

Designed to behave identically whether invoked interactively, by udev under
PID 1, by a macOS LaunchAgent, or by a Windows Scheduled Task.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

from .config import LoggingConfig

LOGGER_NAME = "dji_auto_upload"


def configure(log_file: Path, cfg: LoggingConfig, *, console_verbose: bool = False, quiet: bool = False) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    # Idempotent: drop existing handlers so re-configure() in the same process
    # doesn't double-log (matters for tests and for the macOS watcher daemon).
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    file_handler = RotatingFileHandler(
        log_file, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%F %T")
    )
    logger.addHandler(file_handler)

    if quiet:
        console_level = logging.WARNING
    elif console_verbose:
        console_level = logging.DEBUG
    else:
        console_level = logging.INFO

    console: logging.Handler
    if sys.stdout.isatty():
        console = RichHandler(rich_tracebacks=True, show_time=False, show_path=False)
    else:
        console = logging.StreamHandler(stream=sys.stdout)
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.setLevel(console_level)
    logger.addHandler(console)

    logger.propagate = False
    return logger


def tail_log(log_file: Path, n: int = 25) -> str:
    """Return the last `n` lines of the log file (best-effort, never raises)."""
    if not log_file.is_file():
        return ""
    try:
        with log_file.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        return b"\n".join(data.splitlines()[-n:]).decode("utf-8", errors="replace")
    except OSError:
        return ""
