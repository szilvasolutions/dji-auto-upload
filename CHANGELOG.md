# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Windows auto-trigger never fired: the `Register-WmiEvent` action scriptblock ran
  in its own scope, so the `Test-DjiVolume` function and the vendor-ID/label config
  it relied on were undefined at event time (the error was swallowed by
  `$ErrorActionPreference = 'Continue'`). Detection is now inlined in the action
  block and the config is passed in via `$using:`.
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
