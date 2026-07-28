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


# Untrusted removable media: never honour setuid bits, device nodes, or
# executables on the card.
_MOUNT_HARDENING = "nosuid,nodev,noexec"


def _mount_linux_ro(dev: Path, cfg: Config) -> Path:
    """Mount a block device RO (nosuid,nodev,noexec) at a unique runtime path."""
    if not dev.exists():
        raise MountError(f"block device {dev} not found")

    target = cfg.paths.runtime_dir / f"mnt-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        ["mount", "-o", f"ro,{_MOUNT_HARDENING}", str(dev), str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        target.rmdir()
        raise MountError(f"mount {dev} → {target} failed: {proc.stderr.strip()}")

    log.info("mounted %s read-only at %s", dev, target)
    _MOUNTS_TO_RELEASE.append(target)
    return target


def is_managed_mount(mountpoint: Path) -> bool:
    """True if this path is a mount we created (Linux --device path)."""
    return mountpoint in _MOUNTS_TO_RELEASE


def remount_rw(mountpoint: Path) -> bool:
    """Briefly lift the RO flag on one of our own mounts (drone cleanup needs
    to delete files). Hardening flags stay. Returns False on any failure."""
    if not is_managed_mount(mountpoint):
        return True  # macOS/Windows volumes are already writable
    proc = subprocess.run(
        ["mount", "-o", f"remount,rw,{_MOUNT_HARDENING}", str(mountpoint)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.warning("remount rw %s failed: %s", mountpoint, proc.stderr.strip())
    return proc.returncode == 0


def remount_ro(mountpoint: Path) -> None:
    """Drop back to RO after cleanup. Best-effort."""
    if not is_managed_mount(mountpoint):
        return
    subprocess.run(
        ["mount", "-o", f"remount,ro,{_MOUNT_HARDENING}", str(mountpoint)],
        capture_output=True,
        text=True,
    )


def eject_volume(volume: Path) -> bool:
    """Cleanly detach the volume so the user can unplug. Best-effort, never raises."""
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["diskutil", "eject", str(volume)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return proc.returncode == 0
        if sys.platform == "win32":
            drive = str(volume)[:2]  # "E:"
            if len(drive) != 2 or not drive[0].isalpha() or drive[1] != ":":
                return False
            ps = (
                "(New-Object -comObject Shell.Application)"
                f".Namespace(17).ParseName('{drive}\\').InvokeVerb('Eject')"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return proc.returncode == 0
        # Linux: unmount our own mounts now instead of waiting for atexit.
        release_mounts()
        return True
    except Exception as exc:  # never let eject failure tank a finished run
        log.warning("eject %s failed: %s", volume, exc)
        return False


def release_mounts() -> None:
    for mp in list(_MOUNTS_TO_RELEASE):
        try:
            subprocess.run(["umount", str(mp)], capture_output=True, text=True, timeout=10)
        except Exception:
            pass
        try:
            mp.rmdir()
        except OSError:
            pass
        _MOUNTS_TO_RELEASE.remove(mp)


atexit.register(release_mounts)
