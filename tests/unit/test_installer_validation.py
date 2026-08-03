"""Trigger-input validation: config values are rendered into a root-owned udev
rule and a logon PowerShell script, so anything that could break out of its
quoting context must be rejected before install."""

from __future__ import annotations

from pathlib import Path

import pytest

from dji_auto_upload.config import Config, DetectConfig
from dji_auto_upload.errors import ConfigError
from dji_auto_upload.installers import validate_trigger_inputs


def _cfg(vendor_ids: tuple[str, ...] = ("2ca3",), labels: tuple[str, ...] = ("DJI",)) -> Config:
    return Config(detect=DetectConfig(vendor_ids=vendor_ids, volume_labels=labels))


def test_default_config_is_valid() -> None:
    validate_trigger_inputs(Config())  # must not raise


def test_typical_custom_values_are_valid() -> None:
    validate_trigger_inputs(_cfg(("2ca3", "05ac"), ("DJI", "DJIMEDIA", "My Drone_1")))


@pytest.mark.parametrize(
    "bad_vid",
    ["2ca", "2ca3f", "2cg3", '2ca3", RUN+="/bin/evil', "", "2c a"],
)
def test_bad_vendor_ids_rejected(bad_vid: str) -> None:
    with pytest.raises(ConfigError):
        validate_trigger_inputs(_cfg(vendor_ids=(bad_vid,)))


@pytest.mark.parametrize(
    "bad_label",
    [
        'DJI", RUN+="/bin/evil',            # udev rule breakout
        "DJI'; Start-Process calc; '",      # PowerShell breakout
        "DJI`ncalc",                        # backtick escape
        "x" * 33,                           # over-long
        "",                                 # empty
        "DJI\nRUN",                         # newline
    ],
)
def test_bad_labels_rejected(bad_label: str) -> None:
    with pytest.raises(ConfigError):
        validate_trigger_inputs(_cfg(labels=(bad_label,)))


# ---- udev rule: which config the triggered (root) run reads --------------------


def _rule(config_dir: str | None) -> str:
    from dji_auto_upload.installers.linux_udev import _render_rule

    return _render_rule("/usr/bin/dji-auto-upload", ("2ca3",), ("DJI",), config_dir)


def test_rule_pins_config_dir_so_the_root_run_uses_the_users_settings() -> None:
    """udev runs the offload as root, which resolves to /etc/dji-auto-upload.
    `setup` writes to ~/.config, so without --config the trigger would silently
    fall back to built-in defaults (wrong remote, no credentials)."""
    rule = _rule("/home/adam/.config/dji-auto-upload")
    assert "--config /home/adam/.config/dji-auto-upload run --device" in rule


def test_rule_omits_config_when_root_default_is_correct() -> None:
    run_lines = [ln for ln in _rule(None).splitlines() if "RUN+=" in ln]
    assert run_lines
    assert not [ln for ln in run_lines if "--config" in ln]


def test_rule_action_lines_are_never_swallowed_by_a_comment() -> None:
    """A stray Jinja whitespace-trim once merged the ACTION line into the `#`
    header, which silently disables the whole rule."""
    for cfg_dir in ("/home/adam/.config/dji-auto-upload", None):
        rule = _rule(cfg_dir)
        assert not [
            ln for ln in rule.splitlines() if ln.strip().startswith("#") and "ACTION==" in ln
        ]
        assert len([ln for ln in rule.splitlines() if ln.startswith("ACTION==")]) == 2


def test_unsafe_config_path_is_not_embedded_in_the_root_owned_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The RUN+= line is whitespace-separated and runs as root — a path with
    spaces or quotes must never be baked in."""
    from dji_auto_upload.installers import linux_udev

    for evil in ('/home/a b/.config"; rm -rf /', "/home/x'y/.config", "relative/path"):
        assert not linux_udev._SAFE_PATH_RE.match(evil)
    assert linux_udev._SAFE_PATH_RE.match("/home/adam/.config/dji-auto-upload")


def test_rule_points_rclone_at_the_users_config() -> None:
    """The triggered run is root, so rclone would resolve remotes from /root and
    report the user's remote as unreachable."""
    from dji_auto_upload.installers.linux_udev import _render_rule

    rule = _render_rule(
        "/usr/bin/dji-auto-upload", ("2ca3",), (), None, "/home/adam/.config/rclone/rclone.conf"
    )
    assert "--setenv=RCLONE_CONFIG=/home/adam/.config/rclone/rclone.conf" in rule
    # And is omitted entirely when we have nothing to point at.
    assert "RCLONE_CONFIG" not in _render_rule("/usr/bin/dji-auto-upload", ("2ca3",), (), None, None)


