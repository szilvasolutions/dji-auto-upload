"""Shared pytest fixtures.

`tmp_app_paths` gives every test an isolated AppPaths so the test suite can run
on a developer machine without overwriting their real config.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dji_auto_upload.paths import AppPaths


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
