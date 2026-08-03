# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.1] - 2026-08-03

### Fixed
- The Windows worker runs hidden, so a failure *before* Python started (script
  blocked by execution policy, bad interpreter path) produced no window, no
  popup and no log line anywhere. It now writes its start, its exit code, and
  any launch failure to `watcher.log`, so "nothing happened" always leaves a
  trail to read.
- The generated PowerShell scripts are written with a UTF-8 BOM. Windows
  PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI, which mangles non-ASCII
  characters in them.

## [0.5.0] - 2026-08-03

### Added
- **Closing the progress window no longer stops the upload (Windows).** The
  offload now runs in its own hidden, un-closable process; the visible window is
  a separate read-only progress viewer. Close it whenever you like — the
  transfer keeps going, and a popup still reports the result.
- **A visible progress view**, `dji-auto-upload watch-run`, that tails a live
  run-state file and shows stage + a progress bar. This is what the Windows
  progress window runs; it can also be run by hand in a terminal.
- **A completion/failure signal on every OS.** macOS and Linux now get a native
  desktop notification when a run finishes or fails (previously only Windows
  showed anything and Telegram was off by default). Best-effort; degrades to
  nothing on a headless box.
- **`dji-auto-upload status` shows the last run's outcome** — done/failed/running,
  which albums uploaded, and the error if it failed — read from a durable
  run-state file, so "what happened last time?" no longer means grepping logs.
- **The macOS auto-trigger now works.** It never ran in any prior release: Typer
  registered the hidden watcher command as `-watch`, so the LaunchAgent's `_watch`
  argument could never dispatch and the agent crash-looped silently. Fixed, with
  a regression test that every plist argument resolves to a real command. The
  watcher was also rewritten to poll (matching Windows), the LaunchAgent is
  launched via the interpreter so it works under launchd's stripped PATH, the
  plist is XML-escaped, install verifies the job is actually running, and the
  heavy `pyobjc-framework-DiskArbitration` dependency is gone.

### Fixed
- A run with a custom `[paths] stage_dir` crashed in the disk-space precheck on
  first use because the folder didn't exist yet. It is created at startup now.
- A run skipped because another was already in flight exited 0 and, on Windows,
  popped "offload complete". It now exits with a distinct code and a neutral
  "already running" message — never a false success.

### Changed
- `diagnose` now also collects the macOS watcher logs.

## [0.4.1] - 2026-08-03

### Fixed
- **Footage loss when a card had more than one DCIM folder (regression in 0.3.1).**
  A camera rolls over to `101MEDIA`, `102MEDIA`… every 999 files, and two folders
  can hold different clips that share a basename (`DJI_0001.MP4`). They staged to
  the same path so one overwrote the other, the flat ledger marked both uploaded,
  and drone cleanup then erased the clip that was never sent. Files are now given a
  collision-free staged name (`101MEDIA__DJI_0001.MP4`) and both reach the cloud.
- **A re-used filename could be declared already-uploaded and trimmed off the
  drone without being sent** (two drones on one computer, or a formatted card).
  The ledger now records each file's byte size, and a same-named file whose size
  differs is treated as new — uploaded, not skipped, and not deleted from the drone
  until its own identity is confirmed in the cloud.
- Drone cleanup matches a file by name AND size, so it can only ever delete a clip
  that is provably in the cloud.
- A file that vanished from the staging folder mid-run (rclone's `--files-from`
  skips a missing source but still exits 0) is no longer recorded as uploaded.

## [0.4.0] - 2026-08-02

### Added
- Setup now asks where footage should be kept on this computer, and the choice
  is stored as `[paths] stage_dir`. The platform data directory is a poor
  default for multi-gigabyte video — people want it on a drive with room, or
  somewhere they can actually find. The folder is created and write-tested
  during setup rather than failing mid-offload, and free space is reported.
- The "where in the cloud" question spells out what it does, with worked
  examples of how `{date}` expands, and the summary shows the full destination
  (`remote:path`) alongside the local folder.

## [0.3.2] - 2026-08-02

### Fixed
- The Windows watcher never triggered anything. It registered a
  `Register-WmiEvent -Action` handler, and the host process then exited
  silently, leaving the Scheduled Task in `Ready` with no error recorded
  anywhere. That mechanism had already failed twice before for unrelated
  reasons (an event action cannot see the script's functions or variables, and
  `$using:` is not valid inside one), so it is gone. The watcher now polls for
  newly-arrived removable drives every three seconds in a single ordinary
  scope, wrapped so no single iteration can kill the loop.