def test_task_xml_survives_an_ampersand_in_the_username() -> None:
    """`&` in a username or path makes the task XML malformed and schtasks
    fails with an opaque error."""
    import xml.dom.minidom as md

    from dji_auto_upload.installers.windows_task import _render

    xml = _render(
        "DjiAutoUploadTask.xml.j2",
        powershell="powershell.exe",
        watcher_path=r"C:\Users\Tom & Jerry\dji-watcher.ps1",
        user="DOMAIN\\Tom & Jerry",
    )
    md.parseString(xml)  # raises if malformed
    assert "&amp;" in xml and "&amp;amp;" not in xml


def test_runner_binary_path_with_an_apostrophe_cannot_break_the_string() -> None:
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-run.ps1.j2",
        binary=_ps_single_quote(r"C:\Users\O'Brien\dji.exe"),
        pre_args=[],
        log_file=r"C:\Users\O'Brien\log.txt",
    )
    assert "'C:\\Users\\O''Brien\\dji.exe'" in ps


def test_runner_can_launch_via_interpreter_when_shim_is_off_path() -> None:
    """pip --user installs often leave Scripts/ off PATH; the runner must then
    launch `python -m dji_auto_upload` instead of a bare name that would fail
    to resolve at event time."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-run.ps1.j2",
        binary=_ps_single_quote(r"C:\Python312\python.exe"),
        pre_args=["-m", "dji_auto_upload"],
        log_file=r"C:\logs\dji.log",
    )
    assert "& 'C:\\Python312\\python.exe' '-m' 'dji_auto_upload' run --device $Drive" in ps


def test_watcher_polls_and_launches_hidden_worker_plus_visible_viewer() -> None:
    """Event-action registration failed three separate ways in the field, so the
    watcher polls. It must start the WORKER hidden (un-closable, so a closed
    window can't kill the transfer) and the VIEWER visible (closable progress)."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-watcher.ps1.j2",
        worker=_ps_single_quote(r"C:\Users\szisz\AppData\Local\dji-auto-upload\dji-run.ps1"),
        viewer=_ps_single_quote(r"C:\Users\szisz\AppData\Local\dji-auto-upload\dji-view.ps1"),
        vendor_ids=["2ca3"],
        labels=["DJI"],
    )
    code = "\n".join(ln for ln in ps.splitlines() if not ln.strip().startswith("#"))
    assert "Register-WmiEvent" not in code
    assert "Get-ReadyRemovableDrives" in code
    # Worker launched hidden; viewer launched visible.
    assert "-WindowStyle Hidden" in code and "dji-run.ps1" in ps
    assert "-WindowStyle Normal" in code and "dji-view.ps1" in ps


def test_viewer_is_read_only_and_never_runs_an_offload() -> None:
    """Closing the viewer must be harmless: it only watches, it never itself runs
    the offload."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-view.ps1.j2",
        binary=_ps_single_quote(r"C:\Python312\python.exe"),
        pre_args=["-m", "dji_auto_upload"],
    )
    assert "watch-run" in ps
    assert "run --device" not in ps


def test_worker_maps_exit_codes_to_distinct_popups() -> None:
    """0 = success, 75 = already-running (neither success nor error), else failure
    with the log path. The 75 case is what stops a skipped run popping 'complete'."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-run.ps1.j2",
        binary=_ps_single_quote(r"C:\Python312\python.exe"),
        pre_args=["-m", "dji_auto_upload"],
        log_file=r"C:\logs\dji.log",
    )
    assert "run --device $Drive" in ps
    assert "$code -eq 0" in ps
    assert "$code -eq 75" in ps
    assert "FAILED" in ps
