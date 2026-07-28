"""Cross-platform single-flight lock.

filelock works on Linux (flock), macOS (flock), and Windows (msvcrt). We keep
the original bash semantics: if another offload is already in flight, exit
silently with rc=0 — udev/launchd/Task Scheduler can fire double for one plug-in
event and we don't want that to be visible.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .errors import AlreadyRunning


@contextmanager
def single_flight(lock_path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    """Acquire a process-wide lock or raise AlreadyRunning.

    `timeout=0` means non-blocking — same as `flock -n` in the bash version.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise AlreadyRunning(f"another offload pass holds {lock_path}") from exc
