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
    # The name this file is staged/uploaded under. Normally the basename, but
    # when two source files in the same date group share a basename (e.g.
    # 100MEDIA/DJI_0001.MP4 and 101MEDIA/DJI_0001.MP4 — different files, same
    # name) the later one is disambiguated so it can't overwrite the first in
    # the stage dir. Set by assign_stage_names(); defaults to the basename.
    stage_name: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def staged(self) -> str:
        return self.stage_name or self.path.name


def assign_stage_names(files: list[FileInfo]) -> list[FileInfo]:
    """Give every file a stage_name that is unique within the list.

    Bare basename when it is unique; otherwise `<parent-folder>__<basename>`,
    and if that still clashes, a numeric suffix. This is what stops a second
    DCIM folder's identically-named clip from silently overwriting the first.
    """
    from dataclasses import replace

    taken: set[str] = set()
    out: list[FileInfo] = []
    for fi in files:
        candidate = fi.path.name
        if candidate in taken:
            candidate = f"{fi.path.parent.name}__{fi.path.name}"
            n = 1
            base = candidate
            while candidate in taken:
                stem, dot, ext = base.partition(".")
                candidate = f"{stem}_{n}{dot}{ext}"
                n += 1
        taken.add(candidate)
        out.append(replace(fi, stage_name=candidate))
    return out


def find_dcim(volume: Path, dcim_subdirs: tuple[str, ...]) -> Path | None:
    """Return the primary DCIM subfolder that holds media, or None.

    Kept for callers that want a single representative folder; use
    `find_dcim_dirs` to actually offload, or footage in the later folders is
    silently left behind.
    """
    dirs = find_dcim_dirs(volume, dcim_subdirs)
    return dirs[0] if dirs else None


def find_dcim_dirs(volume: Path, dcim_subdirs: tuple[str, ...]) -> list[Path]:
    """Every DCIM subfolder holding media, preferred names first.

    A camera rolls over to a new folder every 999 files — 100MEDIA, 101MEDIA,
    102MEDIA — and goggles in particular tend to accumulate several. Returning
    only the first would silently never upload the newer footage while the run
    still reported success.
    """
    dcim_root = volume / "DCIM"
    if not dcim_root.is_dir():
        return []

    try:
        children = sorted(c for c in dcim_root.iterdir() if c.is_dir())
    except OSError:
        return []

    # Preferred names first (in declared order), then everything else by name,
    # so the "primary" folder stays predictable for messages and tests.
    preferred = [dcim_root / s for s in dcim_subdirs if (dcim_root / s).is_dir()]
    rest = [c for c in children if c not in preferred]
    ordered = preferred + rest

    with_media = [d for d in ordered if _has_any_file(d)]
    if with_media:
        return with_media
    if ordered:
        return ordered  # empty folders: nothing to copy, but a valid layout
    # Last resort: DCIM itself, if it holds files directly.
    return [dcim_root] if _has_any_file(dcim_root) else []


def _has_any_file(d: Path) -> bool:
    try:
        return any(f.is_file() for f in d.iterdir())
    except OSError:
        return False


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
