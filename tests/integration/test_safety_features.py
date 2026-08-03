"""Integration tests for the safety features added after the security review:

- Partial batch-upload failure → per-file fallback ledgers the successes, so a
  replug never re-uploads confirmed files (no cloud duplicates).
- Drone cleanup deletes ONLY ledger-confirmed files (+ their sidecars), keeps
  everything else, and skips entirely when the drone's clock is untrustworthy.
- --dry-run changes nothing on disk, on the drone, or in the cloud.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from dji_auto_upload import offload
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
from dji_auto_upload.inventory import find_dcim_dirs
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
    # Mirror the CLI: pass every media folder, not just the first.
    dcim_dirs = find_dcim_dirs(volume, cfg.detect.dcim_subdirs)
    assert dcim_dirs
    OffloadRun(
        config=cfg, notifier=notifier, volume=volume,
        dcim=dcim_dirs[0], dcim_dirs=dcim_dirs, **kwargs,  # type: ignore[arg-type]
    ).execute()
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


# ---- Sleep inhibition --------------------------------------------------------


def test_run_holds_a_sleep_inhibitor_for_the_whole_pipeline(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A laptop that idle-sleeps mid-upload freezes the transfer, so the run
    must hold an inhibitor from first byte to last and release it after."""
    events: list[str] = []

    @contextlib.contextmanager
    def spy(*, enabled: bool = True, reason: str = "") -> Iterator[str | None]:
        events.append(f"acquire(enabled={enabled})")
        try:
            yield "fake"
        finally:
            events.append("release")

    monkeypatch.setattr(offload, "inhibit_sleep", spy)

    cfg = _make_config(tmp_app_paths)
    notifier = _run(cfg, synth_volume)

    assert events == ["acquire(enabled=True)", "release"]
    # Released only after the run actually finished.
    assert any(e == "done" for e, _ in notifier.events)


def test_inhibitor_is_released_when_the_run_fails(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def spy(*, enabled: bool = True, reason: str = "") -> Iterator[str | None]:
        events.append("acquire")
        try:
            yield "fake"
        finally:
            events.append("release")

    monkeypatch.setattr(offload, "inhibit_sleep", spy)
    monkeypatch.setenv("FAKE_RCLONE_FAIL_ON_DATE", "2026-")  # every album fails

    cfg = _make_config(tmp_app_paths)
    with pytest.raises(OffloadError):
        _run(cfg, synth_volume)

    # Must not leave the machine pinned awake forever after a failure.
    assert events == ["acquire", "release"]


def test_inhibit_sleep_can_be_switched_off(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    @contextlib.contextmanager
    def spy(*, enabled: bool = True, reason: str = "") -> Iterator[str | None]:
        seen.append(enabled)
        yield None

    monkeypatch.setattr(offload, "inhibit_sleep", spy)

    cfg = _make_config(tmp_app_paths)
    cfg = replace(cfg, behaviour=replace(cfg.behaviour, inhibit_sleep=False))
    _run(cfg, synth_volume)

    assert seen == [False]


# ---- Multi-DCIM basename collision (footage-loss regression) ------------------


def test_same_basename_in_two_dcim_folders_both_upload_and_trim_safely(
    tmp_app_paths: AppPaths, synth_volume: Path, fake_rclone: Path,
) -> None:
    """Two DCIM folders can hold different clips that share a basename. Before
    the identity-ledger fix the second overwrote the first in staging, the flat
    ledger marked both 'uploaded', and drone cleanup erased the never-uploaded
    one. This asserts both distinct files reach the cloud and both are trimmed."""
    import os

    dcim = synth_volume / "DCIM"
    # Second rollover folder with a clip whose basename collides with 100MEDIA's.
    second = dcim / "101MEDIA"
    second.mkdir()
    collide = second / "DJI_001.MP4"          # same name as 100MEDIA/DJI_001.MP4
    collide.write_bytes(b"Z" * 4096)          # different content + size
    os.utime(collide, os.stat(dcim / "100MEDIA" / "DJI_001.MP4")[-2:])  # same date

    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=0)
    # drone_days=0 disables cleanup; enable via a tiny age by shifting mtimes back
    cfg = _make_config(tmp_app_paths, delete_drone=True, drone_days=1)
    for f in list((dcim / "100MEDIA").iterdir()) + list(second.iterdir()):
        old = time.time() - 5 * 86400
        os.utime(f, (old, old))

    _run(cfg, synth_volume)

    # Both distinct clips are in the cloud under distinct names.
    cloud = fake_rclone / "testremote"
    uploaded = {p.name: p.stat().st_size for p in cloud.rglob("*") if p.is_file()}
    assert 16 in uploaded.values()            # the original 100MEDIA/DJI_001.MP4
    assert 4096 in uploaded.values()          # the colliding 101MEDIA clip, NOT lost
    # Two files named-from DJI_001 exist (one qualified) — nothing was overwritten.
    dji001_like = [n for n in uploaded if n.endswith("DJI_001.MP4")]
    assert len(dji001_like) == 2, uploaded

    # Both drone copies were confirmed-uploaded and removed; none left behind wrongly.
    assert not (dcim / "100MEDIA" / "DJI_001.MP4").exists()
    assert not (second / "DJI_001.MP4").exists()
