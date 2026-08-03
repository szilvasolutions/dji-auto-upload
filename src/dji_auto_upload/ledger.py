"""Per-stage `.uploaded` ledger.

Records *identity*, not just a name: each line is `<staged-name>\t<size>`, so a
file is only considered "already uploaded" when a file of that name AND that
exact byte size is on record. This is what makes it safe to:
  - re-use a filename (two drones, a formatted card): a same-named but
    different-sized file is NOT mistaken for one already in the cloud, and
  - trim the drone: a clip is only deleted once a file matching its identity is
    proven uploaded.

Backward compatibility: a legacy line with no size (the old bare-basename
format, and the 0-byte-sentinel migration) matches any size — old archives are
never re-uploaded.

The ledger file's mtime is also the prune sentinel (see cleanup.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

LEDGER_FILENAME = ".uploaded"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LedgerEntry:
    staged_name: str
    size: int | None  # None = legacy entry, matches any size

    def matches(self, name: str, size: int) -> bool:
        return self.staged_name == name and (self.size is None or self.size == size)


def ledger_path(stage_dir: Path) -> Path:
    return stage_dir / LEDGER_FILENAME


def _parse_line(line: str) -> LedgerEntry | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    name, tab, rest = line.partition("\t")
    if not tab:
        return LedgerEntry(name.strip(), None)  # legacy bare name
    try:
        return LedgerEntry(name, int(rest.split("\t")[0]))
    except ValueError:
        return LedgerEntry(name, None)


def read_entries(stage_dir: Path) -> list[LedgerEntry]:
    p = ledger_path(stage_dir)
    if not p.is_file():
        return []

    if p.stat().st_size == 0:
        # Legacy 0-byte sentinel: treat everything currently staged as uploaded,
        # recording real sizes so future runs get full identity checking.
        entries = [
            LedgerEntry(f.name, f.stat().st_size)
            for f in stage_dir.iterdir()
            if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".part")
        ]
        if entries:
            _write(p, entries)
            log.info("migrated legacy 0-byte sentinel for %s (%d files)", stage_dir.name, len(entries))
        return entries

    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        e = _parse_line(line)
        if e is not None:
            out.append(e)
    return out


def read_ledger(stage_dir: Path) -> set[str]:
    """Set of staged names on record. Presence-only; use read_entries for identity."""
    return {e.staged_name for e in read_entries(stage_dir)}


def _write(p: Path, entries: list[LedgerEntry]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "".join(f"{e.staged_name}\t{e.size if e.size is not None else ''}\n" for e in entries),
        encoding="utf-8",
    )


def append_uploaded(stage_dir: Path, names: list[str]) -> None:
    """Record staged names as uploaded, stamping each with its on-disk size.

    A name whose staged file no longer exists is skipped: never claim a file is
    in the cloud when it isn't even on disk any more (guards the rclone-rc=0
    'file in --files-from vanished from source' case).
    """
    if not names:
        return
    p = ledger_path(stage_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in names:
        f = stage_dir / name
        try:
            size = f.stat().st_size
        except OSError:
            log.warning("not ledgering %s — staged file is gone, cannot confirm upload", name)
            continue
        lines.append(f"{name}\t{size}\n")
    if lines:
        with p.open("a", encoding="utf-8") as fh:
            fh.writelines(lines)


def files_needing_upload(stage_dir: Path) -> list[str]:
    """Staged files not yet confirmed uploaded at their current identity.

    A file is pending unless a ledger entry matches its name AND size. This
    re-queues a file whose name was seen before but whose content differs.
    Hidden files and `.part` copy temporaries (truncated) are excluded.
    """
    entries = read_entries(stage_dir)
    pending = []
    for f in sorted(stage_dir.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name.endswith(".part"):
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if not any(e.matches(f.name, size) for e in entries):
            pending.append(f.name)
    return pending


def has_sentinel(stage_dir: Path) -> bool:
    """True if the ledger file exists (whatever its content)."""
    return ledger_path(stage_dir).is_file()
