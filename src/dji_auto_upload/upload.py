"""rclone subprocess wrapper.

Uses `rclone copy --files-from` so already-uploaded files (per the .uploaded
ledger) are never sent again. Critical for the Google Photos backend, which
can't dedupe server-side — every duplicate upload creates a duplicate in the
album.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
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
    # rclone's own explanation of the failure. Without this the user only ever
    # saw "exit code 1" and had to dig through a log to find e.g. an expired
    # token — the one line that actually tells them what to do.
    reason: str = ""


@dataclass(frozen=True)
class UploadStats:
    """A point-in-time progress reading parsed from rclone's stats output."""

    percent: float
    transferred: str = ""
    total: str = ""
    speed: str = ""
    eta: str = ""

    def human(self) -> str:
        bits = []
        if self.transferred and self.total:
            bits.append(f"{self.transferred} / {self.total}")
        if self.speed:
            bits.append(self.speed)
        if self.eta:
            bits.append(f"ETA {self.eta}")
        return "  ".join(bits)


# Matches both rclone's multi-line "Transferred: 3.164 GiB / 7.920 GiB, 40%, …"
# and the compact --stats-one-line form, which omits the leading label.
_STATS_RE = re.compile(
    r"([\d.]+\s*[KMGTP]?i?B)\s*/\s*([\d.]+\s*[KMGTP]?i?B)\s*,\s*(\d+)\s*%"
    r"(?:\s*,\s*([\d.]+\s*[KMGTP]?i?B/s))?"
    r"(?:\s*,\s*ETA\s*(\S+))?"
)


def parse_stats(line: str) -> UploadStats | None:
    """Extract a progress reading from one rclone output line, or None.

    Deliberately ignores the per-file "Transferred: 2 / 3, 67%" counter — the
    byte-based figure is what makes a progress bar move smoothly during a
    multi-gigabyte transfer.
    """
    m = _STATS_RE.search(line)
    if not m:
        return None
    try:
        pct = float(m.group(3))
    except (TypeError, ValueError):
        return None
    return UploadStats(
        percent=max(0.0, min(100.0, pct)),
        transferred=(m.group(1) or "").strip(),
        total=(m.group(2) or "").strip(),
        speed=(m.group(4) or "").strip(),
        eta=(m.group(5) or "").strip(),
    )


def _explain(stderr: str) -> str:
    """Pull rclone's actual complaint out of its stderr, for the user to read.

    Prefers the well-known causes we can give advice for, then falls back to the
    first CRITICAL/ERROR line. A NOTICE about the shared client_id is chatter,
    not a failure, so it is never chosen.
    """
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    interesting = [
        ln for ln in lines
        if ("CRITICAL" in ln or "ERROR" in ln) and "NOTICE" not in ln
    ]
    blob = " ".join(interesting)

    if "invalid_grant" in blob or "token expired" in blob or "couldn't fetch token" in blob:
        return (
            "the cloud connection has expired — reauthorise rclone with: "
            "rclone config reconnect <remote>:"
        )
    if "quota" in blob.lower() or "429" in blob:
        return "the cloud rejected the upload for exceeding a quota — try again later"
    if "no such host" in blob.lower() or "dial tcp" in blob.lower():
        return "could not reach the cloud — check the network connection"
    if "directory not found" in blob.lower() or "couldn't find root directory" in blob:
        return "rclone could not open the destination folder on the remote"
    if interesting:
        # Strip rclone's timestamp/level prefix for readability.
        first = interesting[0]
        for marker in ("CRITICAL:", "ERROR :", "ERROR:"):
            if marker in first:
                first = first.split(marker, 1)[1].strip()
                break
        return first[:300]
    return ""


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


def remote_configured(remote: str) -> bool:
    """Is this remote defined in rclone.conf? Local config read, no API call.

    This is the deterministic half of the check: if the name isn't here, the
    user really has mistyped it or never ran `rclone config`.
    """
    remotes = list_remotes()
    # An empty list means `rclone listremotes` itself failed; don't claim the
    # remote is missing on the strength of a failed probe.
    return not remotes or remote in remotes


def remote_reachable(remote: str, *, timeout: int = 30) -> bool:
    """`rclone lsd <remote>:` smoke test.

    Only a liveness hint, never a verdict. A cold cloud remote can easily need
    more than a few seconds (OAuth refresh plus a root listing), so the timeout
    is generous and callers must treat False as "unproven", not "broken".
    """
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
    on_progress: Callable[[UploadStats], None] | None = None,
) -> UploadResult:
    """Run `rclone copy --files-from <list>` and return the basenames that succeeded.

    rc=0 means the batch completed without error; anything else means it failed
    and the caller must not ledger any of these names. (rc=0 does not by itself
    prove every listed file was present — a source that vanished is skipped
    silently — so `append_uploaded` re-checks each file still exists before
    recording it.)
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
        # Compact, frequent progress. Read live below so the progress window can
        # actually move; the previous capture_output=True buffered every line
        # until rclone exited, so a six-minute upload showed 0% throughout.
        "--stats=2s",
        "--stats-one-line",
    ]
    log.info("rclone copy %s → %s (%d file(s))", stage_dir, target, len(basenames))

    stderr_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line buffered: we need each stats line as it happens
        )
    except FileNotFoundError as exc:
        os.unlink(files_from_path)
        raise UploadError("rclone binary not found on PATH") from exc

    # Inactivity timeout, not wall-clock: rclone reports every couple of seconds
    # while it is working, so this fires only when it genuinely wedges. A
    # wall-clock limit would kill a large card on a slow uplink mid-transfer and
    # report it as a failure, which is the opposite of what the user wants.
    deadline = time.monotonic() + behaviour.upload_timeout_sec
    timed_out = False
    try:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.rstrip("\n")
            if line:
                stderr_lines.append(line)
                log.info("rclone: %s", line)
                deadline = time.monotonic() + behaviour.upload_timeout_sec
                stats = parse_stats(line)
                if stats is not None and on_progress is not None:
                    on_progress(stats)
            if time.monotonic() > deadline:
                timed_out = True
                proc.kill()
                break
        rc = proc.wait()
    finally:
        try:
            os.unlink(files_from_path)
        except OSError:
            pass

    if timed_out:
        raise UploadError(
            f"rclone stopped responding for {behaviour.upload_timeout_sec}s on {target}"
        )

    stderr_text = "\n".join(stderr_lines)
    if rc == 0:
        return UploadResult(succeeded=list(basenames), failed=[], rc=0)

    proc_returncode = rc
    reason = _explain(stderr_text)
    if len(basenames) == 1:
        return UploadResult(
            succeeded=[], failed=list(basenames), rc=proc_returncode, reason=reason
        )

    # Batch failed. rclone gives us no reliable per-file verdict, so retry each
    # file on its own: successes get ledgered, so a flaky batch can never cause
    # the whole album to re-upload (Google Photos can't dedupe server-side —
    # every duplicate upload becomes a duplicate photo).
    log.warning(
        "batch upload to %s failed (rc=%d) — retrying %d file(s) individually",
        target, proc_returncode, len(basenames),
    )
    succeeded: list[str] = []
    failed: list[str] = []
    last_reason = reason
    for name in basenames:
        single = upload_files(
            stage_dir, [name], remote_path, remote=remote, behaviour=behaviour,
            on_progress=on_progress,
        )
        if single.rc == 0:
            succeeded.append(name)
        else:
            failed.append(name)
            last_reason = single.reason or last_reason
    return UploadResult(
        succeeded=succeeded,
        failed=failed,
        rc=0 if not failed else 1,
        reason="" if not failed else last_reason,
    )
