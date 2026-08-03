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
import time
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


def copy_one(
    src: FileInfo,
    dest: Path,
    *,
    verify: bool,
    timeout_sec: int,
    on_bytes: Callable[[int], None] | None = None,
) -> None:
    """Copy one file atomically. Raises CopyError or DroneDisconnected on failure.

    Copies in chunks rather than via shutil.copy2 so `on_bytes` can report
    progress *within* a file: drone clips run to several GB, and a per-file
    counter leaves a progress bar frozen for a minute or more per clip.

    `timeout_sec` is an inactivity budget — it only expires when a read has
    stopped producing data, so a slow-but-working device is never killed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        part.unlink()

    try:
        _copy_stream(src.path, part, timeout_sec, on_bytes)
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


# 4 MiB: large enough that syscall overhead is irrelevant on USB, small enough
# that the progress bar updates several times a second on a fast link.
CHUNK_BYTES = 4 * 1024 * 1024


def _copy_stream(
    src: Path,
    dest: Path,
    timeout_sec: int,
    on_bytes: Callable[[int], None] | None = None,
) -> None:
    """Chunked copy that reports bytes as they land and detects a stalled read.

    The copy runs in a worker thread and the caller's thread watches a shared
    byte counter. Both halves are needed: a blocking read() cannot be
    interrupted from the same thread, so a stall check between chunks would
    never run; and a plain shutil.copy2 in a thread gives no visibility inside
    a multi-gigabyte file. `timeout_sec` is an inactivity budget — it fires only
    when the counter has stopped moving, so a slow-but-working device is never
    killed mid-copy.
    """
    import threading

    copied = [0]
    err: list[BaseException] = []
    done = threading.Event()

    def worker() -> None:
        try:
            with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
                while True:
                    buf = fsrc.read(CHUNK_BYTES)
                    if not buf:
                        break
                    fdst.write(buf)
                    copied[0] += len(buf)
        except BaseException as e:  # re-raised on the caller's thread below
            err.append(e)
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    reported = 0
    last_change = time.monotonic()
    while not done.wait(0.25):
        now_copied = copied[0]
        if now_copied != reported:
            if on_bytes is not None:
                on_bytes(now_copied - reported)
            reported = now_copied
            last_change = time.monotonic()
        elif time.monotonic() - last_change > timeout_sec:
            # The thread cannot be killed, but the process is about to fail the
            # run and exit, which takes it with us.
            raise CopyError(f"copy of {src} stalled for {timeout_sec}s")

    if err:
        raise err[0]
    if on_bytes is not None and copied[0] != reported:
        on_bytes(copied[0] - reported)
    shutil.copystat(src, dest, follow_symlinks=True)


def copy_files(
    files: list[FileInfo],
    dest_dir: Path,
    *,
    verify: bool,
    timeout_sec: int,
    on_progress: Callable[[FileInfo], None] | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> CopyResult:
    """Copy each FileInfo to `dest_dir/<staged-name>`. Skips files already at the right size.

    Uses fi.staged, not the raw basename, so two source files that share a name
    (a second DCIM folder) never overwrite each other in the stage dir.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = CopyResult()
    for fi in files:
        dest = dest_dir / fi.staged
        if not needs_copy(fi, dest):
            result.skipped += 1
            continue
        copy_one(fi, dest, verify=verify, timeout_sec=timeout_sec, on_bytes=on_bytes)
        result.copied += 1
        result.bytes_copied += fi.size
        if on_progress is not None:
            on_progress(fi)
    return result
