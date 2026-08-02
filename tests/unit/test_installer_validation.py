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


def test_ps1_binary_path_with_an_apostrophe_cannot_break_the_string() -> None:
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-watcher.ps1.j2",
        binary=_ps_single_quote(r"C:\Users\O'Brien\dji.exe"),
        vendor_ids=["2ca3"],
        labels=["DJI"],
    )
    assert "'C:\\Users\\O''Brien\\dji.exe'" in ps
