"""Stage prune + drone-side cleanup.

Two retentions, both expressed in days:
- stage_days: how long uploaded local stage dirs are kept. Sentinel-gated:
  a stage dir is only eligible for prune if `.uploaded` exists; the sentinel's
  mtime drives the cutoff.
- drone_days: how long files stay on the drone's DCIM folder. Driven by the
  files' own mtimes. 0 disables drone-side deletion entirely.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from .ledger import has_sentinel, ledger_path
from .stage import existing_stage_dirs

log = logging.getLogger(__name__)


def dirs_to_prune(stage_base: Path, retention_days: int, *, now: datetime | None = None) -> list[Path]:
    """Return stage dirs whose .uploaded sentinel is older than retention_days.

    `now` is injected for testability. retention_days <= 0 disables prune.
    """
    if retention_days <= 0:
        return []
    cutoff = (now or datetime.now()).timestamp() - retention_days * 86400
    out: list[Path] = []
    for d in existing_stage_dirs(stage_base):
        if not has_sentinel(d):
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


def delete_files(files: list[Path]) -> int:
    """Delete each path; log failures but don't raise."""
    n = 0
    for f in files:
        try:
            f.unlink()
            n += 1
        except OSError as exc:
            log.warning("could not delete %s: %s", f, exc)
    if n:
        try:
            os.sync()  # type: ignore[attr-defined]
        except AttributeError:
            pass  # Windows
    return n
