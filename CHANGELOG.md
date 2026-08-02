# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-08-02

### Added
- Sleep inhibition while a run is in progress, so an idle laptop no longer
  suspends mid-upload after you plug in and walk away. Uses
  `SetThreadExecutionState` on Windows, `caffeinate` on macOS and
  `systemd-inhibit` on Linux; released as soon as the run ends, including when
  it fails. Best-effort by design: if no inhibitor can be taken the run still
  proceeds. Switch it off with `inhibit_sleep = false`. Note this blocks idle
  sleep only; closing the lid still suspends, which the ledger already makes
  safe to resume from.

### Fixed
- **The published package could not be installed at all.** A redundant
  `force-include` for the installer templates made hatchling add every `.j2`
  twice and abort the wheel build. CI only ever did an editable install, so
  nothing caught it; it now builds the wheel, installs it, and asserts the
  templates ship.
- Windows auto-trigger never fired: the `Register-WmiEvent` action scriptblock
  runs in its own scope, so the `Test-DjiVolume` function and the
  vendor-ID/label config were undefined at event time, and the error was
  swallowed by `$ErrorActionPreference = 'Continue'`. `$using:` is not valid in
  an event action either (only `Invoke-Command` / `Start-Job` /
  `ForEach-Object -Parallel`), so the watcher now defines everything the action
  needs in global scope. It also logs to
  `%LOCALAPPDATA%\dji-auto-upload\watcher.log`, so a failure is visible instead
  of silent.
- The Linux auto-trigger read a different config than `setup` wrote. udev runs
  the offload as root, which resolves to `/etc/dji-auto-upload`, while
  `dji-auto-upload setup` writes to `~/.config/dji-auto-upload`. The trigger
  silently fell back to built-in defaults (wrong remote, no credentials, no
  notifications). `install-trigger` now pins the rule to the invoking user's
  config dir with `--config`, and says so; if it can't find one it warns instead
  of installing a trigger that quietly does nothing.
- The Telegram bot token was written to the log file. `requests` embeds the
  request URL — which contains the token — in its exception strings, and those
  log lines are also tailed into the `fail` notification. The token is now
  redacted before anything is logged.
- The Windows watcher reported drives as `E:`, which is drive-*relative*:
  `Path("E:") / "DCIM"` resolves against that drive's current directory rather
  than its root. Bare drive letters are now normalised to `E:\`.
- `.part` copy temporaries left behind by a killed run were treated as
  uploadable, which could put a truncated clip in the album and then ledger it
  as complete. They are now skipped.
- `strict_detect` was ignored whenever a volume was passed with `--device` —
  which is how every auto-trigger invokes the run, so the setting had no effect
  on the path it was written for.
- On Linux, a finished run claimed "drone ejected, safe to unplug" even when it
  had not unmounted anything (an automounted card is not ours to release).
- The Linux sleep inhibitor could outlive a killed run. The helper held a bare
  `sleep 86400`, so a SIGKILLed run left the machine unable to idle-suspend for
  a day; it is now bound to the run's pid, matching `caffeinate -w`.
- `uninstall-trigger` raised on the wrong OS (e.g. calling the Linux uninstaller on
  Windows hit `os.geteuid`, which doesn't exist there). It now no-ops with a clear
  message, matching `install-trigger`.

### Changed
- CI now enforces `mypy` (dropped `continue-on-error`); the tree type-checks clean
  on Linux, macOS, and Windows.

## [0.1.0] - 2026-07-28

Initial public release.

### Added
- Cross-platform Python CLI: `dji-auto-upload` for Linux, macOS, Windows.
- Auto-trigger on USB plug-in (udev / launchd+DiskArbitration / Scheduled Task+WMI).
- Interactive setup wizard with rclone detection and Telegram chat-ID auto-grab.
- Per-date staging with `.uploaded` ledger for upload deduplication.
- Configurable retention for local stage and drone-side files.
- Safe by default: drone-side deletion is strictly opt-in and only runs after a
  confirmed upload; out of the box nothing is ever removed from the drone.
- Optional Telegram notifications with per-event filtering.
- Sentinel-based stage prune (never deletes un-uploaded data).
- Disk-space precheck and self-healing pre-copy prune.
- Drone-disconnect detection with replug-to-resume semantics.
- `--dry-run`: prints the full copy/upload/delete plan without changing anything.
- Auto-eject when the run finishes, with a "safe to unplug" notification.
- Per-file upload fallback: if an rclone batch fails, each file is retried
  individually and every confirmed upload is ledgered, so a flaky transfer can
  never cause an album to be re-uploaded (which would duplicate it in Google
  Photos, whose backend cannot dedupe server-side).
- Sidecar handling: `.SRT` telemetry and `.LRF` proxies are removed only
  together with the video they belong to.
- Drone-clock sanity check: cleanup is skipped when file timestamps are in the
  future or absurdly old (RTC reset), instead of deleting on bad dates.
- `strict_detect` option to trigger only on DJI vendor ID / volume label.
- Copy progress logging and a "last run" line in `dji-auto-upload status`.

### Security
- Trigger inputs (`vendor_ids`, `volume_labels`) are validated before being
  rendered into the root-owned udev rule or the logon PowerShell watcher, so a
  tampered config cannot inject commands that would run as root.
- Removable media is mounted `nosuid,nodev,noexec` in addition to read-only.
- Telegram chat-ID auto-discovery now asks for confirmation before binding to
  the sender, so a stranger messaging your bot can't capture your notifications.
- Credentials are written through a 0600 file descriptor rather than being
  chmod'ed after the fact, closing a brief world-readable window.

### Fixed
- Drone cleanup deleted age-eligible files that had never been uploaded
  (including sidecars, which are never uploaded). Deletion is now gated per file
  on the `.uploaded` ledger.
- Stage dirs with pending uploads could be pruned once the sentinel aged out.
- An empty drone (the normal state after a successful offload with cleanup on)
  raised an inventory error and fired a failure notification on every replug.

[0.2.0]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/szilvasolutions/dji-auto-upload/releases/tag/v0.1.0
