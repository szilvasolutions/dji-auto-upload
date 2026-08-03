from __future__ import annotations

from pathlib import Path

from dji_auto_upload.paths import AppPaths
from dji_auto_upload.runstate import RunState, read_state, write_state


def _paths(tmp: Path) -> AppPaths:
    return AppPaths(config_dir=tmp, data_dir=tmp, log_dir=tmp, runtime_dir=tmp)


def test_write_then_read_roundtrips(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    write_state(p, RunState(status="done", stage="done", percent=100.0, albums=["DJI-2026-08-03"]))
    st = read_state(p)
    assert st is not None
    assert st.status == "done"
    assert st.percent == 100.0
    assert st.albums == ["DJI-2026-08-03"]


def test_read_missing_is_none(tmp_path: Path) -> None:
    assert read_state(_paths(tmp_path)) is None


def test_read_tolerates_corrupt_file(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    (tmp_path / "last-run.json").write_text("{ not json", encoding="utf-8")
    assert read_state(p) is None


def test_unknown_fields_are_ignored(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    (tmp_path / "last-run.json").write_text('{"status":"done","from_future":1}', encoding="utf-8")
    st = read_state(p)
    assert st is not None and st.status == "done"
