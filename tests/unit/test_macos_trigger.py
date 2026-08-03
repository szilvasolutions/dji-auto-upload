"""macOS LaunchAgent guards.

The macOS auto-trigger silently never ran in 0.1–0.4.0 because Typer registered
the hidden `_watch` callback under the command name `-watch`, so the plist's
`_watch` argv could not dispatch. These tests pin the invariant so it can't
regress: every token the plist runs must resolve to a real, invokable command.
"""

from __future__ import annotations

import xml.dom.minidom as md
from pathlib import Path

import typer.main

from dji_auto_upload.cli import app
from dji_auto_upload.installers.macos_launchd import _render_plist, _watch_argv


def _registered() -> set[str]:
    return set(typer.main.get_command(app).commands.keys())


def test_watch_command_is_registered_under_the_name_the_plist_uses() -> None:
    assert "_watch" in _registered()
    assert "-watch" not in _registered()


def test_plist_argv_terminal_token_is_a_real_command() -> None:
    argv = _watch_argv()
    # last token is the subcommand; everything before it is python/-m/module or
    # the console script — the subcommand must be dispatchable.
    assert argv[-1] in _registered()


def test_plist_is_valid_xml_even_with_special_chars(tmp_path: Path) -> None:
    xml = _render_plist(
        [sys_exe := "/Users/a & b/python", "-m", "dji_auto_upload", "_watch"],
        tmp_path / "Logs & Stuff",
    )
    md.parseString(xml)  # raises if malformed
    assert "&amp;" in xml
    assert sys_exe.replace("&", "&amp;") in xml


def test_watch_argv_prefers_console_script_then_interpreter(monkeypatch) -> None:
    import dji_auto_upload.installers.macos_launchd as m

    monkeypatch.setattr(m.shutil, "which", lambda _: "/opt/homebrew/bin/dji-auto-upload")
    assert _watch_argv() == ["/opt/homebrew/bin/dji-auto-upload", "_watch"]

    monkeypatch.setattr(m.shutil, "which", lambda _: None)
    argv = _watch_argv()
    assert argv[1:] == ["-m", "dji_auto_upload", "_watch"]
    assert argv[0].endswith("python") or "python" in argv[0]
