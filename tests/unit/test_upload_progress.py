"""Live upload progress.

Field report: a six-minute, 10.7 GB upload showed "0% Uploading to cloud…" the
whole way. rclone was reporting 40%, 85%, MB/s and ETA all along — but
capture_output=True buffered every line until the process exited, so the log
showed all of them stamped with the finish time and the bar never moved.
"""

from __future__ import annotations

import pytest

from dji_auto_upload.upload import parse_stats

# Verbatim lines from the field log.
MULTILINE = "Transferred:        3.164 GiB / 7.920 GiB, 40%, 46.452 MiB/s, ETA 1m44s"
ONELINE = "2026/08/03 12:39:32 INFO  : 6.765 GiB / 7.920 GiB, 85%, 61.098 MiB/s, ETA 19s"


def test_parses_the_multiline_stats_block() -> None:
    st = parse_stats(MULTILINE)
    assert st is not None
    assert st.percent == 40.0
    assert st.transferred == "3.164 GiB"
    assert st.total == "7.920 GiB"
    assert st.speed == "46.452 MiB/s"
    assert st.eta == "1m44s"


def test_parses_the_one_line_stats_form() -> None:
    st = parse_stats(ONELINE)
    assert st is not None and st.percent == 85.0
    assert "6.765 GiB / 7.920 GiB" in st.human()
    assert "ETA 19s" in st.human()


def test_file_count_line_is_not_mistaken_for_byte_progress() -> None:
    """'Transferred: 2 / 3, 67%' counts files, not bytes. Using it would make the
    bar lurch in thirds instead of moving smoothly."""
    assert parse_stats("Transferred:            2 / 3, 67%") is None


@pytest.mark.parametrize(
    "line",
    [
        "2026/08/03 NOTICE: this remote uses rclone's shared client_id",
        "2026/08/03 INFO  : DJI_0001.MP4: Copied (new)",
        "",
    ],
)
def test_non_progress_lines_yield_nothing(line: str) -> None:
    assert parse_stats(line) is None


def test_percent_is_clamped() -> None:
    st = parse_stats("Transferred: 1 GiB / 1 GiB, 140%, 1 MiB/s")
    assert st is not None and st.percent == 100.0


def test_streaming_invokes_the_callback_for_each_reading(monkeypatch, tmp_path) -> None:
    """The callback must fire DURING the transfer, not once at the end."""
    import subprocess

    from dji_auto_upload import upload as up
    from dji_auto_upload.config import BehaviourConfig, RemoteConfig

    emitted = [
        "2026/08/03 INFO  : 1.000 GiB / 4.000 GiB, 25%, 20 MiB/s, ETA 2m",
        "2026/08/03 INFO  : 2.000 GiB / 4.000 GiB, 50%, 20 MiB/s, ETA 1m30s",
        "2026/08/03 INFO  : 4.000 GiB / 4.000 GiB, 100%, 20 MiB/s, ETA 0s",
    ]

    class FakeProc:
        def __init__(self) -> None:
            self.stderr = iter(ln + "\n" for ln in emitted)

        def wait(self) -> int:
            return 0

        def kill(self) -> None:  # pragma: no cover
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    (tmp_path / "a.MP4").write_bytes(b"x")

    seen: list[float] = []
    res = up.upload_files(
        tmp_path, ["a.MP4"], "DJI/2026-08-03",
        remote=RemoteConfig(name="r", path_template="DJI/{date}"),
        behaviour=BehaviourConfig(),
        on_progress=lambda st: seen.append(st.percent),
    )
    assert res.rc == 0
    assert seen == [25.0, 50.0, 100.0], seen
