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
