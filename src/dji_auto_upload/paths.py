"""Per-OS directory resolution.

Wraps platformdirs so the rest of the code never branches on sys.platform for
config/data/log/cache/state paths. Linux-root fallback (used when udev runs the
binary as root and HOME is unset) maps to /etc, /var/lib, /var/log.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "dji-auto-upload"
APP_AUTHOR = "dji-auto-upload"

CONFIG_FILENAME = "config.toml"
CREDENTIALS_FILENAME = "credentials.toml"
LOG_FILENAME = "dji-auto-upload.log"
LOCK_FILENAME = "dji-auto-upload.lock"


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    log_dir: Path
    runtime_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILENAME

    @property
    def credentials_file(self) -> Path:
        return self.config_dir / CREDENTIALS_FILENAME

    @property
    def stage_dir(self) -> Path:
        return self.data_dir / "stage"

    @property
    def log_file(self) -> Path:
        return self.log_dir / LOG_FILENAME

    @property
    def lock_file(self) -> Path:
        return self.runtime_dir / LOCK_FILENAME


def _is_running_as_root() -> bool:
    return sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() == 0


def get_paths() -> AppPaths:
    """Resolve the right config/data/log/runtime dirs for the current OS+user."""
    if sys.platform.startswith("linux") and _is_running_as_root():
        # System-wide layout when invoked by udev under PID 1.
        return AppPaths(
            config_dir=Path("/etc/dji-auto-upload"),
            data_dir=Path("/var/lib/dji-auto-upload"),
            log_dir=Path("/var/log/dji-auto-upload"),
            runtime_dir=Path("/run/dji-auto-upload"),
        )

    pd = PlatformDirs(APP_NAME, APP_AUTHOR, ensure_exists=False, roaming=False)
    runtime = Path(pd.user_runtime_dir) if pd.user_runtime_dir else Path(pd.user_cache_dir)
    return AppPaths(
        config_dir=Path(pd.user_config_dir),
        data_dir=Path(pd.user_data_dir),
        log_dir=Path(pd.user_log_dir),
        runtime_dir=runtime,
    )


def ensure_dirs(paths: AppPaths) -> None:
    for d in (paths.config_dir, paths.data_dir, paths.stage_dir, paths.log_dir, paths.runtime_dir):
        d.mkdir(parents=True, exist_ok=True)
