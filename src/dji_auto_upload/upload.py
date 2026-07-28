"""rclone subprocess wrapper.

Uses `rclone copy --files-from` so already-uploaded files (per the .uploaded
ledger) are never sent again. Critical for the Google Photos backend, which
can't dedupe server-side — every duplicate upload creates a duplicate in the
album.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import BehaviourConfig, RemoteConfig
from .errors import UploadError

log = logging.getLogger(__name__)


@dataclass
class UploadResult:
    succeeded: list[str]   # basenames that uploaded cleanly
    failed: list[str]      # basenames in this batch (all-or-nothing per call)
    rc: int


def rclone_binary() -> str:
    return os.environ.get("RCLONE_BIN") or shutil.which("rclone") or "rclone"


def list_remotes() -> list[str]:
    """Return configured rclone remote names (without trailing colon). Empty on error."""
    try:
        proc = subprocess.run(
            [rclone_binary(), "listremotes"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.rstrip(":").strip() for line in proc.stdout.splitlines() if line.strip()]


def remote_reachable(remote: str, *, timeout: int = 10) -> bool:
    """`rclone lsd <remote>:` smoke test."""
    try:
        proc = subprocess.run(
            [rclone_binary(), "lsd", f"{remote}:"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def upload_files(
    stage_dir: Path,
    basenames: list[str],
    remote_path: str,
    *,
    remote: RemoteConfig,
    behaviour: BehaviourConfig,
) -> UploadResult:
    """Run `rclone copy --files-from <list>` and return the basenames that succeeded.

    rclone is all-or-nothing per invocation: rc=0 means *all* listed files are on
    the remote. Anything else means the batch failed and the caller must not
    append any of these names to the ledger.
    """
    if not basenames:
        return UploadResult(succeeded=[], failed=[], rc=0)

    target = f"{remote.name}:{remote_path}"

    with tempfile.NamedTemporaryFile(
        "w", prefix="dji-files-from-", suffix=".txt", delete=False, encoding="utf-8"
    ) as ff:
        ff.write("\n".join(basenames) + "\n")
        files_from_path = ff.name

    cmd = [
        rclone_binary(),
        "copy",
        str(stage_dir),
        target,
        "--files-from",
        files_from_path,
        f"--transfers={behaviour.upload_transfers}",
        f"--retries={behaviour.upload_retries}",
        "--low-level-retries=10",
        "--log-level=INFO",
    ]
    log.info("rclone copy %s → %s (%d file(s))", stage_dir, target, len(basenames))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=behaviour.upload_timeout_sec,
        )
    except FileNotFoundError as exc:
        os.unlink(files_from_path)
        raise UploadError("rclone binary not found on PATH") from exc
    except subprocess.TimeoutExpired:
        os.unlink(files_from_path)
        raise UploadError(
            f"rclone timed out after {behaviour.upload_timeout_sec}s on {target}"
        ) from None
    finally:
        try:
            os.unlink(files_from_path)
        except OSError:
            pass

    if proc.stdout:
        for line in proc.stdout.splitlines():
            log.debug("rclone: %s", line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            log.info("rclone[stderr]: %s", line)

    if proc.returncode == 0:
        return UploadResult(succeeded=list(basenames), failed=[], rc=0)
    return UploadResult(succeeded=[], failed=list(basenames), rc=proc.returncode)
