"""Stage prune + drone-side cleanup.

Two retentions, both expressed in days:
- stage_days: how long uploaded local stage dirs are kept. Sentinel-gated:
  a stage dir is only eligible for prune if `.uploaded` exists, every file in
  it is ledgered, and the sentinel's mtime drives the cutoff.
- drone_days: how long files stay on the drone's DCIM folder. Age makes a file
  a *candidate*; it is only deleted if the ledger proves it (or, for sidecars,
  its paired video) was uploaded. 0 disables drone-side deletion entirely.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from .ledger import files_needing_upload, has_sentinel, ledger_path
from .stage import existing_stage_dirs

log = logging.getLogger(__name__)

# Files with mtimes further out than these bounds mean the drone's RTC is
# lying (dead clock battery, factory reset). Age-based deletion must not run
# on untrustworthy timestamps.
CLOCK_FUTURE_SLACK_SEC = 86400          # > 1 day in the future
CLOCK_ANCIENT_CUTOFF_SEC = 10 * 365 * 86400  # > ~10 years old


def dirs_to_prune(stage_base: Path, retention_days: int, *, now: datetime | None = None) -> list[Path]:
    """Return stage dirs whose .uploaded sentinel is older than retention_days.

    A dir with files not yet in its ledger is never eligible, whatever the
    sentinel age — un-uploaded data is never pruned.
    `now` is injected for testability. retention_days <= 0 disables prune.
    """
    if retention_days <= 0:
        return []
    cutoff = (now or datetime.now()).timestamp() - retention_days * 86400
    out: list[Path] = []
    for d in existing_stage_dirs(stage_base):
        if not has_sentinel(d):
            continue
        if files_needing_upload(d):
            log.info("stage dir %s has pending uploads — not pruning", d.name)
            continue
        try:
            mtime = ledger_path(d).stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            out.append(d)
    return out


def prune_stage(stage_base: Path, retention_days: int) -> int:
    """Delete eligible stage dirs. Returns count pruned."""
    if not stage_base.is_dir():
        return 0
    targets = dirs_to_prune(stage_base, retention_days)
    n = 0
    for d in targets:
        try:
            shutil.rmtree(d)
            log.info("pruned stage dir %s", d)
            n += 1
        except OSError as exc:
            log.warning("could not prune %s: %s", d, exc)
    return n


def files_older_than(directory: Path, retention_days: int, *, now: float | None = None) -> list[Path]:
    """Files in `directory` whose mtime is older than retention_days. Top-level only."""
    if retention_days <= 0 or not directory.is_dir():
        return []
    cutoff = (now or time.time()) - retention_days * 86400
    out: list[Path] = []
    for f in directory.iterdir():
        if not f.is_file():
            continue
        try:
            if f.stat().st_mtime < cutoff:
                out.append(f)
        except OSError:
            continue
    return out


def drone_clock_sane(files: list[Path], *, now: float | None = None) -> bool:
    """False if any file's mtime is in the future or absurdly old — the drone's
    RTC can't be trusted, so age-based deletion must be skipped this run."""
    now_ts = now or time.time()
    for f in files:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime > now_ts + CLOCK_FUTURE_SLACK_SEC:
            log.warning("clock sanity: %s has a future mtime — skipping drone cleanup", f.name)
            return False
        if mtime < now_ts - CLOCK_ANCIENT_CUTOFF_SEC:
            log.warning("clock sanity: %s is >10 years old — drone RTC reset? skipping cleanup", f.name)
            return False
    return True


def select_deletable(
    candidates: list[Path],
    uploaded_basenames: set[str],
    sidecar_extensions: tuple[str, ...],
) -> list[Path]:
    """Filter age-eligible drone files down to the provably-safe-to-delete set.

    A file is deletable only if:
    - its basename appears in an .uploaded ledger (media, confirmed on remote), or
    - it is a sidecar (.SRT/.LRF/...) whose paired video stem IS ledgered.
    Anything else — un-uploaded media, unknown extensions — is left alone.
    """
    sidecar_exts = {f".{e.lower().lstrip('.')}" for e in sidecar_extensions}
    uploaded_stems = {Path(n).stem.lower() for n in uploaded_basenames}
    out: list[Path] = []
    for f in candidates:
        if f.name in uploaded_basenames or (f.suffix.lower() in sidecar_exts and f.stem.lower() in uploaded_stems):
            out.append(f)
        else:
            log.info("drone cleanup: keeping %s (not confirmed uploaded)", f.name)
    return out


def delete_files(files: list[Path]) -> int:
    """Delete each path; log failures but don't raise."""
    n = 0
    for f in files:
        try:
            f.unlink()
            n += 1
        except OSError as exc:
            log.warning("could not delete %s: %s", f, exc)
    if n and hasattr(os, "sync"):
        os.sync()  # flush removable-media writes; not available on Windows
    return n
