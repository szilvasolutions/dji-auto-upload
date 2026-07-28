"""End-to-end test of the offload pipeline using a fake rclone binary.

Builds a synthetic DCIM tree, points the orchestrator at a temp app-paths,
sets RCLONE_BIN to our fake binary, runs the pipeline, and asserts that:
- Files land in the staging dir, grouped by mtime date.
- `.uploaded` ledgers are populated *after* (fake) upload returns 0.
- The `copy` notifier event fires before `done_copy` before `done_upload`.
- Already-uploaded files are skipped on a second pass.
"""

from __future__ import annotations

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
from dji_auto_upload.inventory import find_dcim
from dji_auto_upload.ledger import read_ledger
from dji_auto_upload.offload import OffloadRun
from dji_auto_upload.paths import AppPaths


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def send(self, event: str, message: str) -> None:
        self.events.append((event, message))


def _make_config(paths: AppPaths) -> Config:
    return Config(
        remote=RemoteConfig(name="testremote", path_template="album/DJI-{date}"),
        retention=RetentionConfig(stage_days=30, drone_days=0),
        detect=DetectConfig(),
        behaviour=BehaviourConfig(
            disk_headroom_mb=0,
            copy_timeout_sec=30,
            upload_timeout_sec=30,
            verify_after_copy=True,
            delete_drone_files=False,
            eject_when_done=False,  # keep tests hermetic — no diskutil/powershell
        ),
        notifier=NotifierConfig(enabled=False),
        logging=LoggingConfig(),
        telegram=TelegramCredentials(),
        paths=paths,
    )


def test_pipeline_copies_uploads_and_writes_ledgers(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path
) -> None:
    cfg = _make_config(tmp_app_paths)
    notifier = RecordingNotifier()
    dcim = find_dcim(synth_volume, cfg.detect.dcim_subdirs)
    assert dcim is not None

    OffloadRun(config=cfg, notifier=notifier, volume=synth_volume, dcim=dcim).execute()

    # Stage dirs created per recording date.
    stage_base = cfg.paths.stage_dir
    feb1_dir = stage_base / "2026-02-01"
    feb2_dir = stage_base / "2026-02-02"
    assert feb1_dir.is_dir()
    assert feb2_dir.is_dir()
    assert (feb1_dir / "DJI_001.MP4").is_file()
    assert (feb1_dir / "DJI_002.JPG").is_file()
    assert (feb2_dir / "DJI_003.MP4").is_file()

    # Ledgers updated after upload.
    assert read_ledger(feb1_dir) == {"DJI_001.MP4", "DJI_002.JPG"}
    assert read_ledger(feb2_dir) == {"DJI_003.MP4"}

    # Files made it to the fake cloud.
    cloud_root = fake_rclone / "testremote" / "album"
    assert (cloud_root / "DJI-2026-02-01" / "DJI_001.MP4").is_file()
    assert (cloud_root / "DJI-2026-02-02" / "DJI_003.MP4").is_file()

    # Notifier event ordering.
    events = [e for e, _ in notifier.events]
    assert "start" in events
    assert "copy" in events
    assert "done_copy" in events
    assert "upload" in events
    assert "done_upload" in events
    assert "done" in events
    # Copy must precede upload.
    assert events.index("done_copy") < events.index("upload")


def test_second_pass_skips_uploaded_files(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path
) -> None:
    cfg = _make_config(tmp_app_paths)
    dcim = find_dcim(synth_volume, cfg.detect.dcim_subdirs)
    OffloadRun(config=cfg, notifier=RecordingNotifier(), volume=synth_volume, dcim=dcim).execute()

    notifier = RecordingNotifier()
    OffloadRun(config=cfg, notifier=notifier, volume=synth_volume, dcim=dcim).execute()

    events = [e for e, _ in notifier.events]
    # No "upload" event because nothing pending; should fire "info" + "done".
    assert "upload" not in events
    assert "info" in events
    assert "done" in events


def test_upload_failure_keeps_stage_and_omits_ledger(
    tmp_app_paths: AppPaths,
    synth_volume: Path,
    fake_rclone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_RCLONE_FAIL_ON_DATE", "2026-02-02")
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "1")

    cfg = _make_config(tmp_app_paths)
    notifier = RecordingNotifier()
    dcim = find_dcim(synth_volume, cfg.detect.dcim_subdirs)

    from dji_auto_upload.errors import OffloadError

    with pytest.raises(OffloadError):
        OffloadRun(config=cfg, notifier=notifier, volume=synth_volume, dcim=dcim).execute()

    feb1_dir = cfg.paths.stage_dir / "2026-02-01"
    feb2_dir = cfg.paths.stage_dir / "2026-02-02"
    # Feb 1 succeeded → ledger written.
    assert read_ledger(feb1_dir) == {"DJI_001.MP4", "DJI_002.JPG"}
    # Feb 2 failed → no ledger entry.
    assert read_ledger(feb2_dir) == set()
    # Stage dirs retained for both.
    assert feb1_dir.is_dir()
    assert feb2_dir.is_dir()
