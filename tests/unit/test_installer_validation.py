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
    assert "Get-CandidateDrives" in code
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


def test_worker_logs_start_and_exit_so_a_hidden_failure_leaves_a_trail() -> None:
    """The worker runs hidden. Without its own log, a failure before Python even
    starts (execution policy, bad path) is invisible everywhere."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-run.ps1.j2",
        binary=_ps_single_quote(r"C:\Py\dji.exe"),
        pre_args=[],
        log_file=r"C:\logs\dji.log",
    )
    assert "worker started for" in ps
    assert "worker finished for" in ps
    assert "FAILED to launch" in ps
    assert "watcher.log" in ps


def test_watcher_does_not_filter_out_drives_windows_calls_fixed() -> None:
    """Field failure: the goggles mounted as D:\\ but the watcher logged nothing
    at all, because it only enumerated DriveType 'Removable' and Windows reports
    many USB devices as 'Fixed'. Enumerate every ready non-system drive instead."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-watcher.ps1.j2",
        worker=_ps_single_quote(r"C:\x\dji-run.ps1"),
        viewer=_ps_single_quote(r"C:\x\dji-view.ps1"),
        vendor_ids=["2ca3"],
        labels=["DJI"],
    )
    assert "DriveType -eq 'Removable'" not in ps
    assert "SystemDrive" in ps          # system drive excluded instead
    assert "drives visible" in ps       # and it logs what it can see


def test_task_runs_the_vbs_launcher_not_powershell_directly() -> None:
    """Field failure: the task ran `powershell -WindowStyle Hidden`, which still
    allocates a console. A window appeared, the user closed it, and the watcher
    died — twice. The task must run the windowless .vbs launcher instead."""
    import xml.dom.minidom as md

    from dji_auto_upload.installers.windows_task import _render

    xml = _render(
        "DjiAutoUploadTask.xml.j2",
        powershell="powershell.exe",
        watcher_path=r"C:\x\dji-watcher.ps1",
        launcher_path=r"C:\x\dji-watcher-launch.vbs",
        user="szisz",
        repeat=True,
    )
    md.parseString(xml)
    assert "<Command>wscript.exe</Command>" in xml
    assert "dji-watcher-launch.vbs" in xml
    assert "-WindowStyle Hidden -ExecutionPolicy" not in xml


def test_vbs_launcher_spawns_with_no_window() -> None:
    from dji_auto_upload.installers.windows_task import _render

    vbs = _render("dji-watcher-launch.vbs.j2", watcher_path=r"C:\x\dji-watcher.ps1")
    # intWindowStyle 0 = no window; bWaitOnReturn False = launcher exits at once.
    assert ", 0, False" in vbs
    assert '""C:\\x\\dji-watcher.ps1""' in vbs  # VBS-escaped quoting


def test_watcher_holds_a_single_instance_mutex() -> None:
    """The task repeats every 5 min and the launcher exits immediately, so
    without a mutex each repetition would stack another watcher."""
    from dji_auto_upload.installers.windows_task import _ps_single_quote, _render

    ps = _render(
        "dji-watcher.ps1.j2",
        worker=_ps_single_quote(r"C:\x\dji-run.ps1"),
        viewer=_ps_single_quote(r"C:\x\dji-view.ps1"),
        vendor_ids=["2ca3"],
        labels=["DJI"],
    )
    assert "System.Threading.Mutex" in ps
    assert "already running" in ps


def test_vbs_launcher_is_pure_ascii_and_gets_no_bom() -> None:
    """VBScript cannot parse a UTF-8 BOM: wscript dies with 'Invalid character'
    on line 1 and //B hides the dialog, so the watcher never starts and nothing
    is logged anywhere. The .ps1 files need a BOM; this file must not have one."""
    import inspect

    from dji_auto_upload.installers import windows_task as w

    vbs = w._render("dji-watcher-launch.vbs.j2", watcher_path=r"C:\x\dji-watcher.ps1")
    assert vbs.isascii(), [c for c in vbs if not c.isascii()]
    assert not vbs.encode("ascii").startswith(b"\xef\xbb\xbf")

    # And the installer must write it without a BOM.
    src = inspect.getsource(w.install)
    launcher_block = src[src.index("dji-watcher-launch.vbs.j2"):]
    launcher_block = launcher_block[: launcher_block.index(")")]
    assert "utf-8-sig" not in launcher_block


def test_install_kills_stale_watchers_before_starting_a_new_one() -> None:
    """A running watcher holds the OLD script in memory, so after an update it
    keeps behaving like the previous version — which made several updates look
    like they had changed nothing. Workers are deliberately NOT killed: one may
    be mid-upload."""
    import inspect

    from dji_auto_upload.installers import windows_task as w

    src = inspect.getsource(w.install)
    assert "_kill_stale_watchers()" in src

    killer = inspect.getsource(w._kill_stale_watchers)
    assert "dji-watcher.ps1" in killer
    assert "dji-run.ps1" not in killer  # never kill a worker mid-transfer
