#!/usr/bin/env python
"""Stand-in for rclone in tests.

Implements the subset of rclone flags the offload pipeline uses:
  - listremotes              → prints `<remote>:` for each FAKE_RCLONE_REMOTE
  - lsd <remote>:            → exits 0 if remote is in FAKE_RCLONE_REMOTE
  - copy <src> <remote>:path --files-from <list>
        → copies each listed basename from src into FAKE_RCLONE_TARGET/<remote>/<path>
        → exit code controlled by FAKE_RCLONE_EXIT (default 0)
        → optional FAKE_RCLONE_FAIL_ON_DATE substring to fail a specific date

Honored env vars:
  FAKE_RCLONE_REMOTE      comma-separated list of valid remote names (default: testremote)
  FAKE_RCLONE_TARGET      directory acting as the cloud (required for `copy`)
  FAKE_RCLONE_EXIT        integer exit code for `copy` (default: 0)
  FAKE_RCLONE_FAIL_ON_DATE if the remote path contains this substring, exit non-zero
  FAKE_RCLONE_FAIL_MULTI   if set, any copy with >1 file in --files-from exits 3
                           (simulates a flaky batch; single-file retries can pass)
  FAKE_RCLONE_FAIL_ON_NAME if any listed basename contains this substring, exit 4
                           (simulates one poison file that always fails)
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _remotes() -> list[str]:
    raw = os.environ.get("FAKE_RCLONE_REMOTE", "testremote")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _cmd_listremotes() -> int:
    for r in _remotes():
        sys.stdout.write(f"{r}:\n")
    return 0


def _cmd_lsd(target: str) -> int:
    name = target.rstrip(":")
    return 0 if name in _remotes() else 1


def _cmd_copy(args: list[str]) -> int:
    if len(args) < 2:
        return 2
    src = Path(args[0])
    dest = args[1]   # e.g. "testremote:album/DJI-2026-04-01"
    files_from: Path | None = None
    i = 2
    while i < len(args):
        if args[i] == "--files-from":
            files_from = Path(args[i + 1])
            i += 2
        else:
            i += 1

    target_root = os.environ.get("FAKE_RCLONE_TARGET")
    if target_root is None:
        sys.stderr.write("FAKE_RCLONE_TARGET not set\n")
        return 2

    if files_from and files_from.is_file():
        names = [
            line.strip()
            for line in files_from.read_text().splitlines()
            if line.strip()
        ]
    else:
        names = [p.name for p in src.iterdir() if p.is_file() and not p.name.startswith(".")]

    if os.environ.get("FAKE_RCLONE_FAIL_MULTI") and len(names) > 1:
        return 3

    fail_name = os.environ.get("FAKE_RCLONE_FAIL_ON_NAME", "")
    if fail_name and any(fail_name in n for n in names):
        return 4

    fail_on = os.environ.get("FAKE_RCLONE_FAIL_ON_DATE", "")
    if fail_on and fail_on in dest:
        return int(os.environ.get("FAKE_RCLONE_EXIT", "1"))

    # Only honour FAKE_RCLONE_EXIT globally when no FAIL_ON_DATE is set;
    # otherwise the env var is the per-match exit code, not a global override.
    if not fail_on:
        rc = int(os.environ.get("FAKE_RCLONE_EXIT", "0"))
        if rc != 0:
            return rc

    remote_name, _, path = dest.partition(":")
    out_dir = Path(target_root) / remote_name / path
    out_dir.mkdir(parents=True, exist_ok=True)

    for n in names:
        s = src / n
        if s.is_file():
            shutil.copy2(s, out_dir / n)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    cmd = argv[1]
    rest = argv[2:]
    if cmd == "listremotes":
        return _cmd_listremotes()
    if cmd == "lsd":
        return _cmd_lsd(rest[0]) if rest else 1
    if cmd == "copy":
        return _cmd_copy(rest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
