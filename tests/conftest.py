"""Shared pytest fixtures.

`tmp_app_paths` gives every test an isolated AppPaths so the test suite can run
on a developer machine without overwriting their real config.

`fake_rclone` + `synth_volume` back the integration tests: a stand-in rclone
binary (works on POSIX and Windows) and a synthetic DJI DCIM tree.
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime
from pathlib import Path

import pytest

from dji_auto_upload.paths import AppPaths

FAKE_RCLONE = Path(__file__).parent / "fixtures" / "fake_rclone.py"


@pytest.fixture
def tmp_app_paths(tmp_path: Path) -> AppPaths:
    config = tmp_path / "config"
    data = tmp_path / "data"
    log = tmp_path / "log"
    runtime = tmp_path / "runtime"
    for d in (config, data, log, runtime, data / "stage"):
        d.mkdir(parents=True, exist_ok=True)
    return AppPaths(
        config_dir=config,
        data_dir=data,
        log_dir=log,
        runtime_dir=runtime,
    )


@pytest.fixture
def fake_rclone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point RCLONE_BIN at a wrapper for tests/fixtures/fake_rclone.py.

    Returns the directory acting as the fake cloud.
    """
    target = tmp_path / "cloud"
    target.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if sys.platform == "win32":
        rclone_bin = bin_dir / "rclone.bat"
        rclone_bin.write_text(
            f'@echo off\r\n"{sys.executable}" "{FAKE_RCLONE}" %*\r\n',
            encoding="utf-8",
        )
    else:
        rclone_bin = bin_dir / "rclone"
        rclone_bin.write_text(
            f'#!/usr/bin/env bash\nexec {sys.executable} {FAKE_RCLONE} "$@"\n',
            encoding="utf-8",
        )
        rclone_bin.chmod(
            rclone_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    monkeypatch.setenv("RCLONE_BIN", str(rclone_bin))
    monkeypatch.setenv("FAKE_RCLONE_REMOTE", "testremote")
    monkeypatch.setenv("FAKE_RCLONE_TARGET", str(target))
    return target


@pytest.fixture
def synth_volume(tmp_path: Path) -> Path:
    """Build a fake DJI mountpoint with two recording dates."""
    media = tmp_path / "volume" / "DCIM" / "100MEDIA"
    media.mkdir(parents=True)

    feb1 = datetime(2026, 2, 1, 12, 0, 0).timestamp()
    feb2 = datetime(2026, 2, 2, 12, 0, 0).timestamp()
    for name, ts in [
        ("DJI_001.MP4", feb1),
        ("DJI_002.JPG", feb1),
        ("DJI_003.MP4", feb2),
    ]:
        p = media / name
        p.write_bytes(b"x" * 16)
        os.utime(p, (ts, ts))

    return tmp_path / "volume"
