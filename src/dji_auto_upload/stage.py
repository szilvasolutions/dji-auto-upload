"""Stage layout: <data>/stage/YYYY-MM-DD/<file>.

Helpers for date-keyed staging dirs. Names are validated as YYYY-MM-DD so we
don't ever consider a hand-edited file like `notes.txt` as a stage dir.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def stage_dir_for(stage_base: Path, d: date) -> Path:
    return stage_base / d.isoformat()


def existing_stage_dirs(stage_base: Path) -> list[Path]:
    if not stage_base.is_dir():
        return []
    out = []
    for child in sorted(stage_base.iterdir()):
        if child.is_dir() and DATE_RE.match(child.name):
            out.append(child)
    return out


def parse_stage_dir(p: Path) -> date | None:
    if not DATE_RE.match(p.name):
        return None
    try:
        return date.fromisoformat(p.name)
    except ValueError:
        return None
