"""A small, always-current record of the run in progress.

Written to `<log_dir>/last-run.json` and updated as each stage advances and as
bytes upload. Two things read it:

- `dji-auto-upload status` / `watch-run`, so a user can SEE what happened or is
  happening without reading raw logs, and
- the visible progress window on Windows, which tails this file rather than the
  offload process itself — so closing the window cannot touch the transfer.

Writes are atomic (temp file + os.replace) so a reader never sees half a record.
Best-effort throughout: a run must never fail because its status file couldn't
be written.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import AppPaths

log = logging.getLogger(__name__)

STATE_FILENAME = "last-run.json"


@dataclass
class RunState:
    status: str = "running"  # running | done | failed | skipped
    stage: str = ""
    started: str = ""  # ISO8601 UTC, stamped by the caller
    updated: str = ""
    pid: int = 0
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    percent: float = 0.0
    current: str = ""
    message: str = ""
    error: str = ""
    albums: list[str] = field(default_factory=list)


def state_path(paths: AppPaths) -> Path:
    return paths.log_dir / STATE_FILENAME


def write_state(paths: AppPaths, state: RunState) -> None:
    p = state_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:  # never let status-writing break a run
        log.debug("could not write run state: %s", exc)


def read_state(paths: AppPaths) -> RunState | None:
    p = state_path(paths)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    known = {f for f in RunState.__dataclass_fields__}
    return RunState(**{k: v for k, v in data.items() if k in known})
