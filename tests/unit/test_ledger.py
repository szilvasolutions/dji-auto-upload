from __future__ import annotations

from pathlib import Path

from dji_auto_upload.ledger import (
    append_uploaded,
    files_needing_upload,
    has_sentinel,
    ledger_path,
    read_entries,
    read_ledger,
)


def _seed_dir(d: Path, names: list[str], size: int = 1) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"x" * size)


def test_read_ledger_empty(tmp_path: Path) -> None:
    assert read_ledger(tmp_path) == set()
    assert not has_sentinel(tmp_path)


def test_append_creates_ledger(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a.mp4", "b.mp4"])
    append_uploaded(tmp_path, ["a.mp4"])
    assert read_ledger(tmp_path) == {"a.mp4"}
    assert has_sentinel(tmp_path)


def test_append_is_additive(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a", "b", "c"])
    append_uploaded(tmp_path, ["a"])
    append_uploaded(tmp_path, ["b", "c"])
    assert read_ledger(tmp_path) == {"a", "b", "c"}


def test_append_records_size_for_identity(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a.mp4"], size=1234)
    append_uploaded(tmp_path, ["a.mp4"])
    (entry,) = read_entries(tmp_path)
    assert entry.staged_name == "a.mp4"
    assert entry.size == 1234


def test_legacy_zero_byte_sentinel_migrates(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["DJI_001.MP4", "DJI_002.JPG"])
    ledger_path(tmp_path).touch()  # 0-byte legacy sentinel
    names = read_ledger(tmp_path)
    assert names == {"DJI_001.MP4", "DJI_002.JPG"}
    assert ledger_path(tmp_path).stat().st_size > 0


def test_legacy_bare_name_line_matches_any_size(tmp_path: Path) -> None:
    """Old ledgers had no size; they must still count as uploaded so existing
    archives are never re-sent."""
    (tmp_path / "a.mp4").write_bytes(b"x" * 999)
    ledger_path(tmp_path).write_text("a.mp4\n", encoding="utf-8")  # legacy format
    assert files_needing_upload(tmp_path) == []


def test_files_needing_upload_excludes_uploaded_and_hidden(tmp_path: Path) -> None:
    _seed_dir(tmp_path, ["a.mp4", "b.mp4", "c.mp4"])
    (tmp_path / ".rsync-partial").mkdir()
    append_uploaded(tmp_path, ["a.mp4"])
    assert files_needing_upload(tmp_path) == ["b.mp4", "c.mp4"]


def test_reused_name_with_different_content_is_requeued(tmp_path: Path) -> None:
    """Two drones, or a formatted card: a same-named but different-sized file must
    NOT be mistaken for one already uploaded."""
    (tmp_path / "DJI_0001.MP4").write_bytes(b"x" * 30)
    append_uploaded(tmp_path, ["DJI_0001.MP4"])
    assert files_needing_upload(tmp_path) == []
    # Same name, new content.
    (tmp_path / "DJI_0001.MP4").write_bytes(b"y" * 1000)
    assert files_needing_upload(tmp_path) == ["DJI_0001.MP4"]


def test_vanished_file_is_never_ledgered(tmp_path: Path) -> None:
    """rclone copy --files-from silently skips a source that disappeared and
    still exits 0; we must not then record it as uploaded."""
    append_uploaded(tmp_path, ["GHOST.MP4"])  # never existed on disk
    assert read_ledger(tmp_path) == set()


def test_part_files_are_never_uploaded(tmp_path: Path) -> None:
    (tmp_path / "DJI_001.MP4").write_bytes(b"complete")
    (tmp_path / "DJI_002.MP4.part").write_bytes(b"trunc")
    assert files_needing_upload(tmp_path) == ["DJI_001.MP4"]
