"""Sleep inhibitor: a run must not be frozen mid-upload by an idle laptop.

The inhibitor is strictly best-effort, so the contract these tests lock down is
mostly negative: it must never raise, never block a run, and always release.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from dji_auto_upload import platform_glue
from dji_auto_upload.platform_glue import inhibit_sleep


class FakePopen:
    """Stands in for a live inhibitor helper (caffeinate / systemd-inhibit)."""

    def __init__(self, *, dies_immediately: bool = False) -> None:
        self.terminated = False
        self.waited = False
        self._dies_immediately = dies_immediately

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self._dies_immediately:
            return 1
        if timeout is not None and not self.terminated:
            raise subprocess.TimeoutExpired("inhibitor", timeout)
        return 0

    def terminate(self) -> None:
        self.terminated = True


class Spawned:
    """Records the inhibitor commands issued and the fake processes handed back."""

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []
        self.procs: list[FakePopen] = []

    def popen(self, cmd: list[str], **kwargs: object) -> FakePopen:
        self.cmds.append(cmd)
        proc = FakePopen()
        self.procs.append(proc)
        return proc


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawned:
    """Pretend we're on Linux with a working systemd-inhibit."""
    rec = Spawned()
    monkeypatch.setattr(platform_glue.sys, "platform", "linux")
    monkeypatch.setattr(platform_glue.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(platform_glue.subprocess, "Popen", rec.popen)
    return rec


def test_disabled_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("must not spawn anything when disabled")

    monkeypatch.setattr(platform_glue.subprocess, "Popen", boom)
    with inhibit_sleep(enabled=False) as how:
        assert how is None


def test_linux_holds_and_releases_systemd_inhibit(spawned: Spawned) -> None:
    with inhibit_sleep(reason="testing") as how:
        assert how == "systemd-inhibit"
        assert len(spawned.cmds) == 1
        cmd = spawned.cmds[0]
        assert cmd[0] == "systemd-inhibit"
        assert "--what=sleep:idle" in cmd
        assert "--why=testing" in cmd
        assert "--mode=block" in cmd
        # Still held while the block is running.
        assert not spawned.procs[0].terminated

    # Released on the way out, so the machine can sleep normally again.
    assert spawned.procs[0].terminated


def test_inhibitor_is_released_even_when_the_run_raises(spawned: Spawned) -> None:
    with pytest.raises(RuntimeError), inhibit_sleep():
        raise RuntimeError("upload blew up")
    assert spawned.procs[0].terminated


def test_missing_binary_degrades_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_glue.sys, "platform", "linux")
    monkeypatch.setattr(platform_glue.shutil, "which", lambda name: None)

    with inhibit_sleep() as how:
        assert how is None  # no protection, but the run still proceeds


def test_helper_that_dies_instantly_is_not_reported_as_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """logind can refuse the inhibitor; claiming we hold one would be a lie."""
    monkeypatch.setattr(platform_glue.sys, "platform", "linux")
    monkeypatch.setattr(platform_glue.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        platform_glue.subprocess,
        "Popen",
        lambda cmd, **kw: FakePopen(dies_immediately=True),
    )

    with inhibit_sleep() as how:
        assert how is None


def test_spawn_failure_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_glue.sys, "platform", "linux")
    monkeypatch.setattr(platform_glue.shutil, "which", lambda name: f"/usr/bin/{name}")

    def raise_oserror(cmd: list[str], **kw: object) -> None:
        raise OSError("fork failed")

    monkeypatch.setattr(platform_glue.subprocess, "Popen", raise_oserror)

    with inhibit_sleep() as how:
        assert how is None


def test_macos_uses_caffeinate_bound_to_our_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(platform_glue.sys, "platform", "darwin")
    monkeypatch.setattr(platform_glue.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        platform_glue.subprocess,
        "Popen",
        lambda cmd, **kw: (calls.append(cmd), FakePopen())[1],
    )
    monkeypatch.setattr(platform_glue.os, "getpid", lambda: 4242)

    with inhibit_sleep() as how:
        assert how == "caffeinate"

    cmd = calls[0]
    assert cmd[0] == "caffeinate"
    assert "-i" in cmd  # block idle sleep
    # -w <pid> makes caffeinate exit with us even if we are killed outright.
    assert cmd[cmd.index("-w") + 1] == "4242"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows execution-state API")
def test_windows_sets_and_clears_execution_state() -> None:  # pragma: no cover
    with inhibit_sleep() as how:
        assert how == "SetThreadExecutionState"
