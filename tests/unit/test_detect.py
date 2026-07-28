from __future__ import annotations

from pathlib import Path

from dji_auto_upload.config import DetectConfig
from dji_auto_upload.detect import VolumeInfo, has_dji_dcim, is_dji_volume


def test_has_dji_dcim_true_when_known_subdir(tmp_path: Path) -> None:
    (tmp_path / "DCIM" / "100MEDIA").mkdir(parents=True)
    assert has_dji_dcim(tmp_path, ("100MEDIA", "DJI_001"))


def test_has_dji_dcim_true_when_any_subdir(tmp_path: Path) -> None:
    (tmp_path / "DCIM" / "777OTHER").mkdir(parents=True)
    assert has_dji_dcim(tmp_path, ("100MEDIA",))


def test_has_dji_dcim_false_when_no_subdirs(tmp_path: Path) -> None:
    (tmp_path / "DCIM").mkdir()
    assert not has_dji_dcim(tmp_path, ("100MEDIA",))


def test_has_dji_dcim_false_when_no_dcim(tmp_path: Path) -> None:
    assert not has_dji_dcim(tmp_path, ("100MEDIA",))


def test_is_dji_by_vendor_id(tmp_path: Path) -> None:
    cfg = DetectConfig()
    vol = VolumeInfo(mountpoint=tmp_path, label=None, vendor_id="2ca3")
    assert is_dji_volume(vol, cfg)


def test_is_dji_by_label(tmp_path: Path) -> None:
    cfg = DetectConfig()
    vol = VolumeInfo(mountpoint=tmp_path, label="DJIMEDIA", vendor_id=None)
    assert is_dji_volume(vol, cfg)


def test_is_dji_by_dcim(tmp_path: Path) -> None:
    cfg = DetectConfig()
    (tmp_path / "DCIM" / "100MEDIA").mkdir(parents=True)
    vol = VolumeInfo(mountpoint=tmp_path, label="UntitledSDCard", vendor_id="0000")
    assert is_dji_volume(vol, cfg)


def test_not_dji_when_no_signal(tmp_path: Path) -> None:
    cfg = DetectConfig()
    vol = VolumeInfo(mountpoint=tmp_path, label="Photos", vendor_id="0000")
    assert not is_dji_volume(vol, cfg)
