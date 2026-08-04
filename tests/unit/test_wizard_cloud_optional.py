"""Cloud is optional, so nothing in setup may dead-end a user who hasn't got
rclone and doesn't want it."""

from __future__ import annotations

import pytest

from dji_auto_upload import wizard


def test_rclone_present_needs_no_prompting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.shutil, "which", lambda n: "/usr/bin/rclone")
    monkeypatch.setattr(
        wizard.Confirm, "ask", lambda *a, **k: pytest.fail("should not prompt")
    )
    assert wizard._ensure_rclone() is True


def test_declining_the_install_returns_false_instead_of_exiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old code called sys.exit(1) here, which killed setup outright."""
    monkeypatch.setattr(wizard.shutil, "which", lambda n: None if n == "rclone" else f"/bin/{n}")
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: False)
    assert wizard._ensure_rclone() is False  # returns, does not raise SystemExit


def test_failed_install_still_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.shutil, "which", lambda n: None if n == "rclone" else f"/bin/{n}")
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: None)  # install no-ops
    assert wizard._ensure_rclone() is False


def test_successful_install_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"installed": False}

    def which(n: str) -> str | None:
        if n == "rclone":
            return "/usr/bin/rclone" if state["installed"] else None
        return f"/bin/{n}"

    monkeypatch.setattr(wizard.shutil, "which", which)
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(
        wizard.subprocess, "run", lambda *a, **k: state.__setitem__("installed", True)
    )
    assert wizard._ensure_rclone() is True


def test_setup_never_forces_rclone_before_asking_about_cloud() -> None:
    """The install scripts used to install rclone up front — including a sudo
    prompt on Linux — before setup ever asked whether the user wanted cloud."""
    import pathlib

    for script in ("install.sh", "install.ps1"):
        body = pathlib.Path(script).read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in body.splitlines()
            if not ln.strip().startswith("#") and not ln.strip().startswith("'")
        )
        assert "install rclone" not in code.lower(), script
        assert "Rclone.Rclone" not in code, script
