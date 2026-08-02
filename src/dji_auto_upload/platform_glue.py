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
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

from .config import Config
from .errors import MountError

log = logging.getLogger(__name__)

_MOUNTS_TO_RELEASE: list[Path] = []


def resolve_volume(device: str, cfg: Config) -> Path:
    """Turn a --device argument into a mountpoint Path."""
    # "E:" is drive-RELATIVE on Windows (Path("E:") / "DCIM" == "E:DCIM", resolved
    # against that drive's current directory), and the WMI watcher reports drives
    # in exactly that form. Normalise to the volume root before anything uses it.
    if re.fullmatch(r"[A-Za-z]:", device):
        device = device + "\\"

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
        # Only claim success if there was actually something of ours to release —
        # for a desktop-automounted card we haven't ejected anything, and telling
        # the user "safe to unplug" while it's still mounted risks their data.
        had_mounts = bool(_MOUNTS_TO_RELEASE)
        release_mounts()
        return had_mounts and not _MOUNTS_TO_RELEASE
    except Exception as exc:  # never let eject failure tank a finished run
        log.warning("eject %s failed: %s", volume, exc)
        return False


_INHIBIT_REASON = "DJI offload in progress"

# Windows SetThreadExecutionState flags. ES_SYSTEM_REQUIRED blocks *idle* sleep;
# we deliberately omit ES_DISPLAY_REQUIRED so the screen still turns off — the
# whole point is that the user has walked away.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def inhibit_sleep(*, enabled: bool = True, reason: str = _INHIBIT_REASON) -> Iterator[str | None]:
    """Keep the machine awake for the duration of the block.

    A run is "plug in and walk away", which is exactly when a laptop decides to
    idle-sleep and freeze the transfer mid-flight. This holds an OS sleep
    inhibitor until the run finishes, then releases it so the machine can sleep
    normally.

    Strictly best-effort: yields a short description of the mechanism that was
    acquired, or None if none was available. Never raises, and never blocks a
    run just because the inhibitor could not be taken.

    Note this only blocks *idle* sleep. Closing the lid or an explicit sleep
    still suspends the machine on every OS.
    """
    if not enabled:
        yield None
        return

    release: subprocess.Popen[bytes] | None = None
    how: str | None = None
    try:
        if sys.platform == "win32":
            how = _inhibit_windows()
        elif sys.platform == "darwin":
            release = _spawn_inhibitor(
                ["caffeinate", "-i", "-m", "-w", str(os.getpid())]
            )
            how = "caffeinate" if release else None
        else:
            # Bind the helper's lifetime to ours. A bare `sleep N` would outlive
            # a SIGKILLed run, reparent to PID 1, and keep the machine awake for
            # the rest of that sleep — caffeinate gets this via -w, so match it.
            release = _spawn_inhibitor(
                [
                    "systemd-inhibit",
                    "--what=sleep:idle",
                    "--who=dji-auto-upload",
                    f"--why={reason}",
                    "--mode=block",
                    "sh",
                    "-c",
                    f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 5; done',
                ]
            )
            how = "systemd-inhibit" if release else None
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("could not acquire sleep inhibitor: %s", exc)

    if how:
        log.info("sleep inhibitor active (%s) — machine will stay awake for this run", how)
    else:
        log.info("no sleep inhibitor available — an idle machine may sleep mid-run")

    try:
        yield how
    finally:
        if release is not None:
            with contextlib.suppress(Exception):
                release.terminate()
                release.wait(timeout=5)
        if sys.platform == "win32" and how:
            with contextlib.suppress(Exception):
                _set_execution_state(_ES_CONTINUOUS)
        if how:
            log.info("sleep inhibitor released (%s)", how)


def _spawn_inhibitor(cmd: list[str]) -> subprocess.Popen[bytes] | None:
    """Hold an inhibitor by keeping a helper process alive. None if unavailable."""
    if not shutil.which(cmd[0]):
        log.debug("%s not found — cannot inhibit sleep", cmd[0])
        return None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.debug("failed to start %s: %s", cmd[0], exc)
        return None
    # A helper that dies instantly (e.g. logind refused the inhibitor) is not
    # holding anything — report that honestly rather than claiming protection.
    try:
        if proc.wait(timeout=0.5) is not None:
            log.debug("%s exited immediately — no inhibitor held", cmd[0])
            return None
    except subprocess.TimeoutExpired:
        pass  # still running, which is what we want
    return proc


def _set_execution_state(flags: int) -> int:  # pragma: no cover - Windows only
    import ctypes

    # `windll` only exists on Windows, so the ignore is needed off-Windows and
    # redundant on it — hence the extra `unused-ignore` for the win32 mypy run.
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    return int(kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))


def _inhibit_windows() -> str | None:  # pragma: no cover - Windows only
    # Returns the previous state, or 0 on failure.
    if _set_execution_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED) == 0:
        log.debug("SetThreadExecutionState failed — cannot inhibit sleep")
        return None
    return "SetThreadExecutionState"


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
