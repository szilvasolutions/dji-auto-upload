# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
