"""Source → stage transfer.

Replaces the bash version's `rsync --partial-dir`. Strategy: copy to
`<dest>.part`, then `os.replace` to atomic-rename on success — half-files can
never masquerade as complete because they live under a different name. We also
preserve mtime (date grouping depends on it) and verify size after copy.

Not as clever as rsync (no byte-resume on a single huge file), but DJI clips
top out around 5 GB and the stage is local SSD — re-copying a partial file
costs seconds. The portability win pays for it.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import CopyError, DroneDisconnected
from .inventory import FileInfo

log = logging.getLogger(__name__)


@dataclass
class CopyResult:
    copied: int = 0
    skipped: int = 0
    bytes_copied: int = 0


def needs_copy(src: FileInfo, dest: Path) -> bool:
    if not dest.exists():
        return True
    try:
        return dest.stat().st_size != src.size
    except OSError:
        return True


def copy_one(src: FileInfo, dest: Path, *, verify: bool, timeout_sec: int) -> None:
    """Copy one file atomically. Raises CopyError or DroneDisconnected on failure.

    `timeout_sec` is advisory: shutil.copy2 doesn't take a timeout, so we wrap
    the underlying read in a worker thread guarded by a deadline. For typical
    drone clips ≤ 5 GB on USB-3, copies complete in under 30 s — the timeout is
    a hang-detector, not a normal-path budget.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        part.unlink()

    try:
        _copy_with_timeout(src.path, part, timeout_sec)
    except FileNotFoundError as exc:
        # Source vanished — drone unplugged mid-copy.
        if part.exists():
            part.unlink(missing_ok=True)
        raise DroneDisconnected(f"source vanished mid-copy: {src.path}") from exc
    except OSError as exc:
        if part.exists():
            part.unlink(missing_ok=True)
        # On Linux, an unplugged drone manifests as EIO/ENXIO partway through.
        if not src.path.exists():
            raise DroneDisconnected(f"source unreachable mid-copy: {src.path}") from exc
        raise CopyError(f"copy {src.path} → {dest} failed: {exc}") from exc

    if verify and part.stat().st_size != src.size:
        part.unlink(missing_ok=True)
        raise CopyError(
            f"size mismatch after copy: {part} = {part.stat().st_size} bytes, expected {src.size}"
        )

    # Preserve mtime — group_by_date relies on it.
    os.utime(part, (src.mtime, src.mtime))
    os.replace(part, dest)


def _copy_with_timeout(src: Path, dest: Path, timeout_sec: int) -> None:
    """shutil.copy2 in a thread with a timeout. Cross-platform and dependency-free."""
    import threading

    err: list[BaseException] = []

    def worker() -> None:
        try:
            shutil.copy2(src, dest)
        except BaseException as e:
            err.append(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout_sec)
    if t.is_alive():
        # We can't kill a Python thread cleanly, but the parent process will
        # exit shortly (this is the offload pipeline's last gasp). Surface a
        # CopyError so the caller fires `fail` notify with context.
        raise CopyError(f"copy of {src} timed out after {timeout_sec}s")
    if err:
        raise err[0]


def copy_files(
    files: list[FileInfo],
    dest_dir: Path,
    *,
    verify: bool,
    timeout_sec: int,
    on_progress: Callable[[FileInfo], None] | None = None,
) -> CopyResult:
    """Copy each FileInfo to `dest_dir/<basename>`. Skips files already at the right size."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = CopyResult()
    for fi in files:
        dest = dest_dir / fi.path.name
        if not needs_copy(fi, dest):
            result.skipped += 1
            continue
        copy_one(fi, dest, verify=verify, timeout_sec=timeout_sec)
        result.copied += 1
        result.bytes_copied += fi.size
        if on_progress is not None:
            on_progress(fi)
    return result
