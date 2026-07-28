"""Walk the DCIM directory and group files by recording date.

The recording date is derived from each file's mtime (the drone's RTC is
expected to be in sync — DJI Fly does this). We group on local date because
that matches how an operator thinks ("Tuesday's footage") and how albums are
named.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import DetectConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name


def find_dcim(volume: Path, dcim_subdirs: tuple[str, ...]) -> Path | None:
    """Return the DCIM subfolder that holds the actual media, or None."""
    dcim_root = volume / "DCIM"
    if not dcim_root.is_dir():
        return None

    # Prefer known subdirs in declared order.
    for sub in dcim_subdirs:
        cand = dcim_root / sub
        if cand.is_dir():
            return cand

    # Fallback: first subdirectory under DCIM.
    for child in sorted(dcim_root.iterdir()):
        if child.is_dir():
            return child

    # Last resort: DCIM itself, if it has files directly.
    return dcim_root


def walk_dcim(dcim: Path, extensions: tuple[str, ...]) -> list[FileInfo]:
    exts = {f".{e.lower().lstrip('.')}" for e in extensions}
    out: list[FileInfo] = []
    for f in sorted(dcim.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in exts:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        out.append(FileInfo(path=f, size=st.st_size, mtime=st.st_mtime))
    return out


def group_by_date(files: list[FileInfo]) -> dict[date, list[FileInfo]]:
    """Group files by the local date of their mtime."""
    groups: dict[date, list[FileInfo]] = {}
    for fi in files:
        d = datetime.fromtimestamp(fi.mtime).date()
        groups.setdefault(d, []).append(fi)
    for d in groups:
        groups[d].sort(key=lambda fi: fi.path.name)
    return groups


def inventory(volume: Path, cfg: DetectConfig) -> tuple[Path | None, dict[date, list[FileInfo]]]:
    dcim = find_dcim(volume, cfg.dcim_subdirs)
    if dcim is None:
        return None, {}
    files = walk_dcim(dcim, cfg.extensions)
    log.info("inventory: %d media file(s) under %s", len(files), dcim)
    return dcim, group_by_date(files)
