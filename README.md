# dji-auto-upload

> Plug in your DJI drone. Walk away. Footage shows up in the cloud.

[![CI](https://github.com/szilvasolutions/dji-auto-upload/actions/workflows/ci.yml/badge.svg)](https://github.com/szilvasolutions/dji-auto-upload/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tested on Linux/macOS/Windows](https://img.shields.io/badge/tested-linux%20%7C%20macos%20%7C%20windows-success.svg)](#per-os-installation)

A cross-platform auto-offload pipeline for DJI drones. The moment you connect
the drone over USB, it copies new footage to a local stage, uploads it to your
chosen rclone destination (Google Drive, Photos, Dropbox, S3, anything rclone
speaks), and — if you ask it to — trims old clips off the drone once they're
safely backed up. All without you opening an app.

## Why this exists

I fly a DJI drone. Every time I land I want the footage in the cloud before I
close my laptop, and I want yesterday's clips off the drone so the card never
fills up. The vendor app needs babysitting; the cards need pulling. So I
plugged the drone into my homelab and never thought about it again. This is
that, packaged for everyone.

## Features

- **Plug-and-go on three OSes.** Linux uses udev, macOS uses
  DiskArbitration via a LaunchAgent, Windows uses a Scheduled Task running a
  WMI watcher.
- **Any rclone remote.** Pick during setup. Google Photos, Drive, Dropbox,
  OneDrive, S3, your NAS — all the same code path.
- **Per-recording-date albums.** Files are grouped by their on-card mtime,
  so a single plug-in can produce multiple albums if the card spans dates.
- **Resume-safe.** A `.uploaded` ledger per stage tracks exactly which
  files have been uploaded. Re-running uploads nothing twice; an interrupted
  upload picks up where it left off.
- **Safe by default.** Out of the box it *never* deletes from your drone —
  card-trimming is strictly opt-in, and even then only fires after a confirmed
  upload. Local copies are sentinel-gated too: un-uploaded data is never pruned.
- **Failure visibility.** Optional Telegram notifications for every stage,
  with the last 25 log lines on failure.

## Quick start

```bash
pip install dji-auto-upload
dji-auto-upload setup            # interactive: rclone remote, retention, optional Telegram
dji-auto-upload install-trigger  # OS-specific auto-trigger (sudo on Linux)
```

Plug in your drone. That's it.

## Setting up rclone

dji-auto-upload doesn't lock you into one cloud — it uploads through
[rclone](https://rclone.org), which speaks **Google Drive, Dropbox, OneDrive,
Google Photos, Amazon S3, Backblaze B2, a NAS over SFTP/SMB, and ~70 other
backends**. You pick the one you want; `dji-auto-upload setup` just asks for the
remote's name.

**Install rclone** (once):

| OS | Command |
|---|---|
| macOS | `brew install rclone` |
| Windows | `winget install Rclone.Rclone` |
| Linux | `curl https://rclone.org/install.sh \| sudo bash` |

**Easy path — Drive / Dropbox / OneDrive / S3 / NAS.** Run `rclone config`,
choose "New remote", pick your provider, and follow the prompts. For the big
consumer clouds it's a single browser sign-in — click *Allow* and you're done.
Give the remote a name (e.g. `gdrive`) and use that name in setup. In the
config, a path template like `DJI/{date}` files each day's clips into its own
folder.

<details>
<summary><b>Advanced path — Google Photos (extra step, worth knowing)</b></summary>

Google Photos works, but rclone's shared OAuth client is rate-limited to
**~10 GB/day**. For your own quota you need your own OAuth client:

1. [console.cloud.google.com](https://console.cloud.google.com/) → create a project
2. Enable the **Photos Library API**
3. OAuth consent screen → *External* → **Publish App** (so the token doesn't expire weekly)
4. Credentials → **Desktop app** OAuth client → copy the `client_id` and `client_secret`
5. `rclone config` → new remote → `google photos` → paste those in

Then use a path template of `album/DJI-{date}` so each date becomes its own
album. On a headless box, forward the OAuth callback port over SSH:
`ssh -L 53682:127.0.0.1:53682 user@host`, then open the URL rclone prints.

</details>

Not sure which to choose? **Google Drive is the least-friction option** and the
one most people should pick.

## Per-OS installation

<details>
<summary><b>Linux</b></summary>

Requires `rclone` and `udev` (already on every desktop distro).

```bash
pip install dji-auto-upload
dji-auto-upload setup
sudo dji-auto-upload install-trigger    # writes /etc/udev/rules.d/99-dji-auto-upload.rules
```

The trigger uses `systemd-run` (no `--scope`, with `--collect`) so the
offload runs as a child of PID 1 — escaping `systemd-udevd`'s seccomp filter,
which would otherwise block the `mount(2)` syscall. Logs land in `journalctl`.

</details>

<details>
<summary><b>macOS</b></summary>

Requires `rclone`. Install via Homebrew if needed:

```bash
brew install rclone
pip install dji-auto-upload
dji-auto-upload setup
dji-auto-upload install-trigger    # writes ~/Library/LaunchAgents/com.dji-auto-upload.watcher.plist
```

The trigger registers a per-user LaunchAgent that runs a tiny resident
process subscribed to DiskArbitration disk-appeared events. Watch logs with:

```bash
log stream --predicate 'subsystem == "com.dji-auto-upload"'
```

</details>

<details>
<summary><b>Windows</b></summary>

Install rclone via winget:

```powershell
winget install Rclone.Rclone
pip install dji-auto-upload
dji-auto-upload setup
dji-auto-upload install-trigger
```

The trigger registers a Scheduled Task triggered at logon that runs a
PowerShell `Win32_VolumeChangeEvent` watcher. No admin needed for a per-user
task. Run manually for testing:

```powershell
schtasks /run /tn "DJI Auto Upload Watcher"
```

</details>

## Configuration

`dji-auto-upload setup` writes a TOML config to your platform's config dir:

| OS | Path |
|---|---|
| Linux | `~/.config/dji-auto-upload/config.toml` |
| macOS | `~/Library/Application Support/dji-auto-upload/config.toml` |
| Windows | `%APPDATA%\dji-auto-upload\config.toml` |

Edit it freely; comments survive `dji-auto-upload setup` re-runs.

```toml
[remote]
name = "gphotos"
path_template = "album/DJI-{date}"   # {date} expands to YYYY-MM-DD

[retention]
stage_days = 2     # delete local copies N days after upload (0 = keep forever)
drone_days = 0     # delete drone files older than N days (0 = never touch the drone)

[behaviour]
disk_headroom_mb   = 512
verify_after_copy  = true
delete_drone_files = false   # drone-side deletion is strictly opt-in (set during setup)

[notifier]
enabled = true
events  = ["start", "done_copy", "done_upload", "done", "fail"]
```

Telegram credentials live in a separate `credentials.toml` (chmod 0600).
`dji-auto-upload setup` walks you through getting a bot token from `@BotFather`
and auto-discovers your chat ID by watching for the next message you send to
your bot — no copy-pasting from `getUpdates` URLs.

## CLI

```
dji-auto-upload setup              # interactive setup wizard
dji-auto-upload run [--device P]   # one offload pass (autodetects if --device omitted)
dji-auto-upload install-trigger    # install per-OS auto-trigger
dji-auto-upload uninstall-trigger
dji-auto-upload status             # config summary, rclone reachable, telegram reachable
dji-auto-upload test-notify        # send a test message
dji-auto-upload prune              # manual stage prune
dji-auto-upload version
```

## How it works

```
USB plug-in
    │
    ▼
┌────────────────────────────────────────────────┐
│  Per-OS trigger                                │
│  Linux: udev rule → systemd-run                │
│  macOS: LaunchAgent + DiskArbitration callback │
│  Windows: Scheduled Task + WMI watcher         │
└──────────────┬─────────────────────────────────┘
               │
               ▼
       dji-auto-upload run --device <volume>
               │
   ┌───────────┼───────────┬────────────┬──────────┐
   ▼           ▼           ▼            ▼          ▼
inventory   precheck     copy        upload     cleanup
 (group   (free space  (atomic     (rclone     (ledger-
  by mtime  + headroom)  .part →    --files-     gated
  date)               os.replace)   from)       prune)
                                       │
                                       ▼
                               .uploaded ledger
                                       │
                                       ▼
                              Telegram notification
```

See [`docs/architecture.md`](docs/architecture.md) for the full state machine
and [`docs/why.md`](docs/why.md) for the bash → Python rewrite story.

## Development

```bash
git clone https://github.com/szilvasolutions/dji-auto-upload
cd dji-auto-upload
pip install -e ".[dev]"
pytest                       # unit + integration
ruff check .
mypy src
```

The integration test (`tests/integration/test_offload_pipeline.py`) builds a
synthetic DCIM tree, points the orchestrator at a fake rclone binary
([`tests/fixtures/fake_rclone.py`](tests/fixtures/fake_rclone.py)), and
asserts ledger contents, file placement, and notifier event ordering.

## License

MIT — see [LICENSE](LICENSE).