- A device already plugged in when the watcher starts now counts as an arrival.
  Previously you had to unplug and replug after installing.
- The startup log line printed an empty binary path; it reports the runner and
  poll interval, and logs a heartbeat every 30 minutes so the log proves the
  watcher is alive.

### Added
- The Scheduled Task repeats every 5 minutes, so a watcher that dies for any
  reason restarts by itself rather than staying dead until the next logon.
  If Task Scheduler rejects that XML, install falls back to the plain trigger
  rather than leaving no task at all.

## [0.3.1] - 2026-08-02

### Fixed
- Only the first `DCIM` subfolder was ever offloaded. A camera starts a new
  folder every 999 files (`100MEDIA`, `101MEDIA`, …) and goggles accumulate
  several, so everything past the first was silently never uploaded while the
  run still reported success. Copy *and* drone cleanup now cover every media
  folder on the card.

## [0.3.0] - 2026-08-02

### Added
- `dji-auto-upload update` upgrades in place and regenerates the auto-trigger
  from the new templates. It drives `sys.executable -m pip`, so it works on
  machines with several Pythons where a stale `pip.exe` launcher points at an
  interpreter that has since been uninstalled ("Fatal error in launcher:
  Unable to create process").
- `dji-auto-upload uninstall` removes the trigger and, after asking, the config
  and the local staging dir. It refuses to delete staged files that are not in
  the `.uploaded` ledger — even with `--yes` — and lists them instead. Removing
  the rclone remote is *not* covered by `--yes` and needs `--forget-remote`,
  because `rclone config disconnect` revokes the token server-side and would
  break any other tool or backup job that shares that remote.
- Plugging in a drone on Windows now opens a visible console that streams the
  offload as it runs, and announces the outcome with a popup: success
  auto-dismisses after 5 seconds, failure stays on screen with the log path and
  the `diagnose` command. Silent background success looked identical to silent
  background failure, which is the one thing an automation must never do.
- `install-trigger` on Windows starts the watcher immediately after creating
  the Scheduled Task. The logon trigger only armed it at the NEXT sign-in, so a
  fresh install did nothing until the user happened to log out.
- One-command install. `install.ps1` (Windows) and `install.sh` (Linux/macOS)
  install Python and rclone if missing, install the tool, and drop straight
  into the setup wizard — which already ends by offering the auto-trigger.
  The scripts survive being streamed (`irm | iex`, `curl | bash`): the shell
  parses everything before running, and the wizard reads from /dev/tty.
- The Windows watcher no longer depends on PATH: if the `dji-auto-upload`
  console script isn't resolvable (typical for `pip --user` installs), the
  Scheduled Task launches `python -m dji_auto_upload` via the interpreter that
  installed it.
- `dji-auto-upload diagnose` writes a single support bundle (versions, config,
  rclone remotes, trigger state, stage summary, recent logs, and the Windows
  watcher log) so a bug report can be one attachment instead of a back-and-forth.
  The Telegram token and chat ID are redacted; local paths and media filenames
  are included and the file says so.

### Fixed
- Every update after the first silently did nothing. Builds from `main` all
  carry the same version string, so `pip install --upgrade <zip-url>` compared
  0.2.0 against 0.2.0, concluded the requirement was satisfied, and installed
  no code — while printing pages of "Requirement already satisfied" that look
  like success. `update` and both bootstrap scripts now force the package in
  with `--force-reinstall --no-deps` after a normal pass for dependencies.

### Fixed
- A slow rclone remote no longer aborts the run. The reachability probe used a
  10s timeout on `rclone lsd`, which a cold cloud remote regularly exceeds
  (measured: 2 of 10 probes hit the limit, and a first upload batch took 55s),
  and the run then died claiming the credentials were bad. Whether the remote
  exists in rclone.conf is now the only thing that aborts; a slow probe just
  warns and lets rclone report any real error.
- The Windows task XML is escaped, so an `&` in a username or install path no
  longer makes the document malformed and fail `schtasks` with an opaque error.
  Apostrophes in the binary path are escaped for the PowerShell watcher too.

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

[0.5.1]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/szilvasolutions/dji-auto-upload/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/szilvasolutions/dji-auto-upload/releases/tag/v0.1.0
