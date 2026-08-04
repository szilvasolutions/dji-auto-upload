"""Uninstall must never take footage with it.

Two cases matter most: local-only mode, where the staging folder is the user's
ONLY copy, and cloud mode where some files have not reached the cloud yet.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dji_auto_upload.cli import app

runner = CliRunner()


def _setup(tmp_path: Path, *, cloud: bool, ledger_all: bool) -> tuple[Path, Path]:
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    stage = tmp_path / "footage"
    day = stage / "2026-08-04"
    day.mkdir(parents=True)
    (day / "DONE.MP4").write_bytes(b"x" * 1000)
    (day / "PENDING.MP4").write_bytes(b"y" * 500)
    entries = "DONE.MP4\t1000\n" + ("PENDING.MP4\t500\n" if ledger_all else "")
    (day / ".uploaded").write_text(entries, encoding="utf-8")
    (cfgdir / "config.toml").write_text(
        f"[remote]\nenabled = {str(cloud).lower()}\nname = 'x'\n"
        f"[paths]\nstage_dir = '{stage.as_posix()}'\n[notifier]\nenabled = false\n",
        encoding="utf-8",
    )
    return cfgdir, stage


def test_purge_refuses_when_local_only_is_the_only_copy(tmp_path: Path) -> None:
    cfgdir, stage = _setup(tmp_path, cloud=False, ledger_all=True)
    res = runner.invoke(app, ["--config", str(cfgdir), "uninstall", "--yes", "--purge"])
    assert res.exit_code == 0
    assert (stage / "2026-08-04" / "DONE.MP4").is_file()
    assert "only copy" in res.stdout


def test_purge_refuses_while_anything_is_unuploaded(tmp_path: Path) -> None:
    cfgdir, stage = _setup(tmp_path, cloud=True, ledger_all=False)
    res = runner.invoke(
        app, ["--config", str(cfgdir), "uninstall", "--yes", "--purge", "--keep-config"]
    )
    assert res.exit_code == 0
    assert (stage / "2026-08-04" / "PENDING.MP4").is_file()
    assert (stage / "2026-08-04" / "DONE.MP4").is_file()


def test_purge_removes_footage_only_when_everything_is_in_the_cloud(tmp_path: Path) -> None:
    cfgdir, stage = _setup(tmp_path, cloud=True, ledger_all=True)
    res = runner.invoke(
        app, ["--config", str(cfgdir), "uninstall", "--yes", "--purge", "--keep-config"]
    )
    assert res.exit_code == 0
    assert not stage.exists()


def test_plain_uninstall_never_touches_footage(tmp_path: Path) -> None:
    """Without --purge the footage stays put, whatever else is removed."""
    cfgdir, stage = _setup(tmp_path, cloud=True, ledger_all=True)
    res = runner.invoke(app, ["--config", str(cfgdir), "uninstall", "--yes"])
    assert res.exit_code == 0
    assert (stage / "2026-08-04" / "DONE.MP4").is_file()
    assert "still in" in res.stdout
