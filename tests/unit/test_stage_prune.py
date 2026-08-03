from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from dji_auto_upload.cleanup import dirs_to_prune, prune_stage
from dji_auto_upload.ledger import append_uploaded


def _make_stage(base: Path, name: str, *, sentinel_age_days: float | None) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "DJI_001.MP4").write_bytes(b"x")
    if sentinel_age_days is not None:
        append_uploaded(d, ["DJI_001.MP4"])
        ts = (datetime.now() - timedelta(days=sentinel_age_days)).timestamp()
        os.utime(d / ".uploaded", (ts, ts))
    return d


def test_dirs_to_prune_respects_retention(tmp_path: Path) -> None:
    fresh = _make_stage(tmp_path, "2026-04-01", sentinel_age_days=1)
    old = _make_stage(tmp_path, "2026-03-01", sentinel_age_days=10)
    no_sentinel = _make_stage(tmp_path, "2026-02-01", sentinel_age_days=None)

    targets = dirs_to_prune(tmp_path, retention_days=7)
    assert old in targets
    assert fresh not in targets
    assert no_sentinel not in targets


def test_zero_retention_disables_prune(tmp_path: Path) -> None:
    _make_stage(tmp_path, "2026-03-01", sentinel_age_days=100)
    assert dirs_to_prune(tmp_path, retention_days=0) == []
    assert prune_stage(tmp_path, retention_days=0) == 0


def test_no_sentinel_means_never_prune(tmp_path: Path) -> None:
    _make_stage(tmp_path, "2026-01-01", sentinel_age_days=None)
    assert dirs_to_prune(tmp_path, retention_days=1) == []
    assert prune_stage(tmp_path, retention_days=1) == 0
    assert (tmp_path / "2026-01-01").is_dir()


def test_prune_actually_deletes(tmp_path: Path) -> None:
    old = _make_stage(tmp_path, "2026-01-01", sentinel_age_days=30)
    n = prune_stage(tmp_path, retention_days=7)
    assert n == 1
    assert not old.exists()


def test_non_date_dirs_are_ignored(tmp_path: Path) -> None:
    junk = tmp_path / "scratch"
    junk.mkdir()
    (junk / ".uploaded").write_text("abc\n")
    ts = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(junk / ".uploaded", (ts, ts))
    assert dirs_to_prune(tmp_path, retention_days=1) == []
