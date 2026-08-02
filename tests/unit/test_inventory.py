from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from dji_auto_upload.config import DetectConfig
from dji_auto_upload.inventory import (
    FileInfo,
    find_dcim,
    group_by_date,
    inventory,
    walk_dcim,
)


def _touch(path: Path, mtime_ts: float, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime_ts, mtime_ts))


def test_find_dcim_prefers_known_subdirs(tmp_path: Path) -> None:
    (tmp_path / "DCIM" / "DJI_001").mkdir(parents=True)
    (tmp_path / "DCIM" / "999OTHER").mkdir(parents=True)
    found = find_dcim(tmp_path, ("100MEDIA", "DJI_001"))
    assert found == tmp_path / "DCIM" / "DJI_001"


def test_find_dcim_falls_back_to_first_subdir(tmp_path: Path) -> None:
    (tmp_path / "DCIM" / "777STRANGE").mkdir(parents=True)
    found = find_dcim(tmp_path, ("100MEDIA", "DJI_001"))
    assert found == tmp_path / "DCIM" / "777STRANGE"


def test_find_dcim_returns_none_when_no_dcim(tmp_path: Path) -> None:
    assert find_dcim(tmp_path, ("100MEDIA",)) is None


def test_walk_dcim_filters_extensions(tmp_path: Path) -> None:
    d = tmp_path / "media"
    d.mkdir()
    (d / "clip.MP4").write_bytes(b"a")
    (d / "shot.jpg").write_bytes(b"b")
    (d / "notes.txt").write_bytes(b"c")
    (d / "subdir").mkdir()
    files = walk_dcim(d, ("mp4", "jpg"))
    names = sorted(f.path.name for f in files)
    assert names == ["clip.MP4", "shot.jpg"]


def test_group_by_date_groups_correctly() -> None:
    feb1 = datetime(2026, 2, 1, 12, 0, 0).timestamp()
    feb2 = datetime(2026, 2, 2, 8, 0, 0).timestamp()
    files = [
        FileInfo(path=Path("a"), size=10, mtime=feb1),
        FileInfo(path=Path("b"), size=20, mtime=feb1),
        FileInfo(path=Path("c"), size=30, mtime=feb2),
    ]
    out = group_by_date(files)
    assert set(out.keys()) == {date(2026, 2, 1), date(2026, 2, 2)}
    assert len(out[date(2026, 2, 1)]) == 2
    assert len(out[date(2026, 2, 2)]) == 1


def test_group_by_date_empty() -> None:
    assert group_by_date([]) == {}


def test_inventory_end_to_end(tmp_path: Path) -> None:
    media = tmp_path / "DCIM" / "100MEDIA"
    media.mkdir(parents=True)
    feb1 = datetime(2026, 2, 1, 12, 0, 0).timestamp()
    _touch(media / "DJI_0001.MP4", feb1)
    _touch(media / "DJI_0002.JPG", feb1)
    cfg = DetectConfig()
    dcim, groups = inventory(tmp_path, cfg)
    assert dcim == media
    assert list(groups.keys()) == [date(2026, 2, 1)]
    assert len(groups[date(2026, 2, 1)]) == 2


def test_every_media_folder_is_found_not_just_the_first(tmp_path: Path) -> None:
    """A camera rolls over to a new folder every 999 files. Returning only the
    first would silently never upload the newer clips while the run still
    reported success."""
    from dji_auto_upload.inventory import find_dcim_dirs

    for sub in ("100MEDIA", "101MEDIA", "102MEDIA"):
        d = tmp_path / "DCIM" / sub
        d.mkdir(parents=True)
        (d / "DJI_0001.MP4").write_bytes(b"x")

    found = find_dcim_dirs(tmp_path, ("100MEDIA", "DJI_001"))
    assert [p.name for p in found] == ["100MEDIA", "101MEDIA", "102MEDIA"]


def test_preferred_folder_is_listed_first(tmp_path: Path) -> None:
    for sub in ("999OTHER", "DJI_001"):
        d = tmp_path / "DCIM" / sub
        d.mkdir(parents=True)
        (d / "DJI_0001.MP4").write_bytes(b"x")

    from dji_auto_upload.inventory import find_dcim_dirs

    found = find_dcim_dirs(tmp_path, ("100MEDIA", "DJI_001"))
    assert found[0].name == "DJI_001"
    assert {p.name for p in found} == {"DJI_001", "999OTHER"}


def test_no_dcim_at_all_returns_nothing(tmp_path: Path) -> None:
    from dji_auto_upload.inventory import find_dcim_dirs

    assert find_dcim_dirs(tmp_path, ("100MEDIA",)) == []
