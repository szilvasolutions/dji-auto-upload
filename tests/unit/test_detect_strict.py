"""strict_detect: with the flag on, only vendor-ID / label matches trigger —
a bare DCIM folder (any camera card) must not."""

from __future__ import annotations

from pathlib import Path

from dji_auto_upload.config import DetectConfig
from dji_auto_upload.detect import VolumeInfo, is_dji_volume


def _volume_with_dcim(tmp_path: Path) -> Path:
    (tmp_path / "DCIM" / "100GOPRO").mkdir(parents=True)
    return tmp_path


def test_lenient_mode_accepts_any_dcim(tmp_path: Path) -> None:
    vol = VolumeInfo(mountpoint=_volume_with_dcim(tmp_path), label="NO NAME")
    assert is_dji_volume(vol, DetectConfig(strict_detect=False)) is True


def test_strict_mode_rejects_bare_dcim(tmp_path: Path) -> None:
    vol = VolumeInfo(mountpoint=_volume_with_dcim(tmp_path), label="NO NAME")
    assert is_dji_volume(vol, DetectConfig(strict_detect=True)) is False


def test_strict_mode_still_accepts_label(tmp_path: Path) -> None:
    vol = VolumeInfo(mountpoint=tmp_path, label="DJIMEDIA")
    assert is_dji_volume(vol, DetectConfig(strict_detect=True)) is True


def test_strict_mode_still_accepts_vendor_id(tmp_path: Path) -> None:
    vol = VolumeInfo(mountpoint=tmp_path, vendor_id="2CA3")
    assert is_dji_volume(vol, DetectConfig(strict_detect=True)) is True
