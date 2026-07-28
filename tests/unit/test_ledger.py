from __future__ import annotations

from pathlib import Path

from dji_auto_upload.ledger import (
    append_to_ledger,
    files_needing_upload,
    has_sentinel,
    ledger_path,
    read_ledger,
)


def _seed_dir(d: Path, names: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"x")


def test_read_ledger_empty(tmp_path: Path) -> None:
    assert read_ledger(tmp_path) == set()
    assert not has_sentinel(tmp_path)


def test_append_creates_ledger(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a.mp4", "b.mp4"])
    append_to_ledger(tmp_path, ["a.mp4"])
    assert read_ledger(tmp_path) == {"a.mp4"}
    assert has_sentinel(tmp_path)


def test_append_is_additive(tmp_path: Path) -> None:
    append_to_ledger(tmp_path, ["a"])
    append_to_ledger(tmp_path, ["b", "c"])
    assert read_ledger(tmp_path) == {"a", "b", "c"}


def test_legacy_zero_byte_sentinel_migrates(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["DJI_001.MP4", "DJI_002.JPG"])
    ledger_path(tmp_path).touch()  # 0-byte legacy sentinel
    names = read_ledger(tmp_path)
    assert names == {"DJI_001.MP4", "DJI_002.JPG"}
    # After migration the file is no longer 0 bytes.
    assert ledger_path(tmp_path).stat().st_size > 0


def test_files_needing_upload_excludes_uploaded_and_hidden(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a.mp4", "b.mp4", "c.mp4"])
    (tmp_path / ".rsync-partial").mkdir()
    append_to_ledger(tmp_path, ["a.mp4"])
    pending = files_needing_upload(tmp_path)
    assert pending == ["b.mp4", "c.mp4"]
