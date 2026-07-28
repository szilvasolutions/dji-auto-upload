"""Per-stage `.uploaded` ledger.

Format: one basename per line. Mtime of the file is the prune sentinel.
Migration: a legacy 0-byte sentinel (from the bash version) is treated as
"everything currently in this directory was uploaded" and populated on first
read. This is the same migration the bash script does — keeping it ensures a
user can swap implementations without re-uploading their archive.
"""

from __future__ import annotations

import logging
from pathlib import Path

LEDGER_FILENAME = ".uploaded"
log = logging.getLogger(__name__)


def ledger_path(stage_dir: Path) -> Path:
    return stage_dir / LEDGER_FILENAME


def read_ledger(stage_dir: Path) -> set[str]:
    p = ledger_path(stage_dir)
    if not p.is_file():
        return set()

    if p.stat().st_size == 0:
        # Legacy 0-byte sentinel migration.
        names = [
            f.name
            for f in stage_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ]
        if names:
            p.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
            log.info("migrated legacy 0-byte sentinel for %s (%d files)", stage_dir.name, len(names))
        return set(names)

    return {
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_to_ledger(stage_dir: Path, basenames: list[str]) -> None:
    """Append uploaded basenames to the ledger and bump its mtime."""
    if not basenames:
        return
    p = ledger_path(stage_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for name in basenames:
            f.write(name + "\n")


def files_needing_upload(stage_dir: Path) -> list[str]:
    """Disk basenames that aren't already in the ledger. Excludes hidden files."""
    uploaded = read_ledger(stage_dir)
    pending = []
    for f in sorted(stage_dir.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.name not in uploaded:
            pending.append(f.name)
    return pending


def has_sentinel(stage_dir: Path) -> bool:
    """True if the ledger file exists (whatever its content)."""
    return ledger_path(stage_dir).is_file()
