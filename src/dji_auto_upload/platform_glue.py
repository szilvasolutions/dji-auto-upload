"""Resolve --device into a usable Path.

Linux trigger passes block-device paths like `/dev/sdc1`. We mount these RO at
a per-run mountpoint and return that. macOS/Windows pass already-mounted
volumes directly.

The mount lifetime is the OffloadRun's lifetime — we register an atexit handler
so the umount happens regardless of how the process dies, mirroring the bash
EXIT trap. This is best-effort; the kernel will reap stale mounts on reboot.
"""

from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import uuid
from pathlib import Path

from .config import Config
from .errors import MountError

log = logging.getLogger(__name__)

_MOUNTS_TO_RELEASE: list[Path] = []


def resolve_volume(device: str, cfg: Config) -> Path:
    """Turn a --device argument into a mountpoint Path."""
    p = Path(device)

    if sys.platform.startswith("linux") and device.startswith("/dev/"):
        return _mount_linux_ro(p, cfg)

    # macOS: typically /Volumes/<label>; Windows: C:\, E:\, etc.
    return p


def _mount_linux_ro(dev: Path, cfg: Config) -> Path:
    """Mount a block device RO at a unique runtime path."""
    if not dev.exists():
        raise MountError(f"block device {dev} not found")

    target = cfg.paths.runtime_dir / f"mnt-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        ["mount", "-o", "ro", str(dev), str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        target.rmdir()
        raise MountError(f"mount {dev} → {target} failed: {proc.stderr.strip()}")

    log.info("mounted %s read-only at %s", dev, target)
    _MOUNTS_TO_RELEASE.append(target)
    return target


def _release_mounts() -> None:
    for mp in list(_MOUNTS_TO_RELEASE):
        try:
            subprocess.run(["umount", str(mp)], capture_output=True, text=True, timeout=10)
        except Exception:
            pass
        try:
            mp.rmdir()
        except OSError:
            pass


atexit.register(_release_mounts)
