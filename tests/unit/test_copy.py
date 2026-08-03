from __future__ import annotations

import os
from pathlib import Path

import pytest

from dji_auto_upload.copy import copy_files, copy_one, needs_copy
from dji_auto_upload.errors import DroneDisconnected
from dji_auto_upload.inventory import FileInfo


def _file(path: Path, content: bytes = b"abcde") -> FileInfo:
    path.write_bytes(content)
    st = path.stat()
    return FileInfo(path=path, size=st.st_size, mtime=st.st_mtime)


def test_needs_copy_when_missing(tmp_path: Path) -> None:
    src = _file(tmp_path / "a.bin")
    assert needs_copy(src, tmp_path / "dest.bin")


def test_needs_copy_when_size_mismatch(tmp_path: Path) -> None:
    src = _file(tmp_path / "a.bin", b"12345")
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"12")  # different size
    assert needs_copy(src, dest)


def test_no_copy_needed_when_size_matches(tmp_path: Path) -> None:
    src = _file(tmp_path / "a.bin", b"12345")
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"12345")
    assert not needs_copy(src, dest)


def test_copy_one_atomic(tmp_path: Path) -> None:
    src = _file(tmp_path / "src.bin", b"hello world")
    dest = tmp_path / "out" / "dest.bin"
    copy_one(src, dest, verify=True, timeout_sec=10)
    assert dest.read_bytes() == b"hello world"
    # No leftover .part file.
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_copy_one_preserves_mtime(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    os.utime(src, (1_700_000_000.0, 1_700_000_000.0))
    fi = FileInfo(path=src, size=src.stat().st_size, mtime=1_700_000_000.0)
    dest = tmp_path / "dest.bin"
    copy_one(fi, dest, verify=True, timeout_sec=10)
    assert int(dest.stat().st_mtime) == 1_700_000_000


def test_copy_one_drone_disconnected_when_source_missing(tmp_path: Path) -> None:
    fake = FileInfo(path=tmp_path / "ghost.bin", size=10, mtime=0.0)
    dest = tmp_path / "dest.bin"
    with pytest.raises(DroneDisconnected):
        copy_one(fake, dest, verify=True, timeout_sec=10)


def test_copy_files_skips_existing(tmp_path: Path) -> None:
    src1 = _file(tmp_path / "a.bin", b"aa")
    src2 = _file(tmp_path / "b.bin", b"bb")
    dest = tmp_path / "stage"
    (dest).mkdir()
    (dest / "a.bin").write_bytes(b"aa")  # already there with right size
    result = copy_files([src1, src2], dest, verify=True, timeout_sec=10)
    assert result.copied == 1
    assert result.skipped == 1


def test_copy_reports_bytes_within_a_single_file(tmp_path: Path) -> None:
    """Drone clips run to several GB. A per-file counter leaves the progress bar
    frozen for a minute or more per clip; byte callbacks keep it moving."""
    from dji_auto_upload.copy import CHUNK_BYTES, copy_files
    from dji_auto_upload.inventory import FileInfo

    src = tmp_path / "BIG.MP4"
    src.write_bytes(b"x" * (CHUNK_BYTES * 5))  # 5 chunks in ONE file
    fi = FileInfo(
        path=src, size=src.stat().st_size, mtime=src.stat().st_mtime, stage_name="BIG.MP4"
    )

    seen: list[int] = []
    copy_files([fi], tmp_path / "stage", verify=True, timeout_sec=30, on_bytes=seen.append)

    assert sum(seen) == fi.size
    assert (tmp_path / "stage" / "BIG.MP4").stat().st_size == fi.size


def test_copy_preserves_mtime_so_date_grouping_still_works(tmp_path: Path) -> None:
    import os

    from dji_auto_upload.copy import copy_files
    from dji_auto_upload.inventory import FileInfo

    src = tmp_path / "DJI_0001.MP4"
    src.write_bytes(b"x" * 4096)
    os.utime(src, (1_700_000_000, 1_700_000_000))
    fi = FileInfo(path=src, size=4096, mtime=1_700_000_000.0, stage_name="DJI_0001.MP4")

    copy_files([fi], tmp_path / "stage", verify=True, timeout_sec=30)
    assert int((tmp_path / "stage" / "DJI_0001.MP4").stat().st_mtime) == 1_700_000_000
