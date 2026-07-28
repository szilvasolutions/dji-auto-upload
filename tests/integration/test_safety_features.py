"""Integration tests for the safety features added after the security review:

- Partial batch-upload failure → per-file fallback ledgers the successes, so a
  replug never re-uploads confirmed files (no cloud duplicates).
- Drone cleanup deletes ONLY ledger-confirmed files (+ their sidecars), keeps
  everything else, and skips entirely when the drone's clock is untrustworthy.
- --dry-run changes nothing on disk, on the drone, or in the cloud.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from dji_auto_upload.config import (
    BehaviourConfig,
    Config,
    DetectConfig,
    LoggingConfig,
    NotifierConfig,
    RemoteConfig,
    RetentionConfig,
    TelegramCredentials,
)
from dji_auto_upload.errors import OffloadError
from dji_auto_upload.inventory import find_dcim
from dji_auto_upload.ledger import read_ledger
from dji_auto_upload.offload import OffloadRun
from dji_auto_upload.paths import AppPaths


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def send(self, event: str, message: str) -> None:
        self.events.append((event, message))


def _make_config(paths: AppPaths, *, delete_drone: bool = False, drone_days: int = 0) -> Config:
    return Config(
        remote=RemoteConfig(name="testremote", path_template="album/DJI-{date}"),
        retention=RetentionConfig(stage_days=30, drone_days=drone_days),
        detect=DetectConfig(),
        behaviour=BehaviourConfig(
            disk_headroom_mb=0,
            copy_timeout_sec=30,
            upload_timeout_sec=30,
            verify_after_copy=True,
            delete_drone_files=delete_drone,
            eject_when_done=False,  # keep tests hermetic — no diskutil/powershell
        ),
        notifier=NotifierConfig(enabled=False),
        logging=LoggingConfig(),
        telegram=TelegramCredentials(),
        paths=paths,
    )


def _run(cfg: Config, volume: Path, **kwargs: object) -> RecordingNotifier:
    notifier = RecordingNotifier()
    dcim = find_dcim(volume, cfg.detect.dcim_subdirs)
    assert dcim is not None
    OffloadRun(config=cfg, notifier=notifier, volume=volume, dcim=dcim, **kwargs).execute()  # type: ignore[arg-type]
    return notifier


# ---- Partial-upload fallback -------------------------------------------------


def test_partial_batch_failure_ledgers_the_successes(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Batches with >1 file fail; single-file retries succeed except DJI_002.
    monkeypatch.setenv("FAKE_RCLONE_FAIL_MULTI", "1")
    monkeypatch.setenv("FAKE_RCLONE_FAIL_ON_NAME", "DJI_002")

    cfg = _make_config(tmp_app_paths)
    with pytest.raises(OffloadError):
        _run(cfg, synth_volume)

    feb1 = cfg.paths.stage_dir / "2026-02-01"
    # DJI_001 made it through the per-file fallback and MUST be ledgered;
    # DJI_002 failed and must not be.
    assert read_ledger(feb1) == {"DJI_001.MP4"}
    assert (fake_rclone / "testremote" / "album" / "DJI-2026-02-01" / "DJI_001.MP4").is_file()

    # Second run with the flake gone: only the missing files upload.
    monkeypatch.delenv("FAKE_RCLONE_FAIL_MULTI")
    monkeypatch.delenv("FAKE_RCLONE_FAIL_ON_NAME")
    _run(cfg, synth_volume)
    assert read_ledger(feb1) == {"DJI_001.MP4", "DJI_002.JPG"}


# ---- Drone cleanup safety ------------------------------------------------------


def _age(path: Path, days: float) -> None:
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def test_cleanup_deletes_only_uploaded_files_and_their_sidecars(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
) -> None:
    media = synth_volume / "DCIM" / "100MEDIA"
    # Age the shipped media files so they're eligible (recorded 2026-02, but
    # ensure well past drone_days regardless of today's date).
    for f in media.iterdir():
        _age(f, 30)
    # Sidecar of a video that WILL be uploaded → should be deleted with it.
    srt = media / "DJI_001.SRT"
    srt.write_bytes(b"telemetry")
    _age(srt, 30)
    # Orphan sidecar (no matching video anywhere) → must survive.
    orphan = media / "SOLO.SRT"
    orphan.write_bytes(b"telemetry")
    _age(orphan, 30)
    # Unknown extension → must survive.
    other = media / "firmware.bin"
    other.write_bytes(b"blob")
    _age(other, 30)

    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=1)
    notifier = _run(cfg, synth_volume)

    # Uploaded media + paired sidecar: gone.
    assert not (media / "DJI_001.MP4").exists()
    assert not (media / "DJI_002.JPG").exists()
    assert not (media / "DJI_003.MP4").exists()
    assert not srt.exists()
    # Never-uploaded files: untouched.
    assert orphan.exists()
    assert other.exists()

    cleanup_msgs = [m for e, m in notifier.events if e == "cleanup"]
    assert cleanup_msgs and "Kept 2" in cleanup_msgs[-1]


def test_cleanup_skipped_when_drone_clock_is_insane(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
) -> None:
    media = synth_volume / "DCIM" / "100MEDIA"
    for f in media.iterdir():
        _age(f, 30)
    # One file "from the future" — RTC can't be trusted.
    weird = media / "DJI_009.MP4"
    weird.write_bytes(b"x" * 16)
    _age(weird, -30)

    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=1)
    notifier = _run(cfg, synth_volume)

    # Nothing deleted, and the skip was reported.
    assert (media / "DJI_001.MP4").exists()
    cleanup_msgs = [m for e, m in notifier.events if e == "cleanup"]
    assert cleanup_msgs and "clock" in cleanup_msgs[-1].lower()


# ---- Empty drone is not an error -----------------------------------------------


def test_empty_drone_after_cleanup_is_not_a_failure(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
) -> None:
    """Plugging in a drone whose media was already offloaded+cleaned must finish
    quietly, not raise (which would fire a scary ❌ notification every replug)."""
    media = synth_volume / "DCIM" / "100MEDIA"
    for f in media.iterdir():
        _age(f, 30)

    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=1)
    _run(cfg, synth_volume)          # offloads and empties the card
    assert not any(f.suffix.upper() == ".MP4" for f in media.iterdir())

    # Replug: no media left. Must complete without raising.
    notifier = _run(cfg, synth_volume)
    events = [e for e, _ in notifier.events]
    assert "done" in events
    assert "fail" not in events


# ---- Dry run -------------------------------------------------------------------


def test_dry_run_changes_nothing(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    media = synth_volume / "DCIM" / "100MEDIA"
    before = sorted(p.name for p in media.iterdir())

    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=1)
    _run(cfg, synth_volume, dry_run=True)

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "COPY" in out

    # Drone untouched, nothing staged, nothing uploaded.
    assert sorted(p.name for p in media.iterdir()) == before
    assert list(cfg.paths.stage_dir.iterdir()) == []
    assert list(fake_rclone.iterdir()) == []
