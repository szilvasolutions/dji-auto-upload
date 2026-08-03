"""Safety semantics of drone-side cleanup and stage pruning.

These tests pin the project's core promise: nothing is ever deleted — from the
drone or the local stage — unless the ledger proves it was uploaded.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dji_auto_upload.cleanup import (
    dirs_to_prune,
    drone_clock_sane,
    select_deletable,
)
from dji_auto_upload.ledger import LedgerEntry

SIDECARS = ("srt", "lrf")


def _touch(p: Path, *, age_days: float = 0.0, size: int = 1) -> Path:
    p.write_bytes(b"x" * size)
    ts = time.time() - age_days * 86400
    os.utime(p, (ts, ts))
    return p


def _led(*names: str, size: int = 1) -> list[LedgerEntry]:
    return [LedgerEntry(n, size) for n in names]


# ---- select_deletable ------------------------------------------------------


def test_uploaded_media_is_deletable(tmp_path: Path) -> None:
    f = _touch(tmp_path / "DJI_001.MP4")
    assert select_deletable([f], _led("DJI_001.MP4"), SIDECARS) == [f]


def test_unuploaded_media_is_kept(tmp_path: Path) -> None:
    f = _touch(tmp_path / "DJI_002.MP4")
    assert select_deletable([f], _led("DJI_001.MP4"), SIDECARS) == []


def test_same_name_different_size_is_kept(tmp_path: Path) -> None:
    """The reuse hazard: a drone file whose name is ledgered but whose SIZE is
    different is a different clip and must not be deleted."""
    f = _touch(tmp_path / "DJI_001.MP4", size=2000)
    assert select_deletable([f], _led("DJI_001.MP4", size=30), SIDECARS) == []


def test_collision_folder_qualified_entry_matches_by_folder(tmp_path: Path) -> None:
    """A clip staged as 101MEDIA__DJI_0001.MP4 (basename collision) is deletable
    from its own 101MEDIA folder, matched by the folder-qualified staged name."""
    d = tmp_path / "101MEDIA"
    d.mkdir()
    f = _touch(d / "DJI_0001.MP4", size=2000)
    entries = _led("DJI_0001.MP4", size=1000) + _led("101MEDIA__DJI_0001.MP4", size=2000)
    assert select_deletable([f], entries, SIDECARS) == [f]
    # ...but the 100MEDIA twin (size 1000) is NOT deletable from 101MEDIA.
    assert select_deletable([_touch(d / "OTHER.MP4", size=1000)], entries, SIDECARS) == []


def test_sidecar_of_uploaded_video_is_deletable(tmp_path: Path) -> None:
    srt = _touch(tmp_path / "DJI_001.SRT")
    lrf = _touch(tmp_path / "DJI_001.LRF")
    out = select_deletable([srt, lrf], _led("DJI_001.MP4"), SIDECARS)
    assert set(out) == {srt, lrf}


def test_orphan_sidecar_is_kept(tmp_path: Path) -> None:
    srt = _touch(tmp_path / "SOLO.SRT")
    assert select_deletable([srt], _led("DJI_001.MP4"), SIDECARS) == []


def test_unknown_extension_is_kept(tmp_path: Path) -> None:
    other = _touch(tmp_path / "firmware.bin")
    assert select_deletable([other], _led("DJI_001.MP4", "firmware.mp4"), SIDECARS) == []


def test_empty_ledger_deletes_nothing(tmp_path: Path) -> None:
    files = [_touch(tmp_path / n) for n in ("DJI_001.MP4", "DJI_001.SRT", "x.jpg")]
    assert select_deletable(files, [], SIDECARS) == []


# ---- drone_clock_sane ------------------------------------------------------


def test_clock_sane_for_normal_files(tmp_path: Path) -> None:
    f = _touch(tmp_path / "DJI_001.MP4", age_days=3)
    assert drone_clock_sane([f]) is True


def test_clock_insane_for_future_mtime(tmp_path: Path) -> None:
    f = _touch(tmp_path / "DJI_001.MP4", age_days=-30)  # 30 days in the future
    assert drone_clock_sane([f]) is False


def test_clock_insane_for_ancient_mtime(tmp_path: Path) -> None:
    f = _touch(tmp_path / "DJI_001.MP4", age_days=12 * 365)  # RTC reset territory
    assert drone_clock_sane([f]) is False


# ---- dirs_to_prune pending-upload guard -------------------------------------


def _stage_with_ledger(base: Path, name: str, files: list[str], ledgered: list[str], *, ledger_age_days: float) -> Path:
    d = base / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x")
    ledger = d / ".uploaded"
    ledger.write_text("".join(f"{n}\n" for n in ledgered), encoding="utf-8")
    ts = time.time() - ledger_age_days * 86400
    os.utime(ledger, (ts, ts))
    return d


def test_fully_uploaded_old_stage_is_prunable(tmp_path: Path) -> None:
    d = _stage_with_ledger(
        tmp_path, "2026-01-01", ["a.mp4", "b.jpg"], ["a.mp4", "b.jpg"], ledger_age_days=30
    )
    assert dirs_to_prune(tmp_path, 14) == [d]


def test_stage_with_pending_uploads_is_never_pruned(tmp_path: Path) -> None:
    # b.jpg is on disk but NOT in the ledger — partial upload. Whatever the
    # sentinel age, this dir must survive.
    _stage_with_ledger(
        tmp_path, "2026-01-01", ["a.mp4", "b.jpg"], ["a.mp4"], ledger_age_days=365
    )
    assert dirs_to_prune(tmp_path, 14) == []


def test_eject_does_not_claim_success_when_nothing_was_unmounted() -> None:
    """On Linux an autodetected/automounted card isn't ours to release. Saying
    'ejected, safe to unplug' while it's still mounted risks the user's data."""
    import sys

    from dji_auto_upload import platform_glue

    if sys.platform != "linux":
        return
    assert not platform_glue._MOUNTS_TO_RELEASE  # nothing of ours mounted
    assert platform_glue.eject_volume(Path("/definitely/not/ours")) is False
