# dji-auto-upload

> Plug in your DJI drone. Walk away. Footage shows up in your cloud.

[![CI](https://github.com/szilvasolutions/dji-auto-upload/actions/workflows/ci.yml/badge.svg)](https://github.com/szilvasolutions/dji-auto-upload/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tested on Linux/macOS/Windows](https://img.shields.io/badge/tested-linux%20%7C%20macos%20%7C%20windows-success.svg)](#per-os-installation)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/szilvasolus)

My DJI Neo 2 doesn't have an SD card. Everything it records lives on internal
storage, and there are only two ways to get footage off it: the DJI app, which
does a slow Wi-Fi transfer to my phone that I have to sit and watch, or a USB
cable. Neither is something I want to deal with after a flight.

So I stopped dealing with it. Now I land, walk inside, and plug the drone into
my computer. By the time I've set it down, the new clips are already uploading
to the cloud and the drone's storage is clearing space for next time. I don't
open an app, I don't drag any files, I don't click anything.

That's the whole idea: plug in a DJI device, walk away, footage shows up in your
cloud. It works the same whether your footage sits on internal storage (like the
Neo) or an SD card (like a Mini or a Mavic). Either way the storage never fills
up, so you never have to stop and clear it by hand.

## Which DJI devices work?

If your computer sees the device as a USB drive with a `DCIM` folder on it, this
handles it. That's basically the whole lineup: the drones (Neo, Mini, Air,
Mavic, Avata, FPV), the FPV **Goggles** (their recordings land in the same
`DCIM` tree, and yes, it picks those up too), and the Osmo Action / Pocket
cameras.

Detection doesn't actually require it to be a DJI at all. It looks for DJI's USB
vendor ID and the `DJI` / `DJIMEDIA` volume labels first, but the real backstop
is just "is there a `DCIM` folder here?", so new models tend to work on day one.

One thing to watch: a few devices (some Osmo cameras) ask you to pick a mode when
you plug them in. Choose **mass storage** / **USB drive**, not MTP. An MTP device
doesn't mount as a drive, so nothing that watches for drives can see it.

## What you get

- **Plug and forget, on all three OSes.** Linux watches with udev, macOS with a
  DiskArbitration agent, Windows with a Scheduled Task running a WMI watcher.
  Install it once and you never launch it again.
- **Your cloud, your choice.** Uploads go through rclone, so Google Drive,
  Photos, Dropbox, OneDrive, S3, a NAS, whatever you already use. Setup just
  asks which one.
- **Sorted by the day you shot it.** Clips are grouped by recording date, so a
  single plug-in can fill several folders (or albums) if the footage spans days.
- **You can't upload the same clip twice.** Every batch keeps an `.uploaded`
  ledger. Re-run it, unplug mid-upload, whatever; it resumes exactly where it
  left off and never sends a file again.
- **It won't delete your footage behind your back.** Out of the box it removes
  nothing from the drone. Card-trimming is something you switch on during setup,
  and even then a file is only removed if the upload ledger proves that exact
  file reached your cloud. Telemetry sidecars (`.SRT`, `.LRF`) are only removed
  along with the video they belong to, and if the drone's clock looks wrong the
  cleanup is skipped entirely rather than guessing.
- **Your laptop won't fall asleep on the job.** "Walk away" is exactly when a
  machine decides to idle-sleep and freeze the upload halfway, so a run holds an
  OS sleep inhibitor until it's finished, then releases it. (Closing the lid
  still suspends, on any OS. Nothing is lost when it does; see below.)
- **Try it before you trust it.** `dji-auto-upload run --dry-run` prints exactly
  what it would copy, upload, and delete, and changes nothing.
- **It tells you when it's safe to unplug.** When the run finishes it ejects the
  drone and says so, instead of leaving you guessing mid-copy.
- **It tells you when something breaks.** Optional Telegram messages for each
  stage, with the tail end of the log if a run fails.

## Quick start

```bash
pip install dji-auto-upload
dji-auto-upload setup            # interactive: rclone remote, retention, optional Telegram
dji-auto-upload install-trigger  # OS-specific auto-trigger (sudo on Linux)
```

Plug in your drone. That's it.

## Setting up rclone

dji-auto-upload doesn't lock you into one cloud. It uploads through
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

**Easy path (Drive / Dropbox / OneDrive / S3 / NAS).** Run `rclone config`,
choose "New remote", pick your provider, and follow the prompts. For the big
consumer clouds it's a single browser sign-in. Click *Allow* and you're done.
Give the remote a name (e.g. `gdrive`) and use that name in setup. In the
config, a path template like `DJI/{date}` files each day's clips into its own
folder.

<details>
<summary><b>Advanced path: Google Photos (extra step, worth knowing)</b></summary>

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
offload runs as a child of PID 1, which escapes `systemd-udevd`'s seccomp filter,
which would otherwise block the `mount(2)` syscall. Logs land in `journalctl`.

Run `setup` as your normal user (not with `sudo`) so the config lands in your
home directory. `install-trigger` then pins the udev rule to that config with
`--config` and tells you which file it will use; without that the triggered run
would execute as root, read `/etc/dji-auto-upload/`, and silently fall back to
defaults. It also means rclone uses *your* `rclone.conf`.

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

[detect]
sidecar_extensions = ["srt", "lrf"]  # kept with their video, deleted only with it
strict_detect      = false           # true = only trigger on DJI vendor ID / volume label

[behaviour]
disk_headroom_mb   = 512
verify_after_copy  = true
delete_drone_files = false   # drone-side deletion is strictly opt-in (set during setup)
eject_when_done    = true    # eject the drone when finished, so it's safe to unplug
inhibit_sleep      = true    # keep the machine awake while a run is in progress

[notifier]
enabled = true
events  = ["start", "done_copy", "done_upload", "done", "fail"]
```

Telegram credentials live in a separate `credentials.toml` (chmod 0600).
`dji-auto-upload setup` walks you through getting a bot token from `@BotFather`
and auto-discovers your chat ID by watching for the next message you send to
your bot, so there's no copy-pasting from `getUpdates` URLs.

## CLI

```
dji-auto-upload setup              # interactive setup wizard
dji-auto-upload run [--device P]   # one offload pass (autodetects if --device omitted)
dji-auto-upload run --dry-run      # show the full plan, change nothing
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

### What if the computer sleeps anyway?

While a run is active it holds a sleep inhibitor (`SetThreadExecutionState` on
Windows, `caffeinate` on macOS, `systemd-inhibit` on Linux), so an idle machine
won't suspend mid-transfer. That covers the normal "plug in and walk away" case.

It can't stop you closing the lid or choosing Sleep from the menu. If that
happens mid-upload, nothing is lost and nothing is duplicated: files already
confirmed are in the `.uploaded` ledger, and anything unconfirmed is simply
retried. The drone is never trimmed for a file that isn't confirmed in the
cloud. Replug (or run `dji-auto-upload run`) and it picks up exactly where it
stopped.

Set `inhibit_sleep = false` if you'd rather the machine follow its normal power
policy during a run.

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

## Support this project

This is free and I build it in my spare time. If it saved you the hassle of
dragging files off a drone, or you want to nudge a feature along, you can buy me
a coffee. Bug reports and pull requests are just as welcome.

<a href="https://buymeacoffee.com/szilvasolus" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50" width="210"></a>

## License

MIT. See [LICENSE](LICENSE).
